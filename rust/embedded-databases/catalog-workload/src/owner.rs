use crate::{
    engine::Engine,
    protocol::{Reply, Request},
};
use anyhow::{Result, ensure};
use std::{
    io::{BufRead, BufReader, Write},
    os::unix::net::UnixListener,
    path::Path,
    sync::{
        Arc,
        atomic::{AtomicBool, Ordering},
    },
    thread,
    time::Duration,
};

pub fn run(backend: &str, database: &Path, socket: &Path) -> Result<()> {
    let engine = Arc::new(Engine::open(backend, database)?);
    // Opening the engine first preserves its native exclusivity check before socket removal.
    if socket.exists() {
        std::fs::remove_file(socket)?;
    }
    let listener = UnixListener::bind(socket)?;
    listener.set_nonblocking(true)?;
    let stop = Arc::new(AtomicBool::new(false));
    let mut jobs = Vec::new();
    while !stop.load(Ordering::SeqCst) {
        match listener.accept() {
            Ok((mut stream, _)) => {
                let engine = Arc::clone(&engine);
                let stop = Arc::clone(&stop);
                jobs.push(thread::spawn(move || -> Result<()> {
                    stream.set_read_timeout(Some(Duration::from_secs(3)))?;
                    let mut line = String::new();
                    BufReader::new(stream.try_clone()?).read_line(&mut line)?;
                    let result = (|| -> Result<Reply> {
                        let request: Request = serde_json::from_str(&line)?;
                        let mut reply = Reply::default();
                        match request {
                            Request::Health => {}
                            Request::Snapshot { row } => engine.snapshot_control(&row)?,
                            Request::Put { rows } => {
                                ensure!(
                                    !rows.is_empty() && rows.len() <= 1000,
                                    "batch outside contract"
                                );
                                for row in &rows {
                                    ensure!(
                                        row.left.len() == 256 && row.right.len() == 256,
                                        "payload size"
                                    );
                                }
                                engine.put(&rows)?;
                            }
                            Request::Read { id } => {
                                (reply.left, reply.right) = engine.read(id)?;
                            }
                            Request::Delete { id } => engine.delete(id)?,
                            Request::Abort { row } => engine.abort_control(&row)?,
                            Request::Export { path } => {
                                reply.records = Some(engine.export(Path::new(&path))?)
                            }
                            Request::Stop => stop.store(true, Ordering::SeqCst),
                        }
                        Ok(reply)
                    })();
                    let reply = result.unwrap_or_else(|error| {
                        eprintln!("native_request_error: {error:#}");
                        Reply {
                            error: Some(format!("{error:#}")),
                            ..Reply::default()
                        }
                    });
                    let mut bytes = serde_json::to_vec(&reply)?;
                    bytes.push(b'\n');
                    stream.write_all(&bytes)?;
                    Ok(())
                }));
            }
            Err(e) if e.kind() == std::io::ErrorKind::WouldBlock => {
                thread::sleep(Duration::from_millis(1))
            }
            Err(e) => return Err(e.into()),
        }
        // Reap completed handlers so a long-lived owner does not accumulate handles.
        let mut index = 0;
        while index < jobs.len() {
            if jobs[index].is_finished() {
                finish_handler(jobs.swap_remove(index))?;
            } else {
                index += 1;
            }
        }
    }
    for job in jobs {
        finish_handler(job)?;
    }
    drop(listener);
    std::fs::remove_file(socket)?;
    drop(engine);
    Ok(())
}

fn finish_handler(job: thread::JoinHandle<Result<()>>) -> Result<()> {
    match job.join() {
        Ok(Ok(())) => Ok(()),
        Ok(Err(error)) => {
            if error.downcast_ref::<std::io::Error>().is_some_and(|io| {
                matches!(
                    io.kind(),
                    std::io::ErrorKind::BrokenPipe
                        | std::io::ErrorKind::ConnectionReset
                        | std::io::ErrorKind::UnexpectedEof
                        | std::io::ErrorKind::TimedOut
                        | std::io::ErrorKind::WouldBlock
                )
            }) {
                eprintln!("client_disconnected: {error:#}");
                Ok(())
            } else {
                Err(error.context("unexpected owner handler failure"))
            }
        }
        Err(_) => anyhow::bail!("owner handler panicked"),
    }
}
