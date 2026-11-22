use crate::protocol::{Request, now, pair, rpc};
use anyhow::{Result, ensure};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{
    fs::{File, OpenOptions},
    io::{BufRead, BufReader, Write},
    path::Path,
    thread,
    time::{Duration, Instant},
};

pub fn append(file: &mut File, event: &Value) -> Result<()> {
    let mut bytes = serde_json::to_vec(event)?;
    bytes.push(b'\n');
    file.write_all(&bytes)?;
    file.sync_all()?;
    Ok(())
}
pub fn save(path: &Path, value: &Value) -> Result<()> {
    let mut file = File::create_new(path)?;
    serde_json::to_writer_pretty(&mut file, value)?;
    file.write_all(b"\n")?;
    file.sync_all()?;
    File::open(path.parent().ok_or_else(|| anyhow::anyhow!("no parent"))?)?.sync_all()?;
    Ok(())
}
pub fn events(path: &Path) -> Result<Vec<Value>> {
    if !path.exists() {
        return Ok(Vec::new());
    }
    let mut result = Vec::new();
    let mut reader = BufReader::new(File::open(path)?);
    loop {
        let mut line = String::new();
        if reader.read_line(&mut line)? == 0 {
            break;
        }
        if !line.ends_with('\n') {
            break;
        } // A concurrent writer may be mid-record; final verifier checks its clean exit.
        result.push(serde_json::from_str(&line)?);
    }
    Ok(result)
}
pub fn actor(role: &str, socket: &Path, root: &Path, first_id: u64) -> Result<()> {
    let mut journal = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(root.join(format!("{role}.jsonl")))?;
    let mut sequence = 0;
    while !root.join("stop-actors").exists() {
        sequence += 1;
        let started = now();
        let timer = Instant::now();
        if role == "writer" {
            let row = pair(first_id + sequence);
            append(
                &mut journal,
                &json!({"kind":"intent","id":row.id,"started_ns":started,"left":row.left,"right":row.right}),
            )?;
            let intent_preparation_and_fsync_ns = timer.elapsed().as_nanos();
            let request_started_ns = now();
            let request_timer = Instant::now();
            let result = rpc(
                socket,
                &Request::Put {
                    rows: vec![row.clone()],
                },
            );
            let succeeded = result.is_ok();
            append(
                &mut journal,
                &json!({"kind":if succeeded{"ok"}else{"ambiguous"},"id":row.id,"started_ns":started,"ended_ns":now(),"request_started_ns":request_started_ns,"elapsed_ns":request_timer.elapsed().as_nanos(),"intent_preparation_and_fsync_ns":intent_preparation_and_fsync_ns,"error":result.err().map(|e|format!("{e:#}"))}),
            )?;
            if succeeded {
                let temporary = root.join("latest-ack.next");
                let mut marker = File::create(&temporary)?;
                write!(marker, "{}", row.id)?;
                marker.sync_all()?;
                std::fs::rename(temporary, root.join("latest-ack"))?;
                File::open(root)?.sync_all()?;
            }
        } else {
            let latest = match std::fs::read_to_string(root.join("latest-ack")) {
                Ok(value) => value.parse()?,
                Err(e) if e.kind() == std::io::ErrorKind::NotFound => 0,
                Err(e) => return Err(e.into()),
            };
            for (locality, id) in [("historical", 0), ("recent", latest)] {
                let began = now();
                let t = Instant::now();
                let result = rpc(socket, &Request::Read { id });
                let expected = pair(id);
                let actual_left = result
                    .as_ref()
                    .ok()
                    .and_then(|reply| reply.left.as_ref())
                    .map(|body| format!("{:x}", Sha256::digest(body.as_bytes())));
                let actual_right = result
                    .as_ref()
                    .ok()
                    .and_then(|reply| reply.right.as_ref())
                    .map(|body| format!("{:x}", Sha256::digest(body.as_bytes())));
                let (kind, error) = match result {
                    Ok(reply)
                        if reply.left.as_ref() == Some(&expected.left)
                            && reply.right.as_ref() == Some(&expected.right) =>
                    {
                        ("ok", None)
                    }
                    Ok(reply) => ("mismatch", Some(format!("wrong successful pair {reply:?}"))),
                    Err(e) => ("fail", Some(format!("{e:#}"))),
                };
                append(
                    &mut journal,
                    &json!({"kind":kind,"request":sequence,"id":id,"locality":locality,"started_ns":began,"ended_ns":now(),"elapsed_ns":t.elapsed().as_nanos(),"actual_left_sha256":actual_left,"actual_right_sha256":actual_right,"error":error}),
                )?;
                ensure!(
                    kind != "mismatch",
                    "successful read violated acknowledged body"
                );
            }
        }
        thread::sleep(Duration::from_millis(10));
    }
    journal.sync_all()?;
    Ok(())
}
