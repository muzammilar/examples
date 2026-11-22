use anyhow::{Result, ensure};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::{
    io::{BufRead, BufReader, Write},
    os::unix::net::UnixStream,
    path::Path,
    time::{Duration, SystemTime, UNIX_EPOCH},
};

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Pair {
    pub id: u64,
    pub left: String,
    pub right: String,
}

pub fn pair(id: u64) -> Pair {
    fn value(id: u64, side: &str) -> String {
        let prefix = format!(
            "customer={:04};id={id:016};side={side};seed=20261122;",
            id % 10000
        );
        let hash = format!("{:x}", Sha256::digest(prefix.as_bytes()));
        let mut body = prefix;
        while body.len() < 192 {
            body.push('x');
        }
        body.push_str(&hash);
        assert_eq!(body.len(), 256);
        body
    }
    Pair {
        id,
        left: value(id, "L"),
        right: value(id, "R"),
    }
}

pub fn now() -> u128 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .expect("clock before epoch")
        .as_nanos()
}

#[derive(Serialize, Deserialize)]
#[serde(tag = "op", rename_all = "snake_case")]
pub enum Request {
    Health,
    Snapshot { row: Pair },
    Put { rows: Vec<Pair> },
    Read { id: u64 },
    Delete { id: u64 },
    Abort { row: Pair },
    Export { path: String },
    Stop,
}

#[derive(Default, Debug, Serialize, Deserialize)]
pub struct Reply {
    pub error: Option<String>,
    pub left: Option<String>,
    pub right: Option<String>,
    pub records: Option<u64>,
}

pub fn rpc(socket: &Path, request: &Request) -> Result<Reply> {
    let mut stream = UnixStream::connect(socket)?;
    stream.set_read_timeout(Some(Duration::from_secs(
        if matches!(request, Request::Export { .. }) {
            120
        } else {
            1
        },
    )))?;
    stream.set_write_timeout(Some(Duration::from_secs(1)))?;
    let mut bytes = serde_json::to_vec(request)?;
    bytes.push(b'\n');
    stream.write_all(&bytes)?;
    let mut line = String::new();
    BufReader::new(stream).read_line(&mut line)?;
    let reply: Reply = serde_json::from_str(&line)?;
    ensure!(
        reply.error.is_none(),
        "native request failed: {:?}",
        reply.error
    );
    Ok(reply)
}
