//! Where a node keeps its data.
//!
//! A data directory holds:
//!
//! - `wal.log`: the Raft log, one checksummed frame per entry. See
//!   [`LogStore`].
//! - `vote`: the node's current term and vote.
//! - `snapshot`: the latest snapshot of the key-value data. See
//!   [`StateMachine`].
//! - `LOCK`: held while the directory is open; two nodes can't share it.

mod files;
mod frame;
mod log_store;
mod state_machine;

use std::path::Path;

pub use log_store::LogStore;
pub use state_machine::StateMachine;

use crate::error::Result;

/// Opens both halves of a node's storage in `dir`, creating it if needed.
/// Must be called inside a Tokio runtime.
///
/// # Errors
///
/// See [`LogStore::open`] and [`StateMachine::open`].
pub fn open(dir: &Path) -> Result<(LogStore, StateMachine)> {
    let log = LogStore::open(dir)?;
    let state_machine = StateMachine::open(dir)?;
    Ok((log, state_machine))
}
