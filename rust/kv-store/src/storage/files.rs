//! Small-file helpers shared by the log and the state machine.

use std::fs::{self, File, TryLockError};
use std::io::{self, BufWriter, Write};
use std::path::Path;

use super::frame::{self, Decoded};
use crate::error::{Error, Result};

/// Takes the directory's `LOCK` file, held until the returned file closes.
pub(super) fn lock(dir: &Path) -> Result<File> {
    let lock = File::create(dir.join("LOCK"))?;
    lock.try_lock().map_err(|err| match err {
        TryLockError::WouldBlock => Error::Locked,
        TryLockError::Error(err) => Error::Io(err),
    })?;
    Ok(lock)
}

/// Replaces `path` atomically with `payloads`, one checksummed frame each:
/// write a temp file, sync it, rename it over the old one, then sync the
/// directory so the rename sticks.
pub(super) fn write_atomic(path: &Path, payloads: &[&[u8]]) -> io::Result<()> {
    let tmp = path.with_extension("tmp");
    let mut file = BufWriter::new(File::create(&tmp)?);
    for payload in payloads {
        file.write_all(&frame::header(payload)?)?;
        file.write_all(payload)?;
    }
    file.into_inner()
        .map_err(io::IntoInnerError::into_error)?
        .sync_all()?;
    fs::rename(&tmp, path)?;
    sync_parent(path)
}

/// Reads the payloads of a file written by [`write_atomic`], or `None` if it
/// doesn't exist.
pub(super) fn read_atomic(path: &Path, name: &'static str) -> Result<Option<Vec<Vec<u8>>>> {
    let bytes = match fs::read(path) {
        Ok(bytes) => bytes,
        Err(err) if err.kind() == io::ErrorKind::NotFound => return Ok(None),
        Err(err) => return Err(err.into()),
    };
    let mut reader = &bytes[..];
    let mut payloads = Vec::new();
    loop {
        let offset = (bytes.len() - reader.len()) as u64;
        let mut payload = Vec::new();
        match frame::decode(&mut reader, &mut payload)? {
            Decoded::Frame { .. } => payloads.push(payload),
            Decoded::End => return Ok(Some(payloads)),
            // The rename made the write atomic: any bad frame is damage.
            Decoded::Invalid { .. } | Decoded::Corrupt => {
                return Err(Error::Corrupt { file: name, offset });
            }
        }
    }
}

/// Makes creating or renaming `path` durable. Without this, a crash can undo
/// the directory change even though the file's data was synced.
#[cfg(unix)]
pub(super) fn sync_parent(path: &Path) -> io::Result<()> {
    let parent = match path.parent() {
        Some(parent) if !parent.as_os_str().is_empty() => parent,
        _ => Path::new("."),
    };
    File::open(parent)?.sync_all()
}

/// Directories can't be opened for syncing this way outside Unix.
#[cfg(not(unix))]
pub(super) fn sync_parent(_path: &Path) -> io::Result<()> {
    Ok(())
}
