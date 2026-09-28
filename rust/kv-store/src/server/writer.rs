//! Groups concurrent writes into one log entry.
//!
//! openraft 0.9 waits for each log entry to be synced before it takes the
//! next write, so one entry per write means one `fsync` per write. Instead,
//! writes queue here while the previous batch is in flight, and the next
//! batch takes everything that queued: one entry, one `fsync`, and one round
//! of replication, however many writes it carries.

use std::time::Duration;

use openraft::BasicNode;
use openraft::error::{ClientWriteError, ForwardToLeader, RaftError};
use tokio::sync::{mpsc, oneshot};
use tokio::time;

use crate::{Batch, Command, CommandResult, NodeId, Raft};

/// Most writes in one batch.
const MAX_WRITES: usize = 512;
/// Most key and value bytes in one batch.
const MAX_BYTES: usize = 512 * 1024;
/// Writes waiting for a batch. When it's full, new writes get a `503`.
const QUEUE_LEN: usize = 16 * 1024;
/// How long a batch may wait to commit, e.g. while there's no majority.
const COMMIT_TIMEOUT: Duration = Duration::from_secs(10);

/// Why a write failed.
#[derive(Debug, Clone, thiserror::Error)]
pub enum WriteError {
    /// This node isn't the leader.
    #[error("not the leader")]
    NotLeader(ForwardToLeader<NodeId, BasicNode>),
    /// Raft couldn't commit the write, for example because it is shutting
    /// down or has lost its majority.
    #[error("write failed: {0}")]
    Failed(String),
}

type Reply = oneshot::Sender<Result<CommandResult, WriteError>>;

/// A handle for submitting writes. Cheap to clone.
#[derive(Debug, Clone)]
pub(super) struct Writer {
    queue: mpsc::Sender<(Command, Reply)>,
}

impl Writer {
    pub(super) fn spawn(raft: Raft) -> Self {
        let (queue, pending) = mpsc::channel(QUEUE_LEN);
        tokio::spawn(write_batches(raft, pending));
        Self { queue }
    }

    pub(super) async fn write(&self, command: Command) -> Result<CommandResult, WriteError> {
        let (reply, result) = oneshot::channel();
        self.queue
            .try_send((command, reply))
            .map_err(|err| match err {
                mpsc::error::TrySendError::Full(_) => {
                    WriteError::Failed("too many writes queued".into())
                }
                mpsc::error::TrySendError::Closed(_) => stopped(),
            })?;
        result.await.map_err(|_| stopped())?
    }
}

fn stopped() -> WriteError {
    WriteError::Failed("node is shutting down".into())
}

async fn write_batches(raft: Raft, mut pending: mpsc::Receiver<(Command, Reply)>) {
    while let Some(first) = pending.recv().await {
        let mut batch = vec![first];
        let mut bytes = command_bytes(&batch[0].0);
        while batch.len() < MAX_WRITES && bytes < MAX_BYTES {
            let Ok(write) = pending.try_recv() else {
                break;
            };
            bytes += command_bytes(&write.0);
            batch.push(write);
        }
        // A caller that gave up, e.g. an HTTP client that disconnected, has
        // probably retried elsewhere; writing its value now could overwrite
        // a newer one.
        batch.retain(|(_, reply)| !reply.is_closed());
        if batch.is_empty() {
            continue;
        }

        let (commands, replies): (Vec<_>, Vec<_>) = batch.into_iter().unzip();
        let result = time::timeout(COMMIT_TIMEOUT, raft.client_write(Batch { commands })).await;
        let err = match result {
            Ok(Ok(response)) => {
                for (reply, result) in replies.into_iter().zip(response.data.results) {
                    let _ = reply.send(Ok(result));
                }
                continue;
            }
            Ok(Err(RaftError::APIError(ClientWriteError::ForwardToLeader(to)))) => {
                WriteError::NotLeader(to)
            }
            Ok(Err(other)) => WriteError::Failed(other.to_string()),
            // Limitation: the entry is in the log and may still commit later;
            // the caller only learns that it wasn't confirmed in time.
            Err(_) => WriteError::Failed("timed out waiting for a majority".into()),
        };
        for reply in replies {
            let _ = reply.send(Err(err.clone()));
        }
    }
}

fn command_bytes(command: &Command) -> usize {
    match command {
        Command::Put { key, value } => key.len() + value.len(),
        Command::Delete { key } => key.len(),
    }
}
