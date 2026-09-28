//! Raft RPCs between nodes, as JSON over HTTP.

use std::io;

use openraft::BasicNode;
use openraft::error::{
    InstallSnapshotError, NetworkError, PayloadTooLarge, RPCError, RaftError, RemoteError,
    Unreachable,
};
use openraft::network::{RPCOption, RaftNetwork, RaftNetworkFactory};
use openraft::raft::{
    AppendEntriesRequest, AppendEntriesResponse, InstallSnapshotRequest, InstallSnapshotResponse,
    VoteRequest, VoteResponse,
};
use reqwest::StatusCode;
use serde::Serialize;
use serde::de::DeserializeOwned;

use crate::{NodeId, TypeConfig};

type RpcError<E = openraft::error::Infallible> = RPCError<NodeId, BasicNode, RaftError<NodeId, E>>;

/// Hands openraft a connection to each peer. They all share one HTTP client
/// and so one connection pool.
#[derive(Debug, Clone, Default)]
pub(crate) struct Network {
    http: reqwest::Client,
}

impl RaftNetworkFactory<TypeConfig> for Network {
    type Network = Peer;

    async fn new_client(&mut self, target: NodeId, node: &BasicNode) -> Peer {
        Peer {
            http: self.http.clone(),
            target,
            url: format!("http://{}/raft", node.addr),
        }
    }
}

/// One peer, as seen by the node talking to it.
#[derive(Debug)]
pub(crate) struct Peer {
    http: reqwest::Client,
    target: NodeId,
    url: String,
}

impl Peer {
    /// Sends one RPC carrying `entries` log entries. If the peer finds it too
    /// large, openraft retries with fewer.
    #[expect(clippy::result_large_err, reason = "openraft's error types are large")]
    async fn call<Req, Resp, E>(
        &self,
        rpc: &str,
        request: &Req,
        entries: usize,
        option: &RPCOption,
    ) -> Result<Resp, RpcError<E>>
    where
        Req: Serialize,
        Resp: DeserializeOwned,
        E: std::error::Error + DeserializeOwned,
    {
        let response = self
            .http
            .post(format!("{}/{rpc}", self.url))
            .timeout(option.hard_ttl())
            .json(request)
            .send()
            .await
            .map_err(|err| {
                // Unreachable makes openraft back off before retrying a node
                // that is down.
                if err.is_connect() {
                    RPCError::Unreachable(Unreachable::new(&err))
                } else {
                    RPCError::Network(NetworkError::new(&err))
                }
            })?;
        // Anything but 200 means the request never reached Raft (a body over
        // the size limit, say) and there's no Raft result to decode.
        let status = response.status();
        if status == StatusCode::PAYLOAD_TOO_LARGE && entries > 1 {
            // openraft retries with at most this many entries.
            let hint = (entries / 2) as u64;
            return Err(RPCError::PayloadTooLarge(
                PayloadTooLarge::new_entries_hint(hint),
            ));
        }
        if !status.is_success() {
            let body = response.text().await.unwrap_or_default();
            let err = io::Error::other(format!("{status}: {}", body.trim()));
            return Err(RPCError::Network(NetworkError::new(&err)));
        }
        let result: Result<Resp, RaftError<NodeId, E>> = response
            .json()
            .await
            .map_err(|err| RPCError::Network(NetworkError::new(&err)))?;
        result.map_err(|err| RPCError::RemoteError(RemoteError::new(self.target, err)))
    }
}

impl RaftNetwork<TypeConfig> for Peer {
    async fn append_entries(
        &mut self,
        request: AppendEntriesRequest<TypeConfig>,
        option: RPCOption,
    ) -> Result<AppendEntriesResponse<NodeId>, RpcError> {
        let entries = request.entries.len();
        self.call("append", &request, entries, &option).await
    }

    async fn install_snapshot(
        &mut self,
        request: InstallSnapshotRequest<TypeConfig>,
        option: RPCOption,
    ) -> Result<InstallSnapshotResponse<NodeId>, RpcError<InstallSnapshotError>> {
        self.call("snapshot", &request, 0, &option).await
    }

    async fn vote(
        &mut self,
        request: VoteRequest<NodeId>,
        option: RPCOption,
    ) -> Result<VoteResponse<NodeId>, RpcError> {
        self.call("vote", &request, 0, &option).await
    }
}
