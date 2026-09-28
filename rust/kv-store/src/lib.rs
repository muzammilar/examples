//! A replicated key-value store.
//!
//! Every node keeps its data in a write-ahead log and agrees with its peers
//! on what goes into that log using [openraft](https://docs.rs/openraft).
//! A write is acknowledged once a majority of nodes have it on disk, so the
//! cluster survives losing any minority of its nodes. A node that comes back
//! with a stale log is caught up by the leader, and one that comes back with
//! no data at all is rebuilt from the leader's snapshot plus its log.
//!
//! - [`storage`]: the write-ahead log, state machine and snapshots.
//! - [`server`]: a node, serving both Raft and clients over HTTP.
//! - [`client`]: a client that finds the leader for you.
//!
//! A proof of concept; see the README for known limitations.

#![expect(
    clippy::unused_async_trait_impl,
    reason = "openraft's traits are async; some of our implementations never await"
)]

pub mod client;
mod error;
mod network;
pub mod server;
pub mod storage;

// The macro's default snapshot type names `Cursor` unqualified.
use std::io::Cursor;

use serde::{Deserialize, Serialize};

pub use error::{Chain, Error, Result};

/// Identifies a node in the cluster.
pub type NodeId = u64;

/// A change to the store. These are what the Raft log holds.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "op", rename_all = "snake_case")]
pub enum Command {
    /// Set `key` to `value`.
    Put { key: String, value: String },
    /// Remove `key`.
    Delete { key: String },
}

/// The result of applying a [`Command`].
#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct CommandResult {
    /// The key's value before the command, if it had one.
    pub previous: Option<String>,
}

/// Commands written to the log as one entry; `server::writer` explains why.
#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct Batch {
    /// Applied in order.
    pub commands: Vec<Command>,
}

/// The result of applying a [`Batch`]: one result per command, in order.
#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct BatchResult {
    pub results: Vec<CommandResult>,
}

openraft::declare_raft_types!(
    /// Plugs the store's types into openraft.
    pub TypeConfig:
        D = Batch,
        R = BatchResult,
);

/// A Raft node for this store.
pub type Raft = openraft::Raft<TypeConfig>;
