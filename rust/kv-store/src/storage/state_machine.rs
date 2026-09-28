//! Applies committed commands to an in-memory map, and snapshots it to disk.
//!
//! The map itself isn't persisted. On restart it is rebuilt from the last
//! snapshot, and openraft replays the committed log entries after it. The
//! snapshot is what lets the log be purged, and what the leader sends a node
//! that has fallen too far behind, or lost its data altogether.
//!
//! The `snapshot` file holds two frames: the snapshot's metadata as JSON,
//! then the map as JSON, which is exactly what is sent to other nodes.

use std::collections::BTreeMap;
use std::io::Cursor;
use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::sync::atomic::{AtomicU64, Ordering};

use openraft::storage::{RaftStateMachine, Snapshot};
use openraft::{
    BasicNode, Entry, EntryPayload, LogId, RaftSnapshotBuilder, SnapshotMeta, StorageError,
    StorageIOError, StoredMembership,
};
use tokio::sync::{Mutex, RwLock};
use tokio::task;

use super::files;
use crate::error::{Error, Result};
use crate::{BatchResult, Command, CommandResult, NodeId, TypeConfig};

const SNAPSHOT_FILE: &str = "snapshot";

type Meta = SnapshotMeta<NodeId, BasicNode>;

/// The key-value data for one node. Cheap to clone; clones share the data.
#[derive(Debug, Clone)]
pub struct StateMachine {
    inner: Arc<RwLock<Inner>>,
    dir: Arc<PathBuf>,
    /// Makes snapshot IDs unique on this node.
    snapshots_built: Arc<AtomicU64>,
    /// openraft builds snapshots in a separate task, so a build can finish
    /// while a newer snapshot is being installed. This lock orders the two,
    /// and keeps them from writing the snapshot file at the same time.
    saving: Arc<Mutex<()>>,
}

#[derive(Debug, Default)]
struct Inner {
    applied: Option<LogId<NodeId>>,
    membership: StoredMembership<NodeId, BasicNode>,
    /// Limitation: every key and value is held in memory, and each snapshot
    /// is a full copy of this map.
    data: BTreeMap<String, String>,
    snapshot: Option<(Meta, Vec<u8>)>,
}

impl StateMachine {
    /// Loads the last snapshot from `dir`, if there is one.
    ///
    /// # Errors
    ///
    /// [`Error::Corrupt`] if the snapshot is damaged, or [`Error::Io`] if it
    /// can't be read.
    pub fn open(dir: &Path) -> Result<Self> {
        let mut inner = Inner::default();
        if let Some(frames) = files::read_atomic(&dir.join(SNAPSHOT_FILE), SNAPSHOT_FILE)? {
            let (meta, data) = parse_snapshot(frames)?;
            inner.data = serde_json::from_slice(&data).map_err(corrupt)?;
            inner.applied = meta.last_log_id;
            inner.membership = meta.last_membership.clone();
            inner.snapshot = Some((meta, data));
        }
        Ok(Self {
            inner: Arc::new(RwLock::new(inner)),
            dir: Arc::new(dir.to_owned()),
            snapshots_built: Arc::default(),
            saving: Arc::default(),
        })
    }

    /// The value stored under `key` on this node. Only as fresh as this node
    /// is; see [`Raft::ensure_linearizable`](openraft::Raft::ensure_linearizable).
    pub async fn get(&self, key: &str) -> Option<String> {
        self.inner.read().await.data.get(key).cloned()
    }

    /// Number of keys on this node.
    pub(crate) async fn len(&self) -> usize {
        self.inner.read().await.data.len()
    }

    /// Writes a snapshot to disk on a blocking thread, handing its data back.
    #[expect(clippy::result_large_err, reason = "openraft's error types are large")]
    async fn save_snapshot(
        &self,
        meta: &Meta,
        data: Vec<u8>,
    ) -> Result<Vec<u8>, StorageError<NodeId>> {
        let meta_json = serde_json::to_vec(meta).expect("snapshot metadata always serializes");
        let path = self.dir.join(SNAPSHOT_FILE);
        let (result, data) = task::spawn_blocking(move || {
            let result = files::write_atomic(&path, &[&meta_json, &data]);
            (result, data)
        })
        .await
        .map_err(|e| StorageIOError::write_snapshot(Some(meta.signature()), &e))?;
        result.map_err(|e| StorageIOError::write_snapshot(Some(meta.signature()), &e))?;
        Ok(data)
    }
}

fn parse_snapshot(frames: Vec<Vec<u8>>) -> Result<(Meta, Vec<u8>)> {
    let Ok([meta, data]) = <[Vec<u8>; 2]>::try_from(frames) else {
        return Err(corrupt("expected a metadata frame and a data frame"));
    };
    Ok((serde_json::from_slice(&meta).map_err(corrupt)?, data))
}

/// Logs why the snapshot is unreadable, since [`Error::Corrupt`] can't carry it.
fn corrupt(reason: impl std::fmt::Display) -> Error {
    tracing::error!("snapshot is corrupt: {reason}");
    Error::Corrupt {
        file: SNAPSHOT_FILE,
        offset: 0,
    }
}

impl RaftSnapshotBuilder<TypeConfig> for StateMachine {
    async fn build_snapshot(&mut self) -> Result<Snapshot<TypeConfig>, StorageError<NodeId>> {
        // A read lock is enough to copy the state out. Writes carry on while
        // the snapshot is saved.
        let (meta, data) = {
            let inner = self.inner.read().await;
            let n = self.snapshots_built.fetch_add(1, Ordering::Relaxed) + 1;
            let snapshot_id = match inner.applied {
                Some(last) => format!("{}-{}-{n}", last.leader_id, last.index),
                None => format!("--{n}"),
            };
            let meta = Meta {
                last_log_id: inner.applied,
                last_membership: inner.membership.clone(),
                snapshot_id,
            };
            let data = serde_json::to_vec(&inner.data).expect("string maps always serialize");
            (meta, data)
        };

        let _saving = self.saving.lock().await;
        // If a newer snapshot was installed meanwhile, keep that one: the log
        // before it may already be purged.
        if let Some((current, current_data)) = &self.inner.read().await.snapshot
            && current.last_log_id >= meta.last_log_id
        {
            return Ok(Snapshot {
                meta: current.clone(),
                snapshot: Box::new(Cursor::new(current_data.clone())),
            });
        }
        let data = self.save_snapshot(&meta, data).await?;
        self.inner.write().await.snapshot = Some((meta.clone(), data.clone()));
        Ok(Snapshot {
            meta,
            snapshot: Box::new(Cursor::new(data)),
        })
    }
}

impl RaftStateMachine<TypeConfig> for StateMachine {
    type SnapshotBuilder = Self;

    async fn applied_state(
        &mut self,
    ) -> Result<(Option<LogId<NodeId>>, StoredMembership<NodeId, BasicNode>), StorageError<NodeId>>
    {
        let inner = self.inner.read().await;
        Ok((inner.applied, inner.membership.clone()))
    }

    async fn apply<I>(&mut self, entries: I) -> Result<Vec<BatchResult>, StorageError<NodeId>>
    where
        I: IntoIterator<Item = Entry<TypeConfig>> + Send,
        I::IntoIter: Send,
    {
        let mut inner = self.inner.write().await;
        let mut results = Vec::new();
        for entry in entries {
            inner.applied = Some(entry.log_id);
            let result = match entry.payload {
                EntryPayload::Blank => BatchResult::default(),
                EntryPayload::Normal(batch) => BatchResult {
                    results: (batch.commands.into_iter())
                        .map(|command| inner.apply(command))
                        .collect(),
                },
                EntryPayload::Membership(membership) => {
                    inner.membership = StoredMembership::new(Some(entry.log_id), membership);
                    BatchResult::default()
                }
            };
            results.push(result);
        }
        Ok(results)
    }

    async fn get_snapshot_builder(&mut self) -> Self::SnapshotBuilder {
        self.clone()
    }

    async fn begin_receiving_snapshot(
        &mut self,
    ) -> Result<Box<Cursor<Vec<u8>>>, StorageError<NodeId>> {
        Ok(Box::new(Cursor::new(Vec::new())))
    }

    async fn install_snapshot(
        &mut self,
        meta: &Meta,
        snapshot: Box<Cursor<Vec<u8>>>,
    ) -> Result<(), StorageError<NodeId>> {
        let data = snapshot.into_inner();
        let map = serde_json::from_slice(&data)
            .map_err(|e| StorageIOError::read_snapshot(Some(meta.signature()), &e))?;

        let _saving = self.saving.lock().await;
        let data = self.save_snapshot(meta, data).await?;
        let mut inner = self.inner.write().await;
        inner.data = map;
        inner.applied = meta.last_log_id;
        inner.membership = meta.last_membership.clone();
        inner.snapshot = Some((meta.clone(), data));
        Ok(())
    }

    async fn get_current_snapshot(
        &mut self,
    ) -> Result<Option<Snapshot<TypeConfig>>, StorageError<NodeId>> {
        let inner = self.inner.read().await;
        Ok(inner.snapshot.as_ref().map(|(meta, data)| Snapshot {
            meta: meta.clone(),
            snapshot: Box::new(Cursor::new(data.clone())),
        }))
    }
}

impl Inner {
    fn apply(&mut self, command: Command) -> CommandResult {
        let previous = match command {
            Command::Put { key, value } => self.data.insert(key, value),
            Command::Delete { key } => self.data.remove(&key),
        };
        CommandResult { previous }
    }
}
