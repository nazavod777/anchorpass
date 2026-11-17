//! Bounded line-oriented public-API adapter for the loopback pressure driver.
use anchorpass::Client;
use serde::Deserialize;
use serde_json::{json, Value};
use std::{
    collections::BTreeMap,
    io::Write,
    time::{Duration, Instant},
};
use tokio::{
    io::{AsyncBufReadExt, BufReader},
    task::JoinSet,
};

#[derive(Deserialize)]
struct Fixture {
    ca_file: String,
    bundle_url: String,
    anchor_url: String,
    proxies: BTreeMap<String, String>,
    trusted_keys: BTreeMap<String, String>,
}
#[derive(Deserialize)]
struct Job {
    fixture: Fixture,
    cache_path: String,
}
#[derive(Default, Deserialize)]
#[serde(default)]
struct Command {
    op: String,
    offline: bool,
    proxy_route: String,
    bundle_url: Option<String>,
    refresh_interval: f64,
    timeout_ms: u64,
    max_in_flight: usize,
    count: usize,
    concurrency: usize,
    milliseconds: u64,
}

fn create(job: &Job, command: &Command) -> Result<Client, String> {
    if command.timeout_ms == 0
        || command.timeout_ms > 60000
        || !command.refresh_interval.is_finite()
        || !(0.0..=86400.0).contains(&command.refresh_interval)
    {
        return Err("invalid create limits".into());
    }
    let proxy = job
        .fixture
        .proxies
        .get(&command.proxy_route)
        .ok_or("unknown fixture route")?;
    let ca = std::fs::read(&job.fixture.ca_file).map_err(|_| "cannot read fixture CA")?;
    Client::builder()
        .proxy(proxy)
        .bundle_url(
            command
                .bundle_url
                .as_ref()
                .unwrap_or(&job.fixture.bundle_url),
        )
        .trusted_keys(job.fixture.trusted_keys.clone())
        .cache_path(&job.cache_path)
        .offline(command.offline)
        .timeout(Duration::from_millis(command.timeout_ms))
        .max_in_flight(command.max_in_flight)
        .refresh_interval(Duration::from_secs_f64(command.refresh_interval))
        .add_root_certificate(&ca)
        .map_err(|error| format!("{:?}", error.kind))?
        .build()
        .map_err(|error| format!("{:?}", error.kind))
}

async fn batch(client: &Client, job: &Job, command: &Command) -> Result<Vec<Value>, String> {
    if command.count == 0
        || command.count > 128
        || command.concurrency == 0
        || command.concurrency > 8
        || !matches!(command.op.as_str(), "load" | "refresh" | "solve")
    {
        return Err("invalid batch".into());
    }
    let mut tasks = JoinSet::new();
    let mut samples = vec![Value::Null; command.count];
    let mut next = 0;
    while next < command.count || !tasks.is_empty() {
        while next < command.count && tasks.len() < command.concurrency {
            let index = next;
            next += 1;
            let client = client.clone();
            let op = command.op.clone();
            let anchor = job.fixture.anchor_url.clone();
            tasks.spawn(async move {
                let started = Instant::now();
                let outcome = match op.as_str() {
                    "load" => client.load_bg().await,
                    "refresh" => client.refresh().await.map(|_| String::new()),
                    _ => client.solve(&anchor).await.map(|result| result.token),
                };
                let (value, error) = match outcome {
                    Ok(value) => (value, None),
                    Err(error) => (String::new(), Some(format!("{:?}", error.kind))),
                };
                (
                    index,
                    json!({"elapsed_ns": started.elapsed().as_nanos() as u64,
                    "error": error, "value": value}),
                )
            });
        }
        if let Some(result) = tasks.join_next().await {
            let (index, sample) = result.map_err(|_| "operation task failed")?;
            samples[index] = sample;
        }
    }
    Ok(samples)
}

async fn run() -> Result<(), String> {
    let path = std::env::args().nth(1).ok_or("expected job path")?;
    let raw = std::fs::read(path).map_err(|_| "cannot read job")?;
    if raw.len() > 1048576 {
        return Err("job limit exceeded".into());
    }
    let job: Job = serde_json::from_slice(&raw).map_err(|_| "invalid job JSON")?;
    let mut input = BufReader::new(tokio::io::stdin());
    let mut client = None;
    loop {
        let mut line = String::new();
        // Commands have a fixed small schema and a bounded line reader.
        let length = tokio::io::AsyncReadExt::take(&mut input, 131073)
            .read_line(&mut line)
            .await
            .map_err(|_| "cannot read command")?;
        if length == 0 {
            return Ok(());
        }
        if length > 131072 || !line.ends_with('\n') {
            return Err("command limit exceeded".into());
        }
        let command: Command = serde_json::from_str(&line).map_err(|_| "invalid command JSON")?;
        let reply = match command.op.as_str() {
            "create" => {
                drop(client.take());
                client = Some(create(&job, &command)?);
                json!({"ok": true})
            }
            "close" => {
                client = None;
                json!({"ok": true})
            }
            "settle" => {
                if command.milliseconds > 5000 {
                    return Err("invalid settle duration".into());
                }
                tokio::time::sleep(Duration::from_millis(command.milliseconds)).await;
                json!({"ok": true})
            }
            _ => {
                json!({"ok": true, "samples": batch(client.as_ref().ok_or("closed client")?, &job, &command).await?})
            }
        };
        println!("{reply}");
        std::io::stdout()
            .flush()
            .map_err(|_| "cannot flush reply")?;
    }
}

#[tokio::main(flavor = "current_thread")]
async fn main() {
    if let Err(detail) = run().await {
        println!("{}", json!({"ok": false, "detail": detail}));
        std::process::exit(1);
    }
}
