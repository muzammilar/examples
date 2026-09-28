//! The Raft log, kept in a write-ahead log on disk.
//!
//! Appends are written to the OS and synced to disk by a background task,
//! which covers every append that arrived since its last `fsync` with one
//! more. openraft only counts an entry as durable once its [`LogFlushed`]
//! callback fires after that `fsync`. The vote is synced before `save_vote`
//! returns.
//!
//! Each frame in `wal.log` holds one [`Record`]. After a purge the file is
//! rewritten to start with a `Purged` marker, followed by the entries that
//! remain.
//!
//! Limitation: recovery assumes appends reach the disk in order, so only the
//! last frame can be torn. A power cut can break that: the unsynced tail may
//! come back with a hole and complete frames after it. Replay then reports
//! corruption, even though nothing acknowledged was lost.

use std::collections::BTreeMap;
use std::fmt::Debug;
use std::fs::{self, File, OpenOptions};
use std::io::{self, BufReader, Write};
use std::ops::RangeBounds;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex, MutexGuard};

use openraft::storage::{LogFlushed, LogState, RaftLogStorage};
use openraft::{Entry, LogId, RaftLogReader, StorageError, StorageIOError, Vote};
use serde::{Deserialize, Serialize};
use tokio::sync::mpsc;
use tokio::task;

use super::files;
use super::frame::{self, Decoded};
use crate::error::{Error, Result};
use crate::{NodeId, TypeConfig};

const LOG_FILE: &str = "wal.log";
const VOTE_FILE: &str = "vote";

/// The Raft log for one node. Cheap to clone; clones share the same log.
#[derive(Clone)]
pub struct LogStore {
    wal: Arc<Mutex<Wal>>,
    flusher: mpsc::UnboundedSender<Flush>,
}

/// An append waiting for its `fsync`.
struct Flush {
    file: Arc<File>,
    callback: LogFlushed<TypeConfig>,
}

impl LogStore {
    /// Opens the log in `dir`, creating it if needed, and replays it.
    ///
    /// A torn write at the end of the log, left by a crash mid-append, is
    /// truncated away; the leader will send it again. Any other damage is an
    /// error, and the file is left untouched for inspection. Must be called
    /// inside a Tokio runtime.
    ///
    /// # Errors
    ///
    /// - [`Error::Locked`] if another process has the directory open.
    /// - [`Error::Corrupt`] if the log or vote is damaged.
    /// - [`Error::Io`] if the files can't be read or created.
    pub fn open(dir: &Path) -> Result<Self> {
        let wal = Wal::open(dir)?;
        let (flusher, requests) = mpsc::unbounded_channel();
        tokio::spawn(flush_batches(requests));
        Ok(Self {
            wal: Arc::new(Mutex::new(wal)),
            flusher,
        })
    }

    /// Runs `op` on a thread meant for blocking, since it syncs or rewrites
    /// files and would otherwise stall the async runtime.
    async fn blocking<T, F>(&self, op: F) -> io::Result<T>
    where
        T: Send + 'static,
        F: FnOnce(&mut Wal) -> io::Result<T> + Send + 'static,
    {
        let wal = Arc::clone(&self.wal);
        task::spawn_blocking(move || op(&mut lock(&wal)))
            .await
            .map_err(io::Error::other)?
    }
}

fn lock(wal: &Mutex<Wal>) -> MutexGuard<'_, Wal> {
    wal.lock()
        .expect("a panic mid-write leaves the log in an unknown state")
}

impl RaftLogReader<TypeConfig> for LogStore {
    async fn try_get_log_entries<RB: RangeBounds<u64> + Clone + Debug + Send>(
        &mut self,
        range: RB,
    ) -> Result<Vec<Entry<TypeConfig>>, StorageError<NodeId>> {
        let wal = lock(&self.wal);
        Ok(wal
            .entries
            .range(range)
            .map(|(_, (e, _))| e.clone())
            .collect())
    }
}

impl RaftLogStorage<TypeConfig> for LogStore {
    type LogReader = Self;

    async fn get_log_state(&mut self) -> Result<LogState<TypeConfig>, StorageError<NodeId>> {
        let wal = lock(&self.wal);
        let last = wal.entries.values().next_back().map(|(e, _)| e.log_id);
        Ok(LogState {
            last_purged_log_id: wal.last_purged,
            last_log_id: last.or(wal.last_purged),
        })
    }

    async fn get_log_reader(&mut self) -> Self::LogReader {
        self.clone()
    }

    async fn save_vote(&mut self, vote: &Vote<NodeId>) -> Result<(), StorageError<NodeId>> {
        let vote = *vote;
        self.blocking(move |wal| wal.save_vote(vote))
            .await
            .map_err(|e| StorageIOError::write_vote(&e).into())
    }

    async fn read_vote(&mut self) -> Result<Option<Vote<NodeId>>, StorageError<NodeId>> {
        Ok(lock(&self.wal).vote)
    }

    async fn append<I>(
        &mut self,
        entries: I,
        callback: LogFlushed<TypeConfig>,
    ) -> Result<(), StorageError<NodeId>>
    where
        I: IntoIterator<Item = Entry<TypeConfig>> + Send,
        I::IntoIter: Send,
    {
        let file = {
            let mut wal = lock(&self.wal);
            wal.append(entries)
                .map_err(|e| StorageIOError::write_logs(&e))?;
            Arc::clone(&wal.file)
        };
        if let Err(mpsc::error::SendError(flush)) = self.flusher.send(Flush { file, callback }) {
            flush
                .callback
                .log_io_completed(Err(io::Error::other("log flusher stopped")));
        }
        Ok(())
    }

    async fn truncate(&mut self, log_id: LogId<NodeId>) -> Result<(), StorageError<NodeId>> {
        self.blocking(move |wal| wal.truncate(log_id.index))
            .await
            .map_err(|e| StorageIOError::write_logs(&e).into())
    }

    async fn purge(&mut self, log_id: LogId<NodeId>) -> Result<(), StorageError<NodeId>> {
        self.blocking(move |wal| wal.purge(log_id))
            .await
            .map_err(|e| StorageIOError::write_logs(&e).into())
    }
}

/// Syncs the log in batches. See the [module docs](self).
async fn flush_batches(mut requests: mpsc::UnboundedReceiver<Flush>) {
    while let Some(first) = requests.recv().await {
        let mut batch = vec![first];
        while let Ok(flush) = requests.try_recv() {
            batch.push(flush);
        }

        // Usually one file; two if a purge rewrote the log mid-batch. The
        // rewrite already synced the new file, and syncing the old one as
        // well is harmless.
        let mut files: Vec<Arc<File>> = Vec::new();
        for flush in &batch {
            if !files.iter().any(|f| Arc::ptr_eq(f, &flush.file)) {
                files.push(Arc::clone(&flush.file));
            }
        }
        let result = task::spawn_blocking(move || files.iter().try_for_each(|f| f.sync_data()))
            .await
            .unwrap_or_else(|err| Err(io::Error::other(err)));

        for flush in batch {
            let result = match &result {
                Ok(()) => Ok(()),
                Err(err) => Err(io::Error::new(err.kind(), err.to_string())),
            };
            flush.callback.log_io_completed(result);
        }
    }
}

/// What each frame of `wal.log` holds.
#[derive(Deserialize)]
enum Record {
    Entry(Entry<TypeConfig>),
    /// Everything up to and including this log ID was purged.
    Purged(LogId<NodeId>),
}

/// [`Record`], borrowed for writing.
#[derive(Serialize)]
enum RecordRef<'a> {
    Entry(&'a Entry<TypeConfig>),
    Purged(LogId<NodeId>),
}

impl RecordRef<'_> {
    fn to_frame(&self) -> io::Result<Vec<u8>> {
        frame::encode(&serde_json::to_vec(self).expect("log records always serialize"))
    }
}

/// The log file and its in-memory index. All I/O here is synchronous.
struct Wal {
    dir: PathBuf,
    file: Arc<File>,
    /// Length of the valid part of the file.
    len: u64,
    /// Entries by index, each with the byte offset where its frame starts, so
    /// truncation knows where to cut.
    ///
    /// Limitation: the whole log is kept in memory as well as on disk.
    entries: BTreeMap<u64, (Entry<TypeConfig>, u64)>,
    last_purged: Option<LogId<NodeId>>,
    vote: Option<Vote<NodeId>>,
    /// Held for the log's lifetime; released when the file closes.
    _lock: File,
}

impl Wal {
    fn open(dir: &Path) -> Result<Self> {
        fs::create_dir_all(dir)?;
        files::sync_parent(dir)?;
        let lock = files::lock(dir)?;

        let vote = match files::read_atomic(&dir.join(VOTE_FILE), VOTE_FILE)?.as_deref() {
            Some([json]) => Some(serde_json::from_slice(json).map_err(|e| {
                tracing::error!("vote is corrupt: {e}");
                Error::Corrupt {
                    file: VOTE_FILE,
                    offset: 0,
                }
            })?),
            Some(_) => {
                return Err(Error::Corrupt {
                    file: VOTE_FILE,
                    offset: 0,
                });
            }
            None => None,
        };

        let path = dir.join(LOG_FILE);
        let file = OpenOptions::new()
            .create(true)
            .read(true)
            .append(true)
            .open(&path)?;
        files::sync_parent(&path)?;

        let mut wal = Self {
            dir: dir.to_owned(),
            file: Arc::new(file),
            len: 0,
            entries: BTreeMap::new(),
            last_purged: None,
            vote,
            _lock: lock,
        };
        wal.replay()?;
        Ok(wal)
    }

    fn replay(&mut self) -> Result<()> {
        let file_len = self.file.metadata()?.len();
        let mut reader = BufReader::new(&*self.file);
        let mut buf = Vec::new();
        loop {
            let offset = self.len;
            let corrupt = Error::Corrupt {
                file: LOG_FILE,
                offset,
            };
            match frame::decode(&mut reader, &mut buf)? {
                Decoded::Frame { payload, len } => {
                    match serde_json::from_slice(payload) {
                        Ok(Record::Purged(id))
                            if self.entries.is_empty() && self.last_purged.is_none() =>
                        {
                            self.last_purged = Some(id);
                        }
                        Ok(Record::Entry(entry))
                            if self
                                .next_index()
                                .is_none_or(|next| entry.log_id.index >= next) =>
                        {
                            self.entries.insert(entry.log_id.index, (entry, offset));
                        }
                        _ => return Err(corrupt),
                    }
                    self.len += len;
                }
                Decoded::End => return Ok(()),
                Decoded::Corrupt => return Err(corrupt),
                Decoded::Invalid { len } => {
                    // Appends are sequential, so a torn frame can only be the
                    // last one. A bad frame with data after it is damage.
                    if len.is_some_and(|len| offset + len < file_len) {
                        return Err(corrupt);
                    }
                    self.file.set_len(offset)?;
                    self.file.sync_all()?;
                    return Ok(());
                }
            }
        }
    }

    /// Smallest index the next entry in the file may have, or `None` if the
    /// log is empty and has never been purged.
    ///
    /// openraft never leaves gaps in a real log, but its storage test suite
    /// builds sparse ones. A gap is stored as given.
    fn next_index(&self) -> Option<u64> {
        match (self.entries.keys().next_back(), self.last_purged) {
            (Some(last), _) => Some(last + 1),
            (None, Some(purged)) => Some(purged.index + 1),
            (None, None) => None,
        }
    }

    fn append(&mut self, entries: impl IntoIterator<Item = Entry<TypeConfig>>) -> io::Result<()> {
        let entries: Vec<_> = entries.into_iter().collect();
        let Some(first) = entries.first().map(|e| e.log_id.index) else {
            return Ok(());
        };
        // Replay expects indexes to rise through the file.
        let rising = entries
            .windows(2)
            .all(|w| w[0].log_id.index < w[1].log_id.index);
        if !rising {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "appended entries must be in index order",
            ));
        }
        // Rewriting indexes we already have replaces them and everything
        // after, as Raft does with a conflicting suffix.
        if self.entries.range(first..).next().is_some() {
            self.truncate(first)?;
        }

        let mut batch = Vec::new();
        let mut offsets = Vec::with_capacity(entries.len());
        for entry in &entries {
            offsets.push(self.len + batch.len() as u64);
            batch.extend(RecordRef::Entry(entry).to_frame()?);
        }
        // No fsync here: the flusher does it. If this write fails partway,
        // openraft stops the node and replay drops the torn frame.
        (&*self.file).write_all(&batch)?;
        self.len += batch.len() as u64;
        for (entry, offset) in entries.into_iter().zip(offsets) {
            self.entries.insert(entry.log_id.index, (entry, offset));
        }
        Ok(())
    }

    /// Removes every entry from `index` onward.
    fn truncate(&mut self, index: u64) -> io::Result<()> {
        let Some(&(_, offset)) = self.entries.get(&index) else {
            return Ok(());
        };
        self.file.set_len(offset)?;
        self.file.sync_data()?;
        self.entries.split_off(&index);
        self.len = offset;
        Ok(())
    }

    /// Drops every entry up to and including `upto`, by rewriting the file
    /// without them.
    ///
    /// Limitation: this rewrites every entry that's kept. A real log would
    /// split into segment files and delete whole segments instead.
    fn purge(&mut self, upto: LogId<NodeId>) -> io::Result<()> {
        if self
            .last_purged
            .is_some_and(|purged| purged.index >= upto.index)
        {
            return Ok(());
        }
        let keep_from = upto.index + 1;

        let mut contents = RecordRef::Purged(upto).to_frame()?;
        let mut offsets = Vec::new();
        for (&index, (entry, _)) in self.entries.range(keep_from..) {
            offsets.push((index, contents.len() as u64));
            contents.extend(RecordRef::Entry(entry).to_frame()?);
        }

        // Written beside the old log and renamed over it: a crash leaves one
        // complete log or the other.
        let path = self.dir.join(LOG_FILE);
        let tmp = path.with_extension("tmp");
        let mut file = File::create(&tmp)?;
        file.write_all(&contents)?;
        file.sync_all()?;
        fs::rename(&tmp, &path)?;
        files::sync_parent(&path)?;
        let file = OpenOptions::new().read(true).append(true).open(&path)?;

        // Update the index only once the new file is in place. A failure
        // above leaves the old file and index consistent.
        self.entries = self.entries.split_off(&keep_from);
        for (index, offset) in offsets {
            if let Some((_, old)) = self.entries.get_mut(&index) {
                *old = offset;
            }
        }
        self.last_purged = Some(upto);
        self.file = Arc::new(file);
        self.len = contents.len() as u64;
        Ok(())
    }

    fn save_vote(&mut self, vote: Vote<NodeId>) -> io::Result<()> {
        let json = serde_json::to_vec(&vote).expect("votes always serialize");
        files::write_atomic(&self.dir.join(VOTE_FILE), &[&json])?;
        self.vote = Some(vote);
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use openraft::testing::{blank_ent, log_id};
    use tempfile::TempDir;

    use super::*;

    fn entries(range: std::ops::Range<u64>) -> Vec<Entry<TypeConfig>> {
        range.map(|i| blank_ent::<TypeConfig>(1, 1, i)).collect()
    }

    fn indexes(wal: &Wal) -> Vec<u64> {
        wal.entries.keys().copied().collect()
    }

    fn log_len(dir: &TempDir) -> u64 {
        fs::metadata(dir.path().join(LOG_FILE)).unwrap().len()
    }

    fn append_raw(dir: &TempDir, bytes: &[u8]) {
        let mut log = OpenOptions::new()
            .create(true)
            .append(true)
            .open(dir.path().join(LOG_FILE))
            .unwrap();
        log.write_all(bytes).unwrap();
    }

    #[test]
    fn entries_and_vote_survive_reopen() {
        let dir = TempDir::new().unwrap();
        let vote = Vote::new(3, 2);
        {
            let mut wal = Wal::open(dir.path()).unwrap();
            wal.append(entries(0..5)).unwrap();
            wal.save_vote(vote).unwrap();
        }

        let wal = Wal::open(dir.path()).unwrap();
        assert_eq!(indexes(&wal), [0, 1, 2, 3, 4]);
        assert_eq!(wal.vote, Some(vote));
    }

    #[test]
    fn truncation_survives_reopen_and_appends_continue_after_it() {
        let dir = TempDir::new().unwrap();
        {
            let mut wal = Wal::open(dir.path()).unwrap();
            wal.append(entries(0..5)).unwrap();
            wal.truncate(3).unwrap();
            wal.append(entries(3..4)).unwrap();
        }
        assert_eq!(indexes(&Wal::open(dir.path()).unwrap()), [0, 1, 2, 3]);
    }

    #[test]
    fn purge_survives_reopen() {
        let dir = TempDir::new().unwrap();
        {
            let mut wal = Wal::open(dir.path()).unwrap();
            wal.append(entries(0..10)).unwrap();
            wal.purge(log_id(1, 1, 6)).unwrap();
            wal.append(entries(10..12)).unwrap();
        }

        let mut wal = Wal::open(dir.path()).unwrap();
        assert_eq!(wal.last_purged, Some(log_id(1, 1, 6)));
        assert_eq!(indexes(&wal), [7, 8, 9, 10, 11]);

        // Offsets were rebuilt by the rewrite, so truncation still works.
        wal.truncate(9).unwrap();
        drop(wal);
        assert_eq!(indexes(&Wal::open(dir.path()).unwrap()), [7, 8]);
    }

    #[test]
    fn purging_everything_leaves_the_marker() {
        let dir = TempDir::new().unwrap();
        {
            let mut wal = Wal::open(dir.path()).unwrap();
            wal.append(entries(0..3)).unwrap();
            wal.purge(log_id(1, 1, 2)).unwrap();
        }
        let wal = Wal::open(dir.path()).unwrap();
        assert_eq!(wal.next_index(), Some(3));
        assert!(wal.entries.is_empty());
    }

    #[test]
    fn torn_tail_is_truncated() {
        let dir = TempDir::new().unwrap();
        Wal::open(dir.path())
            .unwrap()
            .append(entries(0..3))
            .unwrap();
        let good_len = log_len(&dir);
        // A crash partway through appending the next entry.
        let next = RecordRef::Entry(&blank_ent::<TypeConfig>(1, 1, 3))
            .to_frame()
            .unwrap();
        append_raw(&dir, &next[..next.len() - 5]);

        let mut wal = Wal::open(dir.path()).unwrap();
        assert_eq!(indexes(&wal), [0, 1, 2]);
        assert_eq!(log_len(&dir), good_len);
        wal.append(entries(3..4)).unwrap();
        drop(wal);
        assert_eq!(indexes(&Wal::open(dir.path()).unwrap()), [0, 1, 2, 3]);
    }

    #[test]
    fn damaged_length_at_the_tail_is_corruption_not_a_torn_write() {
        let dir = TempDir::new().unwrap();
        let last_start = {
            let mut wal = Wal::open(dir.path()).unwrap();
            wal.append(entries(0..2)).unwrap();
            let last_start = wal.len;
            wal.append(entries(2..3)).unwrap();
            last_start
        };
        let path = dir.path().join(LOG_FILE);
        let mut bytes = fs::read(&path).unwrap();
        // Make the last frame claim to run past the end of the file.
        bytes[usize::try_from(last_start).unwrap() + 1] ^= 0x10;
        fs::write(&path, &bytes).unwrap();

        assert!(matches!(
            Wal::open(dir.path()),
            Err(Error::Corrupt { offset, .. }) if offset == last_start
        ));
    }

    #[test]
    fn appends_must_be_in_index_order() {
        let dir = TempDir::new().unwrap();
        let mut wal = Wal::open(dir.path()).unwrap();
        let backwards = [3, 2].map(|i| blank_ent::<TypeConfig>(1, 1, i));
        let err = wal.append(backwards).unwrap_err();
        assert_eq!(err.kind(), io::ErrorKind::InvalidInput);
        assert!(wal.entries.is_empty());
    }

    #[test]
    fn appending_over_existing_entries_replaces_the_suffix() {
        let dir = TempDir::new().unwrap();
        {
            let mut wal = Wal::open(dir.path()).unwrap();
            wal.append(entries(0..5)).unwrap();
            let replacement = (2..4).map(|i| blank_ent::<TypeConfig>(2, 1, i));
            wal.append(replacement).unwrap();
        }
        let wal = Wal::open(dir.path()).unwrap();
        assert_eq!(indexes(&wal), [0, 1, 2, 3]);
        assert_eq!(wal.entries[&3].0.log_id.leader_id.term, 2);
    }

    #[test]
    fn damage_before_the_tail_is_reported_not_truncated() {
        let dir = TempDir::new().unwrap();
        let first_end = {
            let mut wal = Wal::open(dir.path()).unwrap();
            wal.append(entries(0..1)).unwrap();
            let first_end = wal.len;
            wal.append(entries(1..3)).unwrap();
            first_end
        };
        let path = dir.path().join(LOG_FILE);
        let mut bytes = fs::read(&path).unwrap();
        bytes[usize::try_from(first_end).unwrap() + 10] ^= 0xff;
        fs::write(&path, &bytes).unwrap();

        assert!(matches!(
            Wal::open(dir.path()),
            Err(Error::Corrupt { offset, .. }) if offset == first_end
        ));
        assert_eq!(fs::read(&path).unwrap(), bytes, "left as is for inspection");
    }

    #[test]
    fn indexes_going_backwards_are_corruption() {
        let dir = TempDir::new().unwrap();
        for index in [5, 3] {
            let entry = blank_ent::<TypeConfig>(1, 1, index);
            append_raw(&dir, &RecordRef::Entry(&entry).to_frame().unwrap());
        }
        assert!(matches!(Wal::open(dir.path()), Err(Error::Corrupt { .. })));
    }

    #[test]
    fn gaps_are_stored_as_given() {
        let dir = TempDir::new().unwrap();
        {
            let mut wal = Wal::open(dir.path()).unwrap();
            wal.append(entries(0..2)).unwrap();
            wal.append(entries(5..6)).unwrap();
        }
        assert_eq!(indexes(&Wal::open(dir.path()).unwrap()), [0, 1, 5]);
    }

    #[test]
    fn an_empty_log_may_start_at_any_index() {
        let dir = TempDir::new().unwrap();
        Wal::open(dir.path())
            .unwrap()
            .append(entries(7..9))
            .unwrap();
        assert_eq!(indexes(&Wal::open(dir.path()).unwrap()), [7, 8]);
    }

    #[test]
    fn second_open_is_locked_out() {
        let dir = TempDir::new().unwrap();
        let _first = Wal::open(dir.path()).unwrap();
        assert!(matches!(Wal::open(dir.path()), Err(Error::Locked)));
    }
}
