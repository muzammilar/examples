//! Write throughput through one node: Raft, the WAL with its batched fsync,
//! and the state machine, without the network.

use std::collections::BTreeMap;
use std::sync::Arc;
use std::time::Duration;

use criterion::{BenchmarkId, Criterion, Throughput, criterion_group, criterion_main};
use kv_store::Command;
use kv_store::server::{Config, Node};
use openraft::ServerState;
use tempfile::TempDir;
use tokio::net::TcpListener;
use tokio::runtime::Runtime;
use tokio::task::JoinSet;

async fn start_single_node(dir: &TempDir) -> Node {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let config = Config {
        id: 1,
        members: BTreeMap::from([(1, listener.local_addr().unwrap().to_string())]),
        data_dir: dir.path().to_owned(),
        bootstrap: true,
        raft: Config::raft_defaults(10_000, 1_000),
    };
    let node = Node::start(config, listener).await.unwrap();
    node.raft()
        .wait(Some(Duration::from_secs(5)))
        .state(ServerState::Leader, "single node elects itself")
        .await
        .unwrap();
    node
}

async fn write_batch(node: &Arc<Node>, in_flight: u64) {
    let mut writes = JoinSet::new();
    for i in 0..in_flight {
        let node = Arc::clone(node);
        writes.spawn(async move {
            let command = Command::Put {
                key: format!("key-{i}"),
                value: "value".into(),
            };
            node.write(command).await.unwrap();
        });
    }
    while let Some(result) = writes.join_next().await {
        result.unwrap();
    }
}

fn writes(c: &mut Criterion) {
    let runtime = Runtime::new().unwrap();
    let dir = TempDir::new().unwrap();
    let node = Arc::new(runtime.block_on(start_single_node(&dir)));

    let mut group = c.benchmark_group("writes");
    for in_flight in [1, 16, 64] {
        group.throughput(Throughput::Elements(in_flight));
        group.bench_with_input(
            BenchmarkId::new("in_flight", in_flight),
            &in_flight,
            |b, &n| {
                b.to_async(&runtime).iter(|| write_batch(&node, n));
            },
        );
    }
    group.finish();

    let node = Arc::into_inner(node).expect("benchmarks are done with the node");
    runtime.block_on(node.shutdown()).unwrap();
}

criterion_group!(benches, writes);
criterion_main!(benches);
