//! Runs one node of a kv-store cluster.

use std::collections::BTreeMap;
use std::net::SocketAddr;
use std::path::PathBuf;
use std::process::ExitCode;

use clap::Parser;
use kv_store::server::{Config, Node};
use kv_store::{Chain, NodeId};
use tokio::net::TcpListener;
use tracing_subscriber::EnvFilter;

/// Runs one node of a kv-store cluster.
#[derive(Debug, Parser)]
#[command(version)]
struct Args {
    /// This node's ID, as listed in --members.
    #[arg(long, env = "KV_ID")]
    id: NodeId,

    /// Every member, this node included, as ID=HOST:PORT. The address is
    /// where other nodes and clients reach the member.
    #[arg(long, env = "KV_MEMBERS", value_delimiter = ',', value_parser = parse_member, required = true)]
    members: Vec<(NodeId, String)>,

    /// Form a new cluster from --members if this node has never been part of
    /// one. Set it on exactly one node.
    #[arg(long, env = "KV_BOOTSTRAP")]
    bootstrap: bool,

    /// Address to listen on.
    #[arg(long, env = "KV_LISTEN", default_value = "0.0.0.0:7000")]
    listen: SocketAddr,

    /// Where to keep the log and snapshots.
    #[arg(long, env = "KV_DATA_DIR", default_value = "data")]
    data_dir: PathBuf,

    /// Take a snapshot after this many new log entries.
    #[arg(long, env = "KV_SNAPSHOT_EVERY", default_value_t = 1000)]
    snapshot_every: u64,

    /// Log entries to keep after a snapshot, for followers that are only a
    /// little behind.
    #[arg(long, env = "KV_KEEP_LOGS", default_value_t = 100)]
    keep_logs: u64,
}

fn parse_member(s: &str) -> Result<(NodeId, String), String> {
    let (id, addr) = s.split_once('=').ok_or("expected ID=HOST:PORT")?;
    let id = id
        .parse()
        .map_err(|err| format!("bad node ID {id:?}: {err}"))?;
    Ok((id, addr.to_owned()))
}

#[tokio::main]
async fn main() -> ExitCode {
    tracing_subscriber::fmt()
        .with_env_filter(
            EnvFilter::try_from_default_env().unwrap_or_else(|_| "info,openraft=warn".into()),
        )
        .init();

    let args = Args::parse();
    let members: BTreeMap<NodeId, String> = args.members.into_iter().collect();
    if !members.contains_key(&args.id) {
        eprintln!("kv-store: --id {} is not in --members", args.id);
        return ExitCode::from(2);
    }

    let config = Config {
        id: args.id,
        members,
        data_dir: args.data_dir,
        bootstrap: args.bootstrap,
        raft: Config::raft_defaults(args.snapshot_every, args.keep_logs),
    };
    match run(config, args.listen).await {
        Ok(()) => ExitCode::SUCCESS,
        Err(err) => {
            eprintln!("kv-store: {}", Chain(err.as_ref()));
            ExitCode::FAILURE
        }
    }
}

async fn run(config: Config, listen: SocketAddr) -> Result<(), Box<dyn std::error::Error>> {
    let listener = TcpListener::bind(listen).await?;
    let id = config.id;
    let node = Node::start(config, listener).await?;
    tracing::info!("node {id} listening on {listen}");

    shutdown_signal().await?;
    tracing::info!("shutting down");
    node.shutdown().await?;
    Ok(())
}

/// Resolves on Ctrl-C, or on SIGTERM, which is what `docker stop` sends.
async fn shutdown_signal() -> std::io::Result<()> {
    #[cfg(unix)]
    {
        let mut terminate =
            tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate())?;
        tokio::select! {
            result = tokio::signal::ctrl_c() => result,
            _ = terminate.recv() => Ok(()),
        }
    }
    #[cfg(not(unix))]
    tokio::signal::ctrl_c().await
}
