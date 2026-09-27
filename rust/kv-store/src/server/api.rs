//! HTTP routes for clients and peers.

use std::collections::BTreeSet;
use std::fmt;
use std::sync::Arc;

use axum::extract::{DefaultBodyLimit, OriginalUri, Path, Query, State};
use axum::http::{StatusCode, Uri, header};
use axum::response::{IntoResponse, Redirect, Response};
use axum::routing::{get, post};
use axum::{Json, Router};
use openraft::error::{CheckIsLeaderError, ForwardToLeader, RaftError};
use openraft::raft::{AppendEntriesRequest, InstallSnapshotRequest, VoteRequest};
use openraft::{BasicNode, ServerState};
use serde::{Deserialize, Serialize};

use super::writer::{WriteError, Writer};
use crate::storage::StateMachine;
use crate::{Command, NodeId, Raft, TypeConfig};

/// Largest Raft RPC body accepted.
pub(super) const MAX_RPC_BODY: usize = 64 * 1024 * 1024;

pub(super) struct App {
    pub(super) raft: Raft,
    pub(super) writer: Writer,
    pub(super) state_machine: StateMachine,
}

pub(super) fn routes(app: Arc<App>) -> Router {
    let raft = Router::new()
        .route("/append", post(append_entries))
        .route("/vote", post(vote))
        .route("/snapshot", post(install_snapshot))
        .layer(DefaultBodyLimit::max(MAX_RPC_BODY));
    Router::new()
        .route("/kv/{key}", get(read).put(write).delete(delete))
        .route("/status", get(status))
        .nest("/raft", raft)
        .with_state(app)
}

#[derive(Deserialize)]
struct ReadParams {
    #[serde(default)]
    stale: bool,
}

async fn read(
    State(app): State<Arc<App>>,
    Path(key): Path<String>,
    Query(params): Query<ReadParams>,
    OriginalUri(uri): OriginalUri,
) -> Response {
    if !params.stale {
        // Confirms this node is still leader and has applied everything
        // committed before the read arrived.
        match app.raft.ensure_linearizable().await {
            Ok(_) => {}
            Err(RaftError::APIError(CheckIsLeaderError::ForwardToLeader(to))) => {
                return redirect(&to, &uri);
            }
            Err(err) => return unavailable(&err),
        }
    }
    match app.state_machine.get(&key).await {
        Some(value) => value.into_response(),
        None => StatusCode::NOT_FOUND.into_response(),
    }
}

async fn write(
    State(app): State<Arc<App>>,
    Path(key): Path<String>,
    OriginalUri(uri): OriginalUri,
    value: String,
) -> Response {
    match app.writer.write(Command::Put { key, value }).await {
        Ok(_) => StatusCode::NO_CONTENT.into_response(),
        Err(err) => write_error(&err, &uri),
    }
}

async fn delete(
    State(app): State<Arc<App>>,
    Path(key): Path<String>,
    OriginalUri(uri): OriginalUri,
) -> Response {
    match app.writer.write(Command::Delete { key }).await {
        Ok(result) if result.previous.is_some() => StatusCode::NO_CONTENT.into_response(),
        Ok(_) => StatusCode::NOT_FOUND.into_response(),
        Err(err) => write_error(&err, &uri),
    }
}

fn write_error(err: &WriteError, uri: &Uri) -> Response {
    match err {
        WriteError::NotLeader(to) => redirect(to, uri),
        WriteError::Failed(_) => unavailable(err),
    }
}

fn redirect(to: &ForwardToLeader<NodeId, BasicNode>, uri: &Uri) -> Response {
    let Some(leader) = &to.leader_node else {
        let retry = [(header::RETRY_AFTER, "1")];
        return (
            StatusCode::SERVICE_UNAVAILABLE,
            retry,
            "no leader elected yet\n",
        )
            .into_response();
    };
    let path = uri.path_and_query().map_or("/", |p| p.as_str());
    Redirect::temporary(&format!("http://{}{path}", leader.addr)).into_response()
}

fn unavailable(err: &impl fmt::Display) -> Response {
    (StatusCode::SERVICE_UNAVAILABLE, format!("{err}\n")).into_response()
}

/// What a node currently is.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Role {
    /// Accepting writes and replicating them.
    Leader,
    /// Following a leader, or waiting to hear from one.
    Follower,
    /// Asking for votes.
    Candidate,
    /// Receiving the log without a vote.
    Learner,
    /// Stopped, by shutdown or a fatal error.
    Shutdown,
}

impl From<ServerState> for Role {
    fn from(state: ServerState) -> Self {
        match state {
            ServerState::Leader => Self::Leader,
            ServerState::Follower => Self::Follower,
            ServerState::Candidate => Self::Candidate,
            ServerState::Learner => Self::Learner,
            ServerState::Shutdown => Self::Shutdown,
        }
    }
}

impl fmt::Display for Role {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(match self {
            Self::Leader => "leader",
            Self::Follower => "follower",
            Self::Candidate => "candidate",
            Self::Learner => "learner",
            Self::Shutdown => "shutdown",
        })
    }
}

/// A node's view of the cluster, as served by `GET /status`.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Status {
    pub id: NodeId,
    pub role: Role,
    pub term: u64,
    /// The leader this node knows of, if any.
    pub leader: Option<NodeId>,
    /// Voting members of the cluster.
    pub members: BTreeSet<NodeId>,
    /// Index of the last entry in this node's log.
    pub last_log_index: Option<u64>,
    /// Index of the last entry applied to this node's data.
    pub last_applied: Option<u64>,
    /// Last index covered by this node's snapshot.
    pub snapshot: Option<u64>,
    /// Keys stored on this node.
    pub keys: usize,
}

async fn status(State(app): State<Arc<App>>) -> Json<Status> {
    // Cloned, because the borrow can't be held across the `.await` below.
    let metrics = app.raft.metrics().borrow().clone();
    Json(Status {
        id: metrics.id,
        role: metrics.state.into(),
        term: metrics.current_term,
        leader: metrics.current_leader,
        members: metrics.membership_config.membership().voter_ids().collect(),
        last_log_index: metrics.last_log_index,
        last_applied: metrics.last_applied.map(|id| id.index),
        snapshot: metrics.snapshot.map(|id| id.index),
        keys: app.state_machine.len().await,
    })
}

async fn append_entries(
    State(app): State<Arc<App>>,
    Json(request): Json<AppendEntriesRequest<TypeConfig>>,
) -> impl IntoResponse {
    Json(app.raft.append_entries(request).await)
}

async fn vote(
    State(app): State<Arc<App>>,
    Json(request): Json<VoteRequest<NodeId>>,
) -> impl IntoResponse {
    Json(app.raft.vote(request).await)
}

async fn install_snapshot(
    State(app): State<Arc<App>>,
    Json(request): Json<InstallSnapshotRequest<TypeConfig>>,
) -> impl IntoResponse {
    Json(app.raft.install_snapshot(request).await)
}
