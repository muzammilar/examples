//! Command-line client for a kv-store cluster.

mod bench;

use std::process::ExitCode;
use std::sync::Arc;

use clap::{Parser, Subcommand};
use kv_store::Chain;
use kv_store::client::Client;
use kv_store::server::Role;
use tokio::task::JoinSet;

/// Command-line client for a kv-store cluster.
#[derive(Debug, Parser)]
#[command(version)]
struct Cli {
    /// Nodes to talk to, as comma-separated HOST:PORT.
    #[arg(
        long,
        env = "KV_CLUSTER",
        value_delimiter = ',',
        default_value = "127.0.0.1:7000"
    )]
    cluster: Vec<String>,

    #[command(subcommand)]
    command: Command,
}

#[derive(Debug, Subcommand)]
enum Command {
    /// Set a key.
    Put { key: String, value: String },
    /// Print a key's value.
    Get {
        key: String,
        /// Read NODE's own copy instead of asking the leader. May be stale.
        #[arg(long, value_name = "NODE")]
        local: Option<String>,
    },
    /// Remove a key.
    Delete { key: String },
    /// Show each node's view of the cluster.
    Status,
    /// Print the leader's address.
    Leader,
    /// Succeed if every node in --cluster knows of a leader. Used as the
    /// containers' health check.
    Health,
    /// Write COUNT keys named PREFIX-0, PREFIX-1, ..., for trying things out.
    Fill {
        #[arg(long, default_value_t = 100)]
        count: u32,
        #[arg(long, default_value = "key")]
        prefix: String,
    },
    /// Measure write and read throughput and latency against the leader.
    Bench {
        /// Requests of each kind.
        #[arg(long, default_value_t = 2000, value_parser = clap::value_parser!(u32).range(1..))]
        requests: u32,
        /// Concurrent requests.
        #[arg(long, default_value_t = 32, value_parser = clap::value_parser!(u32).range(1..))]
        concurrency: u32,
        /// Size of each value, in bytes.
        #[arg(long, default_value_t = 100)]
        value_size: usize,
    },
}

#[tokio::main]
async fn main() -> ExitCode {
    let cli = Cli::parse();
    let client = match Client::new(&cli.cluster) {
        Ok(client) => client,
        Err(err) => {
            eprintln!("kv: {err}");
            return ExitCode::from(2);
        }
    };
    match run(client, cli.command).await {
        Ok(code) => code,
        Err(err) => {
            eprintln!("kv: {}", Chain(err.as_ref()));
            ExitCode::FAILURE
        }
    }
}

async fn run(client: Client, command: Command) -> Result<ExitCode, Box<dyn std::error::Error>> {
    match command {
        Command::Put { key, value } => client.put(&key, &value).await?,
        Command::Get { key, local } => {
            let value = match local {
                Some(node) => client.get_local(&node, &key).await?,
                None => client.get(&key).await?,
            };
            let Some(value) = value else {
                eprintln!("kv: {key}: not found");
                return Ok(ExitCode::FAILURE);
            };
            println!("{value}");
        }
        Command::Delete { key } => {
            if !client.delete(&key).await? {
                eprintln!("kv: {key}: not found");
                return Ok(ExitCode::FAILURE);
            }
        }
        Command::Status => return Ok(print_status(&client).await),
        Command::Health => {
            for node in client.nodes() {
                let status = client.status(node).await?;
                if status.leader.is_none() {
                    eprintln!("kv: {node}: no leader");
                    return Ok(ExitCode::FAILURE);
                }
            }
        }
        Command::Leader => {
            let Some(leader) = find_leader(&client).await else {
                eprintln!("kv: no leader found");
                return Ok(ExitCode::FAILURE);
            };
            println!("{leader}");
        }
        Command::Fill { count, prefix } => fill(client, count, &prefix).await?,
        Command::Bench {
            requests,
            concurrency,
            value_size,
        } => {
            // Talk to the leader directly: through a follower, every request
            // would pay for a redirect first.
            let Some(leader) = find_leader(&client).await else {
                eprintln!("kv: no leader found");
                return Ok(ExitCode::FAILURE);
            };
            let options = bench::Options {
                requests,
                concurrency,
                value_size,
            };
            bench::run(Client::new([leader])?, options).await?;
        }
    }
    Ok(ExitCode::SUCCESS)
}

/// The address of whichever node says it's the leader.
async fn find_leader(client: &Client) -> Option<String> {
    for node in client.nodes() {
        if client
            .status(node)
            .await
            .is_ok_and(|s| s.role == Role::Leader)
        {
            return Some(node.to_owned());
        }
    }
    None
}

/// Prints one line per node. Fails only if no node answers.
async fn print_status(client: &Client) -> ExitCode {
    println!(
        "{:<20} {:>3} {:<10} {:>5} {:>7} {:>9} {:>8} {:>9} {:>6}",
        "NODE", "ID", "ROLE", "TERM", "LEADER", "LAST LOG", "APPLIED", "SNAPSHOT", "KEYS"
    );
    let mut reachable = false;
    for node in client.nodes() {
        match client.status(node).await {
            Ok(s) => {
                reachable = true;
                let show = |n: Option<u64>| n.map_or_else(|| "-".to_owned(), |n| n.to_string());
                println!(
                    "{node:<20} {:>3} {:<10} {:>5} {:>7} {:>9} {:>8} {:>9} {:>6}",
                    s.id,
                    s.role.to_string(),
                    s.term,
                    show(s.leader),
                    show(s.last_log_index),
                    show(s.last_applied),
                    show(s.snapshot),
                    s.keys
                );
            }
            Err(_) => println!("{node:<20} unreachable"),
        }
    }
    if reachable {
        ExitCode::SUCCESS
    } else {
        ExitCode::FAILURE
    }
}

/// Writes `count` keys, `IN_FLIGHT` at a time.
async fn fill(client: Client, count: u32, prefix: &str) -> Result<(), Box<dyn std::error::Error>> {
    const IN_FLIGHT: u32 = 16;
    let client = Arc::new(client);
    let mut tasks = JoinSet::new();
    for i in 0..count {
        if tasks.len() >= IN_FLIGHT as usize {
            tasks.join_next().await.expect("tasks are pending")??;
        }
        let client = Arc::clone(&client);
        let (key, value) = (format!("{prefix}-{i}"), format!("value-{i}"));
        tasks.spawn(async move { client.put(&key, &value).await });
    }
    while let Some(result) = tasks.join_next().await {
        result??;
    }
    println!("wrote {count} keys");
    Ok(())
}
