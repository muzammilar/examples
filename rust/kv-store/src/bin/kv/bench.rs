//! A load test: concurrent writes, then concurrent linearizable reads, with
//! throughput and latency percentiles for each.

use std::sync::Arc;
use std::time::Duration;

use kv_store::client::{Client, ClientError};
use tokio::task::JoinSet;
use tokio::time::Instant;

#[derive(Debug, Clone, Copy)]
pub(crate) struct Options {
    pub requests: u32,
    pub concurrency: u32,
    pub value_size: usize,
}

pub(crate) async fn run(client: Client, options: Options) -> Result<(), ClientError> {
    let client = Arc::new(client);
    let value: Arc<str> = "x".repeat(options.value_size).into();
    println!(
        "{} requests of each kind, {} in flight, {}-byte values",
        options.requests, options.concurrency, options.value_size
    );

    let writes = measure(options, {
        let client = Arc::clone(&client);
        move |i| {
            let (client, value) = (Arc::clone(&client), Arc::clone(&value));
            async move { client.put(&bench_key(i), &value).await }
        }
    })
    .await?;
    println!("{}", writes.summary("put"));

    let reads = measure(options, move |i| {
        let client = Arc::clone(&client);
        async move { client.get(&bench_key(i)).await.map(drop) }
    })
    .await?;
    println!("{}", reads.summary("get"));
    Ok(())
}

fn bench_key(i: u32) -> String {
    format!("bench-{i}")
}

/// Runs `op(0)` to `op(requests - 1)`, `concurrency` at a time, timing each.
async fn measure<F, Fut>(options: Options, op: F) -> Result<Report, ClientError>
where
    F: Fn(u32) -> Fut + Clone + Send + 'static,
    Fut: Future<Output = Result<(), ClientError>> + Send,
{
    let started = Instant::now();
    let mut workers = JoinSet::new();
    for worker in 0..options.concurrency {
        let op = op.clone();
        workers.spawn(async move {
            let mut latencies = Vec::new();
            for i in (worker..options.requests).step_by(options.concurrency as usize) {
                let sent = Instant::now();
                op(i).await?;
                latencies.push(sent.elapsed());
            }
            Ok::<_, ClientError>(latencies)
        });
    }

    let mut latencies = Vec::with_capacity(options.requests as usize);
    while let Some(result) = workers.join_next().await {
        latencies.extend(result.expect("bench workers don't panic")?);
    }
    Ok(Report::new(latencies, started.elapsed()))
}

#[derive(Debug)]
struct Report {
    /// Sorted, fastest first.
    latencies: Vec<Duration>,
    elapsed: Duration,
}

impl Report {
    fn new(mut latencies: Vec<Duration>, elapsed: Duration) -> Self {
        latencies.sort_unstable();
        Self { latencies, elapsed }
    }

    #[expect(
        clippy::cast_precision_loss,
        reason = "request counts are far below 2^52"
    )]
    fn throughput(&self) -> f64 {
        self.latencies.len() as f64 / self.elapsed.as_secs_f64()
    }

    /// Latency at or below which `percent` of requests completed, by the
    /// nearest-rank method.
    fn percentile(&self, percent: usize) -> Duration {
        let rank = (self.latencies.len() * percent).div_ceil(100);
        (self.latencies)
            .get(rank.saturating_sub(1))
            .copied()
            .unwrap_or_default()
    }

    fn summary(&self, name: &str) -> String {
        let ms = |d: Duration| format!("{:.1}ms", d.as_secs_f64() * 1000.0);
        format!(
            "{name:<4} {:>8.0} ops/s   p50 {:>7}   p95 {:>7}   p99 {:>7}   max {:>7}",
            self.throughput(),
            ms(self.percentile(50)),
            ms(self.percentile(95)),
            ms(self.percentile(99)),
            ms(self.percentile(100)),
        )
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn report(millis: impl IntoIterator<Item = u64>) -> Report {
        let latencies = millis.into_iter().map(Duration::from_millis).collect();
        Report::new(latencies, Duration::from_secs(1))
    }

    #[test]
    fn percentiles_pick_from_sorted_latencies() {
        let report = report((1..=100).rev());
        assert_eq!(report.percentile(50), Duration::from_millis(50));
        assert_eq!(report.percentile(99), Duration::from_millis(99));
        assert_eq!(report.percentile(100), Duration::from_millis(100));
        assert_eq!(report.percentile(0), Duration::from_millis(1));
    }

    #[test]
    fn single_sample_is_every_percentile() {
        let report = report([7]);
        assert_eq!(report.percentile(50), Duration::from_millis(7));
        assert_eq!(report.percentile(99), Duration::from_millis(7));
    }

    #[test]
    fn empty_report_is_zero() {
        assert_eq!(report([]).percentile(99), Duration::ZERO);
        assert!(report([]).throughput().abs() < f64::EPSILON);
    }

    #[test]
    fn throughput_is_requests_per_second() {
        let report = Report::new(
            vec![Duration::from_millis(1); 500],
            Duration::from_millis(250),
        );
        assert!((report.throughput() - 2000.0).abs() < f64::EPSILON);
    }
}
