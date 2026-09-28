//! Runs real three-node clusters in-process, over localhost.

use std::collections::BTreeMap;
use std::net::SocketAddr;
use std::sync::Arc;
use std::time::Duration;

use kv_store::NodeId;
use kv_store::client::{Client, ClientError};
use kv_store::server::{Config, Node};
use openraft::ServerState;
use reqwest::StatusCode;
use tempfile::TempDir;
use tokio::net::TcpListener;
use tokio::task::JoinSet;
use tokio::time::{self, Instant};

/// Snapshot often and keep few entries, to exercise the snapshot paths.
const SNAPSHOT_EVERY: u64 = 50;
const KEEP_LOGS: u64 = 5;

struct Cluster {
    addrs: BTreeMap<NodeId, SocketAddr>,
    dirs: BTreeMap<NodeId, TempDir>,
    nodes: BTreeMap<NodeId, Node>,
}

impl Cluster {
    async fn start() -> Self {
        let mut listeners = BTreeMap::new();
        for id in 1..=3 {
            listeners.insert(id, TcpListener::bind("127.0.0.1:0").await.unwrap());
        }
        let mut cluster = Self {
            addrs: (listeners.iter())
                .map(|(&id, l)| (id, l.local_addr().unwrap()))
                .collect(),
            dirs: (1..=3).map(|id| (id, TempDir::new().unwrap())).collect(),
            nodes: BTreeMap::new(),
        };
        for (id, listener) in listeners {
            cluster.launch(id, listener).await;
        }
        cluster.leader().await;
        cluster
    }

    async fn launch(&mut self, id: NodeId, listener: TcpListener) {
        let mut raft = Config::raft_defaults(SNAPSHOT_EVERY, KEEP_LOGS);
        raft.heartbeat_interval = 50;
        raft.election_timeout_min = 150;
        raft.election_timeout_max = 300;
        let config = Config {
            id,
            members: (self.addrs.iter())
                .map(|(&id, addr)| (id, addr.to_string()))
                .collect(),
            data_dir: self.dirs[&id].path().to_owned(),
            bootstrap: id == 1,
            raft,
        };
        self.nodes
            .insert(id, Node::start(config, listener).await.unwrap());
    }

    async fn stop(&mut self, id: NodeId) {
        self.nodes.remove(&id).unwrap().shutdown().await.unwrap();
    }

    async fn restart(&mut self, id: NodeId) {
        let listener = TcpListener::bind(self.addrs[&id]).await.unwrap();
        self.launch(id, listener).await;
    }

    /// Replaces a stopped node's data directory with an empty one.
    fn wipe(&mut self, id: NodeId) {
        assert!(!self.nodes.contains_key(&id), "stop the node first");
        self.dirs.insert(id, TempDir::new().unwrap());
    }

    fn addr(&self, id: NodeId) -> String {
        self.addrs[&id].to_string()
    }

    fn client(&self) -> Client {
        Client::new(self.addrs.values().map(ToString::to_string)).unwrap()
    }

    /// Waits until a running node is leader, and returns it.
    async fn leader(&self) -> NodeId {
        eventually("a leader is elected", || async {
            (self.nodes.iter())
                .find(|(_, node)| node.raft().metrics().borrow().state == ServerState::Leader)
                .map(|(&id, _)| id)
        })
        .await
    }

    async fn follower(&self) -> NodeId {
        let leader = self.leader().await;
        *self.nodes.keys().find(|&&id| id != leader).unwrap()
    }
}

/// Polls `check` until it returns `Some`, failing after a few seconds.
async fn eventually<T, F, Fut>(what: &str, mut check: F) -> T
where
    F: FnMut() -> Fut,
    Fut: Future<Output = Option<T>>,
{
    let deadline = Instant::now() + Duration::from_secs(10);
    loop {
        if let Some(value) = check().await {
            return value;
        }
        assert!(Instant::now() < deadline, "timed out waiting until {what}");
        time::sleep(Duration::from_millis(50)).await;
    }
}

async fn fill(client: &Client, keys: std::ops::Range<u32>) {
    for i in keys {
        client
            .put(&format!("key-{i}"), &format!("value-{i}"))
            .await
            .unwrap();
    }
}

/// Waits until `node`'s own copy has `key-{i}`.
async fn wait_for_key(cluster: &Cluster, client: &Client, node: NodeId, i: u32) {
    let addr = cluster.addr(node);
    let (key, value) = (format!("key-{i}"), format!("value-{i}"));
    eventually(&format!("node {node} has {key}"), || async {
        let local = client.get_local(&addr, &key).await.ok().flatten();
        (local.as_deref() == Some(value.as_str())).then_some(())
    })
    .await;
}

#[tokio::test]
async fn writes_reach_every_node() {
    let cluster = Cluster::start().await;
    let client = cluster.client();
    fill(&client, 0..10).await;

    for id in 1..=3 {
        wait_for_key(&cluster, &client, id, 9).await;
    }
    assert_eq!(
        client.get("key-3").await.unwrap().as_deref(),
        Some("value-3")
    );
}

#[tokio::test]
async fn delete_reports_whether_the_key_existed() {
    let cluster = Cluster::start().await;
    let client = cluster.client();
    client.put("k", "v").await.unwrap();

    assert!(client.delete("k").await.unwrap());
    assert_eq!(client.get("k").await.unwrap(), None);
    assert!(!client.delete("k").await.unwrap());
}

#[tokio::test]
async fn concurrent_writes_are_batched_into_fewer_entries() {
    let cluster = Cluster::start().await;
    let client = Arc::new(cluster.client());
    let mut writes = JoinSet::new();
    for i in 0..200 {
        let client = Arc::clone(&client);
        writes.spawn(async move { client.put(&format!("key-{i}"), &format!("value-{i}")).await });
    }
    while let Some(result) = writes.join_next().await {
        result.unwrap().unwrap();
    }

    let leader = cluster.addr(cluster.leader().await);
    let status = client.status(&leader).await.unwrap();
    assert_eq!(status.keys, 200);
    assert!(
        status.last_log_index.unwrap() < 150,
        "200 concurrent writes should share log entries: {status:?}"
    );
}

#[tokio::test]
async fn followers_redirect_to_the_leader() {
    let cluster = Cluster::start().await;
    let leader = cluster.leader().await;
    let follower = *cluster.nodes.keys().find(|&&id| id != leader).unwrap();

    let no_redirects = reqwest::Client::builder()
        .redirect(reqwest::redirect::Policy::none())
        .build()
        .unwrap();
    let response = no_redirects
        .put(format!("http://{}/kv/k", cluster.addr(follower)))
        .body("v")
        .send()
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::TEMPORARY_REDIRECT);
    assert_eq!(
        response.headers()["location"],
        format!("http://{}/kv/k", cluster.addr(leader)).as_str()
    );

    // A client that only knows the follower still gets there.
    let client = Client::new([cluster.addr(follower)]).unwrap();
    client.put("k", "v").await.unwrap();
    assert_eq!(client.get("k").await.unwrap().as_deref(), Some("v"));
}

#[tokio::test]
async fn survives_losing_the_leader() {
    let mut cluster = Cluster::start().await;
    let client = cluster.client();
    fill(&client, 0..5).await;

    let old_leader = cluster.leader().await;
    cluster.stop(old_leader).await;

    let new_leader = cluster.leader().await;
    assert_ne!(new_leader, old_leader);
    fill(&client, 5..10).await;
    assert_eq!(
        client.get("key-2").await.unwrap().as_deref(),
        Some("value-2")
    );

    // Back from the dead, the old leader catches up as a follower.
    cluster.restart(old_leader).await;
    wait_for_key(&cluster, &client, old_leader, 9).await;
}

#[tokio::test]
async fn restarted_node_recovers_from_its_own_log_and_snapshot() {
    let mut cluster = Cluster::start().await;
    let client = cluster.client();
    // Enough writes for a snapshot, plus some log after it.
    fill(&client, 0..80).await;
    let follower = cluster.follower().await;
    wait_for_key(&cluster, &client, follower, 79).await;
    let before = client.status(&cluster.addr(follower)).await.unwrap();
    assert!(
        before.snapshot.is_some(),
        "took its own snapshot: {before:?}"
    );

    cluster.stop(follower).await;
    fill(&client, 80..90).await;
    cluster.restart(follower).await;

    wait_for_key(&cluster, &client, follower, 89).await;
    let after = client.status(&cluster.addr(follower)).await.unwrap();
    assert_eq!(after.keys, 90);
    // It restarted from its own snapshot; the leader didn't send a new one.
    assert!(
        after.snapshot >= before.snapshot,
        "{before:?} then {after:?}"
    );
}

#[tokio::test]
async fn wiped_node_is_rebuilt_from_the_leader() {
    let mut cluster = Cluster::start().await;
    let client = cluster.client();
    fill(&client, 0..120).await;

    let follower = cluster.follower().await;
    cluster.stop(follower).await;
    cluster.wipe(follower);
    cluster.restart(follower).await;

    // The leader has purged its early log, so the node can only have been
    // rebuilt from a snapshot.
    wait_for_key(&cluster, &client, follower, 119).await;
    wait_for_key(&cluster, &client, follower, 0).await;
    let status = client.status(&cluster.addr(follower)).await.unwrap();
    assert!(
        status.snapshot.is_some(),
        "rebuilt from a snapshot: {status:?}"
    );
    assert_eq!(status.keys, 120);
}

#[tokio::test]
async fn refuses_writes_without_a_majority() {
    let mut cluster = Cluster::start().await;
    let client = cluster
        .client()
        .retry_for(Duration::from_secs(1))
        .request_timeout(Duration::from_millis(500));
    client.put("before", "ok").await.unwrap();

    let leader = cluster.leader().await;
    let followers: Vec<_> = (cluster.nodes.keys().copied())
        .filter(|&id| id != leader)
        .collect();
    for id in followers {
        cluster.stop(id).await;
    }

    let result = client.put("after", "lost").await;
    assert!(
        matches!(result, Err(ClientError::Unavailable { .. })),
        "a lone node must not confirm writes: {result:?}"
    );
}
