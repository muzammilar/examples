//! A node: Raft plus an HTTP API for peers and clients.
//!
//! | Route | Purpose |
//! |-------|---------|
//! | `GET /kv/{key}` | Read a key. Linearizable by default; `?stale=true` reads this node's copy. |
//! | `PUT /kv/{key}` | Set a key to the request body. |
//! | `DELETE /kv/{key}` | Remove a key. |
//! | `GET /status` | This node's view of the cluster, as JSON. |
//! | `POST /raft/{append,vote,snapshot}` | Raft RPCs between nodes. |
//!
//! Limitation: plain HTTP, with no TLS or authentication, for peers and
//! clients alike.
//!
//! Only the leader serves writes and linearizable reads. Other nodes answer
//! them with `307 Temporary Redirect` to the leader, which HTTP clients
//! follow, or `503` while no leader is elected.

mod api;
mod writer;

use std::collections::BTreeMap;
use std::io;
use std::path::PathBuf;
use std::sync::Arc;

use openraft::error::{Fatal, InitializeError, RaftError};
use openraft::{BasicNode, SnapshotPolicy};
use tokio::net::TcpListener;
use tokio::sync::oneshot;
use tokio::task::JoinHandle;

pub use api::{Role, Status};
pub use writer::WriteError;

use crate::network::Network;
use crate::storage;
use crate::{Command, CommandResult, NodeId, Raft};

/// How to run a node.
#[derive(Debug, Clone)]
pub struct Config {
    pub id: NodeId,
    /// Every member, this node included, with the `host:port` where peers
    /// and clients reach it.
    ///
    /// Limitation: membership is fixed; there's no way to add or remove a
    /// node from a running cluster.
    pub members: BTreeMap<NodeId, String>,
    /// Where the node keeps its log and snapshots.
    pub data_dir: PathBuf,
    /// Form a new cluster from `members` if this node has never been part
    /// of one. Set it on one node, for the cluster's first start.
    pub bootstrap: bool,
    /// Raft timing and snapshot settings.
    pub raft: openraft::Config,
}

impl Config {
    /// Raft settings suited to a small cluster on a local network.
    ///
    /// `snapshot_every` is how many log entries trigger a new snapshot. After
    /// one, all but `keep_logs` of the entries it covers are purged, so a
    /// follower that has fallen further behind is sent the snapshot instead.
    #[must_use]
    pub fn raft_defaults(snapshot_every: u64, keep_logs: u64) -> openraft::Config {
        openraft::Config {
            cluster_name: "kv-store".into(),
            heartbeat_interval: 100,
            election_timeout_min: 300,
            election_timeout_max: 600,
            snapshot_policy: SnapshotPolicy::LogsSinceLast(snapshot_every),
            max_in_snapshot_log_to_keep: keep_logs,
            // Each entry is a batch of up to `writer::MAX_BYTES`, so this keeps
            // one AppendEntries well under the RPC body limit. A peer that
            // still refuses one gets fewer entries next time; see network.rs.
            max_payload_entries: 16,
            // Chunks travel as JSON, which inflates bytes about fourfold.
            snapshot_max_chunk_size: 1024 * 1024,
            install_snapshot_timeout: 10_000,
            ..openraft::Config::default()
        }
    }
}

/// A running node.
pub struct Node {
    raft: Raft,
    writer: writer::Writer,
    server: JoinHandle<io::Result<()>>,
    stop: oneshot::Sender<()>,
}

/// Why a node failed to start.
#[derive(Debug, thiserror::Error)]
pub enum StartError {
    /// The data directory couldn't be opened.
    #[error("opening storage")]
    Storage(#[source] crate::Error),
    /// The Raft settings are invalid.
    #[error("invalid raft config")]
    Config(#[source] Box<openraft::ConfigError>),
    /// Raft failed to start.
    #[error("starting raft")]
    Raft(#[source] Box<Fatal<NodeId>>),
    /// The cluster couldn't be formed.
    #[error("bootstrapping the cluster")]
    Bootstrap(#[source] Box<RaftError<NodeId, InitializeError<NodeId, BasicNode>>>),
}

impl Node {
    /// Opens the node's storage, starts Raft, and serves on `listener`.
    ///
    /// # Errors
    ///
    /// See [`StartError`].
    pub async fn start(config: Config, listener: TcpListener) -> Result<Self, StartError> {
        let (log, state_machine) = storage::open(&config.data_dir).map_err(StartError::Storage)?;
        let raft_config = Arc::new(
            config
                .raft
                .validate()
                .map_err(|err| StartError::Config(Box::new(err)))?,
        );
        let raft = Raft::new(
            config.id,
            raft_config,
            Network::default(),
            log,
            state_machine.clone(),
        )
        .await
        .map_err(|err| StartError::Raft(Box::new(err)))?;

        if config.bootstrap {
            bootstrap(&raft, &config.members).await?;
        }

        let writer = writer::Writer::spawn(raft.clone());
        let app = Arc::new(api::App {
            raft: raft.clone(),
            writer: writer.clone(),
            state_machine,
        });
        let (stop, stopped) = oneshot::channel();
        let server = tokio::spawn(async move {
            axum::serve(listener, api::routes(app))
                .with_graceful_shutdown(async {
                    let _ = stopped.await;
                })
                .await
        });
        Ok(Self {
            raft,
            writer,
            server,
            stop,
        })
    }

    #[must_use]
    pub fn raft(&self) -> &Raft {
        &self.raft
    }

    /// Applies `command` through this node, batched with any other writes in
    /// flight. Only succeeds on the leader.
    ///
    /// # Errors
    ///
    /// See [`WriteError`].
    pub async fn write(&self, command: Command) -> Result<CommandResult, WriteError> {
        self.writer.write(command).await
    }

    /// Stops Raft, then the HTTP server.
    ///
    /// # Errors
    ///
    /// If the HTTP server failed while running.
    pub async fn shutdown(self) -> io::Result<()> {
        if let Err(err) = self.raft.shutdown().await {
            tracing::warn!("raft did not shut down cleanly: {err}");
        }
        let _ = self.stop.send(());
        self.server.await.map_err(io::Error::other)?
    }
}

async fn bootstrap(raft: &Raft, members: &BTreeMap<NodeId, String>) -> Result<(), StartError> {
    let nodes: BTreeMap<NodeId, BasicNode> = (members.iter())
        .map(|(&id, addr)| (id, BasicNode::new(addr)))
        .collect();
    match raft.initialize(nodes).await {
        Ok(()) => tracing::info!("formed a new cluster"),
        Err(RaftError::APIError(InitializeError::NotAllowed(_))) => {
            tracing::info!("already part of a cluster; not bootstrapping");
        }
        Err(err) => return Err(StartError::Bootstrap(Box::new(err))),
    }
    Ok(())
}
