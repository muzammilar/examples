mod actors;
mod engine;
mod metrics;
mod owner;
mod protocol;
mod report;
mod suite;

use anyhow::{Context, Result};
use std::{env, path::Path};
fn main() {
    if let Err(error) = run() {
        eprintln!("{error:#}");
        std::process::exit(1);
    }
}
fn run() -> Result<()> {
    let args: Vec<String> = env::args().collect();
    match args.get(1).map(String::as_str) {
        Some("record-teardown") => suite::record_teardown(),
        Some("report") => report::run(),
        Some("native-lock-probe") => suite::native_lock_probe(
            args.get(2).context("backend")?,
            Path::new(args.get(3).context("native database")?),
        ),
        Some("lock-probe") => suite::lock_probe(Path::new(args.get(2).context("lock path")?)),
        Some("owner") => owner::run(
            args.get(2).context("backend")?,
            Path::new(args.get(3).context("database")?),
            Path::new(args.get(4).context("socket")?),
        ),
        Some("writer" | "reader") => actors::actor(
            &args[1],
            Path::new(args.get(2).context("socket")?),
            Path::new(args.get(3).context("result directory")?),
            args.get(4).context("first ID")?.parse()?,
        ),
        Some(action) => suite::run(action),
        None => anyhow::bail!("usage: embedded-catalog-lab prepare|test|verify|archive"),
    }
}
