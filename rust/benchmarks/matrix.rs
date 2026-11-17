//! Loopback-only transport matrix adapter. See ../../benchmarks/CONTRACT.md.
mod flow;
use isahc::config::Configurable;
use serde::{Deserialize, Serialize};
use serde_json::json;
use std::{
    collections::BTreeMap,
    error::Error,
    fs,
    io::Read,
    path::Path,
    time::{Duration, Instant},
};
type AnyError = Box<dyn Error + Send + Sync>;
#[derive(Clone, Deserialize)]
struct Job {
    schema_version: u32,
    run_id: String,
    candidate: String,
    fixture: Fixture,
    profile: Profile,
}
#[derive(Clone, Deserialize)]
struct Fixture {
    https_url: String,
    bad_target_url: String,
    ca_file: String,
    proxy_ca_file: String,
    proxies: BTreeMap<String, String>,
    anchor_url: String,
    bundle_url: String,
    trusted_keys: BTreeMap<String, String>,
    #[serde(default)]
    flow_fields: BTreeMap<String, String>,
}
#[derive(Clone, Deserialize)]
struct Profile {
    kind: String,
    #[serde(default)]
    flow_delay_ms: u64,
    route: String,
    connection_mode: String,
    concurrency: usize,
    warmup: usize,
    requests: usize,
    timeout_ms: u64,
    max_response_bytes: usize,
    path: String,
    expected_bytes: usize,
    expected_error: Option<String>,
    recovery_requests: usize,
}
#[derive(Serialize)]
struct Sample {
    #[serde(skip_serializing_if = "Option::is_none")]
    detail: Option<String>,
    elapsed_ns: u64,
    ok: bool,
    error: Option<String>,
    bytes: usize,
}
#[derive(Clone)]
struct Failure(&'static str, Option<String>);
impl Failure {
    fn new(category: &'static str) -> Self {
        Self(category, None)
    }
    fn detail<E: Error>(mut self, error: &E) -> Self {
        let mut messages = vec![error.to_string()];
        let mut source = error.source();
        while let Some(cause) = source {
            messages.push(cause.to_string());
            source = cause.source();
            if messages.len() >= 5 {
                break;
            }
        }
        self.1 = Some(
            format!("{}: {}", std::any::type_name::<E>(), messages.join(": "))
                .chars()
                .take(300)
                .collect(),
        );
        self
    }
}
type Operation = Result<Vec<u8>, Failure>;
fn duration(job: &Job) -> Duration {
    Duration::from_millis(job.profile.timeout_ms)
}
fn proxy(job: &Job) -> Result<Option<&str>, AnyError> {
    let route = match job.profile.route.as_str() {
        "direct" => return Ok(None),
        "bad_target" => "https",
        route @ ("http" | "https" | "socks5" | "socks5h" | "bad_auth" | "bad_tls") => route,
        _ => return Err("unknown route".into()),
    };
    let value = job
        .fixture
        .proxies
        .get(route)
        .filter(|value| !value.is_empty())
        .ok_or("missing selected proxy route")?;
    Ok(Some(value.as_str()))
}
fn target(job: &Job) -> String {
    format!(
        "{}{}",
        if job.profile.route == "bad_target" {
            &job.fixture.bad_target_url
        } else {
            &job.fixture.https_url
        },
        job.profile.path
    )
}
fn status(code: u16) -> Result<(), Failure> {
    if code == 407 {
        Err(Failure::new("proxy"))
    } else if !(200..300).contains(&code) {
        Err(Failure::new("http_status"))
    } else {
        Ok(())
    }
}
fn read_body(mut reader: impl Read, limit: usize) -> Operation {
    let mut body = Vec::new();
    let mut chunk = [0; 8192];
    loop {
        let count = reader.read(&mut chunk).map_err(io_failure)?;
        if count == 0 {
            return Ok(body);
        }
        if count > limit.saturating_sub(body.len()) {
            return Err(Failure::new("response_limit"));
        }
        body.extend_from_slice(&chunk[..count]);
    }
}
fn network_io_category(kind: std::io::ErrorKind) -> Option<&'static str> {
    use std::io::ErrorKind;
    match kind {
        ErrorKind::TimedOut | ErrorKind::WouldBlock => Some("deadline"),
        ErrorKind::ConnectionRefused
        | ErrorKind::ConnectionReset
        | ErrorKind::ConnectionAborted
        | ErrorKind::NotConnected
        | ErrorKind::BrokenPipe
        | ErrorKind::UnexpectedEof
        | ErrorKind::HostUnreachable
        | ErrorKind::NetworkUnreachable
        | ErrorKind::NetworkDown => Some("transport"),
        _ => None,
    }
}
fn source_category(mut source: Option<&(dyn Error + 'static)>) -> Option<&'static str> {
    // A finite chain walk must not treat arbitrary error text as a network error.
    for _ in 0..16 {
        let error = source?;
        if let Some(io) = error.downcast_ref::<std::io::Error>() {
            if let Some(category) = network_io_category(io.kind()) {
                return Some(category);
            }
        }
        if let Some(hyper) = error.downcast_ref::<hyper::Error>() {
            if hyper.is_timeout() {
                return Some("deadline");
            }
            if !hyper.is_user()
                && (hyper.is_closed() || hyper.is_incomplete_message() || hyper.is_parse())
            {
                return Some("transport");
            }
        }
        if error.downcast_ref::<rustls::Error>().is_some() {
            return Some("tls");
        }
        if let Some(curl) = error.downcast_ref::<curl::Error>() {
            if curl.is_operation_timedout() {
                return Some("deadline");
            }
            if curl.is_recv_error()
                || curl.is_send_error()
                || curl.is_got_nothing()
                || curl.is_partial_file()
                || curl.is_couldnt_connect()
                || curl.is_couldnt_resolve_host()
                || curl.is_couldnt_resolve_proxy()
            {
                return Some("transport");
            }
        }
        source = error.source();
    }
    None
}
fn io_failure(error: std::io::Error) -> Failure {
    let category = if let Some(category) = network_io_category(error.kind()) {
        category
    } else if let Some(inner) = error
        .get_ref()
        .and_then(|e| e.downcast_ref::<ureq::Error>())
    {
        return ureq_failure(inner).detail(&error);
    } else if let Some(inner) = error
        .get_ref()
        .and_then(|e| e.downcast_ref::<isahc::Error>())
    {
        return isahc_failure(inner).detail(&error);
    } else if let Some(inner) = error
        .get_ref()
        .and_then(|e| e.downcast_ref::<reqwest::Error>())
    {
        return reqwest_category(inner).detail(&error);
    } else {
        source_category(error.source()).unwrap_or("unclassified")
    };
    Failure::new(category).detail(&error)
}
fn reqwest_category(error: &reqwest::Error) -> Failure {
    if error.is_builder() || error.is_redirect() {
        return Failure::new("unclassified");
    }
    if error.is_timeout() {
        return Failure::new("deadline");
    }
    // TunnelError is private in hyper-util 0.1.21. Match its exact auth variant
    // display only inside a typed reqwest connect error, never arbitrary substrings.
    if error.is_connect() {
        let mut source = error.source();
        for _ in 0..16 {
            let Some(cause) = source else { break };
            if cause.to_string() == "tunnel error: proxy authorization required" {
                return Failure::new("proxy");
            }
            source = cause.source();
        }
    }
    if let Some(category) = source_category(error.source()) {
        return Failure::new(category);
    }
    Failure::new("unclassified")
}
fn reqwest_failure(error: reqwest::Error) -> Failure {
    reqwest_category(&error).detail(&error)
}
fn isahc_failure(error: &isahc::Error) -> Failure {
    use isahc::error::ErrorKind;
    let category = match error.kind() {
        ErrorKind::Timeout => "deadline",
        ErrorKind::BadServerCertificate | ErrorKind::TlsEngine => "tls",
        ErrorKind::InvalidCredentials => "proxy",
        ErrorKind::ConnectionFailed | ErrorKind::NameResolution | ErrorKind::ProtocolViolation => {
            "transport"
        }
        ErrorKind::Io | ErrorKind::Unknown => {
            source_category(error.source()).unwrap_or("unclassified")
        }
        _ => "unclassified",
    };
    Failure::new(category)
}
fn ureq_failure(error: &ureq::Error) -> Failure {
    let category = match error {
        ureq::Error::Timeout(_) => "deadline",
        ureq::Error::Rustls(_) => "tls",
        ureq::Error::StatusCode(407) => "proxy",
        ureq::Error::ConnectProxyFailed(reason)
            if reason.starts_with("proxy server responded 407/") =>
        {
            "proxy"
        }
        ureq::Error::StatusCode(_) => "http_status",
        ureq::Error::BodyExceedsLimit(_) | ureq::Error::LargeResponseHeader(_, _) => {
            "response_limit"
        }
        ureq::Error::Io(io) => network_io_category(io.kind())
            .or_else(|| source_category(io.source()))
            .unwrap_or("unclassified"),
        ureq::Error::Protocol(_) | ureq::Error::HostNotFound | ureq::Error::ConnectionFailed => {
            "transport"
        }
        _ => "unclassified",
    };
    Failure::new(category)
}
fn sample(job: &Job, start: Instant, outcome: Operation) -> Sample {
    let elapsed_ns = start.elapsed().as_nanos() as u64;
    let (error, bytes, valid, detail) = match outcome {
        Ok(body) => {
            let valid = if matches!(job.profile.kind.as_str(), "api" | "flow") {
                body == b"fixture-token"
            } else {
                body.len() == job.profile.expected_bytes && body.iter().all(|&b| b == b'x')
            };
            (None, body.len(), valid, None)
        }
        Err(Failure(e, detail)) => (Some(e.to_owned()), 0, true, detail),
    };
    let within = error.as_deref() != Some("deadline")
        || start.elapsed() <= duration(job) + Duration::from_millis(100);
    Sample {
        detail,
        elapsed_ns,
        ok: valid
            && within
            && error.as_deref() != Some("unclassified")
            && error == job.profile.expected_error,
        error,
        bytes,
    }
}
fn async_http(job: &Job) -> Result<reqwest::Client, AnyError> {
    let selected_proxy = proxy(job)?;
    let mut builder = reqwest::Client::builder()
        .no_proxy()
        .redirect(reqwest::redirect::Policy::none())
        .retry(reqwest::retry::never())
        .timeout(duration(job))
        .pool_max_idle_per_host(job.profile.concurrency)
        .add_root_certificate(reqwest::Certificate::from_pem(&fs::read(
            &job.fixture.ca_file,
        )?)?);
    if let Some(p) = selected_proxy {
        builder = builder.proxy(reqwest::Proxy::all(p)?)
    }
    Ok(builder.build()?)
}
fn blocking_http(job: &Job) -> Result<reqwest::blocking::Client, AnyError> {
    let selected_proxy = proxy(job)?;
    let mut builder = reqwest::blocking::Client::builder()
        .no_proxy()
        .redirect(reqwest::redirect::Policy::none())
        .retry(reqwest::retry::never())
        .timeout(duration(job))
        .add_root_certificate(reqwest::Certificate::from_pem(&fs::read(
            &job.fixture.ca_file,
        )?)?);
    if let Some(p) = selected_proxy {
        builder = builder.proxy(reqwest::Proxy::all(p)?)
    }
    Ok(builder.build()?)
}
fn api_builder(
    job: &Job,
    cache: &Path,
    offline: bool,
) -> Result<anchorpass::ClientBuilder, AnyError> {
    Ok(anchorpass::Client::builder()
        .proxy(proxy(job)?.ok_or("missing selected API proxy")?)
        .timeout(duration(job))
        .update_timeout(duration(job))
        .max_in_flight(job.profile.concurrency)
        .max_response_bytes(job.profile.max_response_bytes)
        .cache_path(cache)
        .bundle_url(&job.fixture.bundle_url)
        .trusted_keys(job.fixture.trusted_keys.clone())
        .offline(offline)
        .add_root_certificate(&fs::read(&job.fixture.ca_file)?)?
        .add_proxy_root_certificate(&fs::read(&job.fixture.proxy_ca_file)?)?)
}
fn blocking_api_builder(
    job: &Job,
    cache: &Path,
    offline: bool,
) -> Result<anchorpass::blocking::ClientBuilder, AnyError> {
    Ok(anchorpass::blocking::Client::builder()
        .proxy(proxy(job)?.ok_or("missing selected API proxy")?)
        .timeout(duration(job))
        .update_timeout(duration(job))
        .max_in_flight(job.profile.concurrency)
        .max_response_bytes(job.profile.max_response_bytes)
        .cache_path(cache)
        .bundle_url(&job.fixture.bundle_url)
        .trusted_keys(job.fixture.trusted_keys.clone())
        .offline(offline)
        .add_root_certificate(&fs::read(&job.fixture.ca_file)?)?
        .add_proxy_root_certificate(&fs::read(&job.fixture.proxy_ca_file)?)?)
}
fn api_failure(error: anchorpass::Error) -> Failure {
    use anchorpass::ErrorKind;
    match error.kind {
        ErrorKind::DeadlineExceeded => Failure::new("deadline"),
        ErrorKind::ProxyAuthError | ErrorKind::ProxyConnectError => Failure::new("proxy"),
        ErrorKind::HttpStatusError => Failure::new("http_status"),
        ErrorKind::ResourceLimit if matches!(error.stage, "anchor" | "reload" | "version") => {
            Failure::new("response_limit")
        }
        ErrorKind::TransportError => Failure::new("transport"),
        _ => Failure::new("unclassified"),
    }
}
enum SyncClient {
    Reqwest(reqwest::blocking::Client),
    Isahc(isahc::HttpClient),
    Ureq(ureq::Agent),
    Api(anchorpass::blocking::Client),
}
fn ureq_proxy(job: &Job) -> Result<Option<ureq::Proxy>, AnyError> {
    let Some(value) = proxy(job)? else {
        return Ok(None);
    };
    if matches!(job.profile.route.as_str(), "socks5" | "socks5h") {
        let url = reqwest::Url::parse(value)?;
        let mut builder = ureq::Proxy::builder(ureq::ProxyProtocol::Socks5)
            .host(url.host_str().ok_or("proxy hostname absent")?)
            .port(url.port().ok_or("proxy port absent")?)
            .resolve_target(job.profile.route == "socks5");
        if !url.username().is_empty() {
            builder = builder.username(url.username());
        }
        if let Some(password) = url.password() {
            builder = builder.password(password);
        }
        return Ok(Some(builder.build()?));
    }
    Ok(Some(ureq::Proxy::new(value)?))
}
fn sync_client(job: &Job, cache: &Path) -> Result<SyncClient, AnyError> {
    proxy(job)?;
    Ok(match job.candidate.as_str() {
        "rust-reqwest-blocking" => SyncClient::Reqwest(blocking_http(job)?),
        "rust-isahc" => {
            let client = isahc::HttpClient::builder()
                .proxy(proxy(job)?.map(str::parse).transpose()?)
                .proxy_blacklist(std::iter::empty::<String>())
                .timeout(duration(job))
                .redirect_policy(isahc::config::RedirectPolicy::None)
                .automatic_decompression(true)
                .ssl_ca_certificate(isahc::config::CaCertificate::file(&job.fixture.ca_file))
                .build()?;
            SyncClient::Isahc(client)
        }
        "rust-ureq" => {
            let pem = fs::read(&job.fixture.ca_file)?;
            let tls = ureq::tls::TlsConfig::builder()
                .root_certs(ureq::tls::RootCerts::new_with_certs(&[
                    ureq::tls::Certificate::from_pem(&pem)?,
                ]))
                .build();
            let conf = ureq::Agent::config_builder()
                .proxy(ureq_proxy(job)?)
                .tls_config(tls)
                .timeout_global(Some(duration(job)))
                .max_redirects(0)
                .http_status_as_error(false)
                .build();
            SyncClient::Ureq(ureq::Agent::new_with_config(conf))
        }
        "rust-api-blocking" => SyncClient::Api(blocking_api_builder(job, cache, true)?.build()?),
        _ => return Err("unknown sync candidate".into()),
    })
}
impl SyncClient {
    fn operation(&self, job: &Job) -> Operation {
        if job.profile.kind == "flow" {
            return flow::sync(self, job);
        }
        match self {
            Self::Reqwest(client) => {
                let response = client
                    .get(target(job))
                    .timeout(duration(job))
                    .send()
                    .map_err(reqwest_failure)?;
                status(response.status().as_u16())?;
                read_body(response, job.profile.max_response_bytes)
            }
            Self::Isahc(client) => {
                let mut response = client
                    .get(target(job))
                    .map_err(|e| isahc_failure(&e).detail(&e))?;
                status(response.status().as_u16())?;
                read_body(response.body_mut(), job.profile.max_response_bytes)
            }
            Self::Ureq(client) => {
                let mut response = client
                    .get(target(job))
                    .call()
                    .map_err(|e| ureq_failure(&e).detail(&e))?;
                status(response.status().as_u16())?;
                read_body(
                    response.body_mut().as_reader(),
                    job.profile.max_response_bytes,
                )
            }
            Self::Api(client) => client
                .solve(&job.fixture.anchor_url)
                .map(|v| v.token.into_bytes())
                .map_err(api_failure),
        }
    }
}
fn recovery_job(job: &Job) -> Job {
    let mut next = job.clone();
    next.profile.path = "/bytes?size=1024".into();
    next.profile.expected_bytes = 1024;
    next.profile.expected_error = None;
    if matches!(
        next.profile.route.as_str(),
        "bad_auth" | "bad_tls" | "bad_target"
    ) {
        next.profile.route = "http".into()
    }
    next
}
#[derive(Clone, Copy)]
struct BatchMetrics {
    wall_ns: u64,
    cpu_ns: u64,
}
type BatchResult = (Vec<Sample>, Vec<Sample>, usize, BatchMetrics);
fn sync_selected(job: &Job, client: &SyncClient, cache: &Path) -> Result<Operation, AnyError> {
    if job.profile.connection_mode == "cold" {
        let fresh = sync_client(job, cache)?;
        let outcome = fresh.operation(job);
        drop(fresh);
        Ok(outcome)
    } else {
        Ok(client.operation(job))
    }
}
fn sync_run(job: &Job) -> Result<BatchResult, AnyError> {
    let count = job.profile.concurrency;
    let scratch = tempfile::tempdir()?;
    let ready = std::sync::Barrier::new(count + 1);
    let start_batch = std::sync::Barrier::new(count + 1);
    let done = std::sync::Barrier::new(count + 1);
    let recover = std::sync::Barrier::new(count + 1);
    let (workers, metrics) = std::thread::scope(|scope| {
        let mut workers = Vec::new();
        for index in 0..count {
            let cache = scratch.path().join(format!("worker-{index}.json"));
            let (ready, start_batch, done, recover) = (&ready, &start_batch, &done, &recover);
            workers.push(scope.spawn(move || -> Result<_, AnyError> {
                let prepared = (|| -> Result<_, AnyError> {
                    if job.profile.kind == "api" {
                        let client = blocking_api_builder(job, &cache, false)?.build()?;
                        client.refresh()?;
                    }
                    let client = sync_client(job, &cache)?;
                    let mut warmup = 0;
                    for _ in (index..job.profile.warmup).step_by(count) {
                        let start = Instant::now();
                        if !sample(job, start, sync_selected(job, &client, &cache)?).ok {
                            return Err("warmup failed".into());
                        }
                        warmup += 1;
                    }
                    Ok((client, warmup))
                })();
                // Even failed preparation must rendezvous so peers cannot deadlock.
                ready.wait();
                start_batch.wait();
                let measured = prepared.and_then(|(client, warmup)| -> Result<_, AnyError> {
                    let mut samples = Vec::new();
                    for _ in (index..job.profile.requests).step_by(count) {
                        let start = Instant::now();
                        samples.push(sample(job, start, sync_selected(job, &client, &cache)?));
                    }
                    Ok((client, samples, warmup))
                });
                done.wait();
                recover.wait();
                let (mut client, samples, warmup) = measured?;
                let recovery = recovery_job(job);
                if recovery.profile.route != job.profile.route {
                    client = sync_client(&recovery, &cache)?;
                }
                let mut recovered = Vec::new();
                for _ in (index..job.profile.recovery_requests).step_by(count) {
                    let start = Instant::now();
                    recovered.push(sample(&recovery, start, client.operation(&recovery)));
                }
                drop(client);
                Ok((samples, recovered, warmup))
            }));
        }
        ready.wait();
        let wall = Instant::now();
        let cpu = cpu_ns();
        start_batch.wait();
        done.wait();
        let metrics = BatchMetrics {
            wall_ns: wall.elapsed().as_nanos() as u64,
            cpu_ns: cpu_ns() - cpu,
        };
        recover.wait();
        let results = workers
            .into_iter()
            .map(|worker| {
                worker
                    .join()
                    .map_err(|_| -> AnyError { "worker panicked".into() })?
            })
            .collect::<Result<Vec<_>, _>>();
        results.map(|workers| (workers, metrics))
    })?;
    let mut samples = Vec::new();
    let mut recovery = Vec::new();
    let mut warmup = 0;
    for (s, r, w) in workers {
        samples.extend(s);
        recovery.extend(r);
        warmup += w;
    }
    Ok((samples, recovery, warmup, metrics))
}
async fn async_operation(
    job: &Job,
    http: &reqwest::Client,
    api: Option<&anchorpass::Client>,
) -> Operation {
    if let Some(api) = api {
        return api
            .solve(&job.fixture.anchor_url)
            .await
            .map(|v| v.token.into_bytes())
            .map_err(api_failure);
    }
    if job.profile.kind == "flow" {
        return flow::asynchronous(http, job).await;
    }
    let operation = async {
        let mut response = http
            .get(target(job))
            .send()
            .await
            .map_err(reqwest_failure)?;
        status(response.status().as_u16())?;
        let mut body = Vec::new();
        while let Some(chunk) = response.chunk().await.map_err(reqwest_failure)? {
            if chunk.len() > job.profile.max_response_bytes.saturating_sub(body.len()) {
                return Err(Failure::new("response_limit"));
            }
            body.extend_from_slice(&chunk);
        }
        Ok(body)
    };
    tokio::time::timeout(duration(job), operation)
        .await
        .unwrap_or(Err(Failure::new("deadline")))
}
async fn async_selected(
    job: &Job,
    http: &reqwest::Client,
    api: Option<&anchorpass::Client>,
    cache: &Path,
) -> Result<Operation, AnyError> {
    if job.profile.connection_mode == "warm" {
        return Ok(async_operation(job, http, api).await);
    }
    let fresh = if api.is_none() {
        Some(async_http(job)?)
    } else {
        None
    };
    let fresh_api = if api.is_some() {
        Some(api_builder(job, cache, true)?.build()?)
    } else {
        None
    };
    let result = async_operation(job, fresh.as_ref().unwrap_or(http), fresh_api.as_ref()).await;
    drop(fresh_api);
    drop(fresh);
    Ok(result)
}
async fn async_run(job: &Job) -> Result<BatchResult, AnyError> {
    let scratch = tempfile::tempdir()?;
    let cache = scratch.path().join("cache.json");
    let http = async_http(job)?;
    let api = if job.profile.kind == "api" {
        let prepared = api_builder(job, &cache, false)?.build()?;
        prepared.refresh().await?;
        Some(api_builder(job, &cache, true)?.build()?)
    } else {
        None
    };
    let count = job.profile.concurrency;
    let ready = std::sync::Arc::new(tokio::sync::Barrier::new(count + 1));
    let start_batch = std::sync::Arc::new(tokio::sync::Barrier::new(count + 1));
    let done = std::sync::Arc::new(tokio::sync::Barrier::new(count + 1));
    let recover = std::sync::Arc::new(tokio::sync::Barrier::new(count + 1));
    let mut tasks = tokio::task::JoinSet::new();
    for index in 0..count {
        let job = job.clone();
        let http = http.clone();
        let api = api.clone();
        let cache = cache.clone();
        let (ready, start_batch, done, recover) = (
            ready.clone(),
            start_batch.clone(),
            done.clone(),
            recover.clone(),
        );
        tasks.spawn(async move {
            let warmed = async {
                let mut warmup = 0;
                for _ in (index..job.profile.warmup).step_by(count) {
                    let start = Instant::now();
                    if !sample(
                        &job,
                        start,
                        async_selected(&job, &http, api.as_ref(), &cache).await?,
                    )
                    .ok
                    {
                        return Err::<_, AnyError>("warmup failed".into());
                    }
                    warmup += 1;
                }
                Ok(warmup)
            }
            .await;
            ready.wait().await;
            start_batch.wait().await;
            let measured = async {
                let warmup = warmed?;
                let mut samples = Vec::new();
                for _ in (index..job.profile.requests).step_by(count) {
                    let start = Instant::now();
                    samples.push(sample(
                        &job,
                        start,
                        async_selected(&job, &http, api.as_ref(), &cache).await?,
                    ));
                }
                Ok::<_, AnyError>((samples, warmup))
            }
            .await;
            done.wait().await;
            recover.wait().await;
            let (samples, warmup) = measured?;
            let recovery = recovery_job(&job);
            let recovery_http = if recovery.profile.route != job.profile.route {
                async_http(&recovery)?
            } else {
                http.clone()
            };
            let mut recovered = Vec::new();
            for _ in (index..job.profile.recovery_requests).step_by(count) {
                let start = Instant::now();
                recovered.push(sample(
                    &recovery,
                    start,
                    async_operation(&recovery, &recovery_http, api.as_ref()).await,
                ));
            }
            Ok::<_, AnyError>((samples, recovered, warmup))
        });
    }
    ready.wait().await;
    let wall = Instant::now();
    let cpu = cpu_ns();
    start_batch.wait().await;
    done.wait().await;
    let metrics = BatchMetrics {
        wall_ns: wall.elapsed().as_nanos() as u64,
        cpu_ns: cpu_ns() - cpu,
    };
    recover.wait().await;
    let mut samples = Vec::new();
    let mut recovery = Vec::new();
    let mut warmup = 0;
    while let Some(result) = tasks.join_next().await {
        let (s, r, w) = result??;
        samples.extend(s);
        recovery.extend(r);
        warmup += w;
    }
    drop(api);
    drop(http);
    Ok((samples, recovery, warmup, metrics))
}
fn unsupported(job: &Job) -> Option<&'static str> {
    if job.profile.kind == "api" {
        return if matches!(job.profile.route.as_str(), "http" | "https") {
            None
        } else {
            Some("API benchmark profiles currently exercise successful HTTP/HTTPS CONNECT routes; independent TLS fault controls are covered by crate tests.")
        };
    }
    if job.candidate == "rust-isahc" && job.profile.route == "bad_auth" {
        return Some("isahc 1.8.3 exposes CONNECT auth rejection as generic ConnectionFailed/curl code 7 in this adapter; it cannot establish the required proxy error category without guessing.");
    }
    if matches!(
        job.profile.route.as_str(),
        "https" | "bad_tls" | "bad_target"
    ) {
        return Some("This adapter does not expose independent target/proxy CA stores; nested TLS routes are not benchmarked with union trust.");
    }
    None
}
fn counts(name: &str) -> Option<usize> {
    fs::read_dir(format!("/proc/self/{name}"))
        .ok()
        .map(Iterator::count)
}
fn cpu_ns() -> u64 {
    let mut time = libc::timespec {
        tv_sec: 0,
        tv_nsec: 0,
    };
    unsafe { libc::clock_gettime(libc::CLOCK_PROCESS_CPUTIME_ID, &mut time) };
    time.tv_sec as u64 * 1_000_000_000 + time.tv_nsec as u64
}
fn is_loopback_host(url: &reqwest::Url) -> bool {
    url.host_str().is_some_and(|host| {
        host == "localhost"
            || host
                .trim_matches(['[', ']'])
                .parse::<std::net::IpAddr>()
                .is_ok_and(|ip| ip.is_loopback())
    })
}
fn runtime_version() -> String {
    std::process::Command::new("rustc")
        .arg("--version")
        .output()
        .ok()
        .filter(|value| value.status.success())
        .map(|value| String::from_utf8_lossy(&value.stdout).trim().to_owned())
        .unwrap_or_else(|| "Rust (compiler version unavailable)".into())
}
fn run() -> Result<(), AnyError> {
    let args: Vec<_> = std::env::args().collect();
    if args.len() != 5 || args[1] != "--job" || args[3] != "--output" {
        return Err("usage: transport-matrix --job PATH --output PATH".into());
    }
    let job: Job = serde_json::from_slice(&fs::read(&args[2])?)?;
    proxy(&job)?;
    if job.schema_version != 1
        || job.profile.concurrency == 0
        || job.profile.concurrency > 1024
        || job.profile.timeout_ms == 0
        || job.profile.timeout_ms > 3_600_000
        || job.profile.max_response_bytes == 0
        || job.profile.max_response_bytes > 16_777_216
        || job.profile.requests > 1_000_000
        || job.profile.warmup > 1_000_000
        || job.profile.recovery_requests > 1_000_000
    {
        return Err("invalid job limits".into());
    }
    if ![
        "rust-reqwest-async",
        "rust-reqwest-blocking",
        "rust-isahc",
        "rust-ureq",
        "rust-api-async",
        "rust-api-blocking",
    ]
    .contains(&job.candidate.as_str())
    {
        return Err("unknown candidate".into());
    }
    if !["warm", "cold"].contains(&job.profile.connection_mode.as_str())
        || !["transport", "api", "flow"].contains(&job.profile.kind.as_str())
        || job.candidate.starts_with("rust-api-") != (job.profile.kind == "api")
    {
        return Err("candidate/profile mismatch".into());
    }
    for value in [
        &job.fixture.https_url,
        &job.fixture.bad_target_url,
        &job.fixture.bundle_url,
    ] {
        let url = reqwest::Url::parse(value)?;
        if !matches!(url.scheme(), "https" | "http") || !is_loopback_host(&url) {
            return Err("fixture URLs must use loopback".into());
        }
    }
    for value in job.fixture.proxies.values() {
        if !is_loopback_host(&reqwest::Url::parse(value)?) {
            return Err("fixture proxies must use loopback".into());
        }
    }
    if job.profile.kind == "api" && cfg!(feature = "transport-gzip") {
        return Err("API candidates require transport-matrix-api built from benchmarks/api/Cargo.toml without transport gzip feature unification".into());
    }
    let mut output = json!({"schema_version":1,"run_id":job.run_id,"candidate":job.candidate,"runtime":runtime_version(),"versions":{"reqwest":"0.12.28","isahc":"1.8.3","ureq":"3.1.4","anchorpass":"0.1.1"},"mode":if job.candidate.ends_with("async"){"native-async"}else{"bounded-threads"},"samples":[],"recovery":[],"notes":["Bounded worker pool; all warmups finish before measured batch. wall_ns/cpu_ns cover only timed batch, including start/end barrier overhead; CPU uses CLOCK_PROCESS_CPUTIME_ID for all process threads. Recovery/cleanup start after metric snapshots.", "API cold clients read a fresh prepared persistent snapshot offline; construction/cache read/cleanup are timed.", "Transport binary enables reqwest gzip; API binary uses an independent manifest without that feature."],"warmup_completed":0});
    if let Some(reason) = unsupported(&job) {
        output["status"] = json!("unsupported");
        output["reason"] = json!(reason);
    } else {
        let before = (counts("fd"), counts("task"));
        let (samples, recovery, warmup, metrics) = if job.candidate.ends_with("async") {
            let runtime = tokio::runtime::Builder::new_current_thread()
                .enable_all()
                .max_blocking_threads(job.profile.concurrency)
                .build()?;
            let result = runtime.block_on(async_run(&job))?;
            drop(runtime);
            result
        } else {
            sync_run(&job)?
        };
        output["status"] = json!("ok");
        output["samples"] = serde_json::to_value(samples)?;
        output["recovery"] = serde_json::to_value(recovery)?;
        output["warmup_completed"] = json!(warmup);
        output["wall_ns"] = json!(metrics.wall_ns);
        output["cpu_ns"] = json!(metrics.cpu_ns);
        output["resources"] = json!({"fds_before":before.0,"fds_after":counts("fd"),"threads_before":before.1,"threads_after":counts("task")});
    }
    fs::write(&args[4], serde_json::to_vec_pretty(&output)?)?;
    Ok(())
}
fn main() {
    if let Err(error) = run() {
        eprintln!("matrix harness failed: {error}");
        std::process::exit(1)
    }
}

#[cfg(test)]
mod classification_tests {
    use super::*;

    #[test]
    fn unknown_io_errors_do_not_satisfy_transport_controls() {
        for kind in [
            std::io::ErrorKind::Other,
            std::io::ErrorKind::InvalidInput,
            std::io::ErrorKind::InvalidData,
            std::io::ErrorKind::PermissionDenied,
        ] {
            assert_eq!(
                io_failure(std::io::Error::new(kind, "adapter programming mistake")).0,
                "unclassified"
            );
        }
        for kind in [
            std::io::ErrorKind::ConnectionReset,
            std::io::ErrorKind::ConnectionRefused,
            std::io::ErrorKind::UnexpectedEof,
            std::io::ErrorKind::BrokenPipe,
        ] {
            assert_eq!(
                io_failure(std::io::Error::new(kind, "network fault")).0,
                "transport"
            );
        }
        assert_eq!(
            io_failure(std::io::Error::from(std::io::ErrorKind::TimedOut)).0,
            "deadline"
        );
    }

    #[test]
    fn ureq_configuration_errors_stay_unclassified_and_limits_are_explicit() {
        for error in [
            ureq::Error::InvalidProxyUrl,
            ureq::Error::BadUri("bad".into()),
            ureq::Error::TlsRequired,
            ureq::Error::BodyStalled,
            ureq::Error::Other("adapter programming mistake".into()),
            ureq::Error::Io(std::io::Error::other("adapter programming mistake")),
        ] {
            assert_eq!(ureq_failure(&error).0, "unclassified");
        }
        assert_eq!(
            ureq_failure(&ureq::Error::BodyExceedsLimit(1)).0,
            "response_limit"
        );
        assert_eq!(
            ureq_failure(&ureq::Error::LargeResponseHeader(1, 2)).0,
            "response_limit"
        );
        assert_eq!(
            ureq_failure(&ureq::Error::Io(std::io::Error::from(
                std::io::ErrorKind::ConnectionReset
            )))
            .0,
            "transport"
        );
        assert_eq!(
            io_failure(std::io::Error::other(ureq::Error::BodyExceedsLimit(1))).0,
            "response_limit"
        );
    }

    #[test]
    fn proxy_category_requires_an_auth_response() {
        assert_eq!(
            ureq_failure(&ureq::Error::ConnectProxyFailed(
                "proxy server responded 407/407".into()
            ))
            .0,
            "proxy"
        );
        for text in [
            "adapter programming mistake 407/",
            "proxy server did not respond",
            "proxy server responded 500/500",
        ] {
            assert_eq!(
                ureq_failure(&ureq::Error::ConnectProxyFailed(text.into())).0,
                "unclassified"
            );
        }
    }

    #[test]
    fn reqwest_builder_errors_are_never_network_failures() {
        let error = reqwest::Client::new()
            .get("invalid URL")
            .build()
            .expect_err("invalid URL");
        assert_eq!(reqwest_category(&error).0, "unclassified");
        assert_eq!(io_failure(std::io::Error::other(error)).0, "unclassified");
    }

    #[test]
    fn isahc_local_and_unknown_errors_require_network_evidence() {
        use isahc::error::ErrorKind;
        for kind in [
            ErrorKind::Unknown,
            ErrorKind::Io,
            ErrorKind::InvalidRequest,
            ErrorKind::ClientInitialization,
            ErrorKind::BadClientCertificate,
            ErrorKind::RequestBodyNotRewindable,
        ] {
            assert_eq!(isahc_failure(&isahc::Error::from(kind)).0, "unclassified");
        }
        let local = isahc::Error::from(std::io::Error::other("adapter programming mistake"));
        assert_eq!(isahc_failure(&local).0, "unclassified");
        let reset = isahc::Error::from(std::io::Error::from(std::io::ErrorKind::ConnectionReset));
        assert_eq!(isahc_failure(&reset).0, "transport");
        assert_eq!(
            source_category(Some(&curl::Error::new(56))),
            Some("transport")
        );
        assert_eq!(source_category(Some(&curl::Error::new(43))), None);
    }

    #[test]
    fn api_configuration_errors_do_not_satisfy_transport_controls() {
        let error = anchorpass::Client::builder()
            .timeout(Duration::ZERO)
            .build()
            .unwrap_err();
        assert_eq!(api_failure(error).0, "unclassified");
    }

    #[test]
    fn missing_or_unknown_routes_never_construct_direct_clients() {
        let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
        listener.set_nonblocking(true).unwrap();
        let address = listener.local_addr().unwrap();
        let mut job: Job = serde_json::from_value(json!({
            "schema_version": 1, "run_id": "route-validation", "candidate": "rust-reqwest-async",
            "fixture": {"https_url": format!("http://{address}"), "bad_target_url": "", "ca_file": "", "proxy_ca_file": "", "proxies": {}, "anchor_url": "", "bundle_url": "", "trusted_keys": {}},
            "profile": {"kind": "transport", "route": "http", "connection_mode": "warm", "concurrency": 1, "warmup": 0, "requests": 1, "timeout_ms": 1000, "max_response_bytes": 1024, "path": "/bytes", "expected_bytes": 2, "expected_error": null, "recovery_requests": 0}
        })).unwrap();
        for route in [
            "http",
            "https",
            "socks5",
            "socks5h",
            "bad_target",
            "bad_auth",
            "bad_tls",
            "unknown",
        ] {
            job.profile.route = route.into();
            assert!(proxy(&job).is_err(), "{route}");
            assert!(async_http(&job).unwrap_err().to_string().contains("route"));
            for candidate in [
                "rust-reqwest-blocking",
                "rust-isahc",
                "rust-ureq",
                "rust-api-blocking",
            ] {
                job.candidate = candidate.into();
                assert!(sync_client(&job, Path::new("unused")).is_err());
            }
        }
        job.profile.route = "direct".into();
        job.fixture
            .proxies
            .insert("direct".into(), "http://invalid.example:1234".into());
        assert!(proxy(&job).unwrap().is_none());
        assert_eq!(
            listener.accept().unwrap_err().kind(),
            std::io::ErrorKind::WouldBlock
        );
        for url in [
            "https://localhost:1234",
            "http://127.0.0.1:1234",
            "http://[::1]:1234",
        ] {
            assert!(is_loopback_host(&reqwest::Url::parse(url).unwrap()));
        }
        assert!(!is_loopback_host(
            &reqwest::Url::parse("https://example.com").unwrap()
        ));
    }

    #[test]
    fn unclassified_failure_cannot_pass_even_a_matching_expected_category() {
        let profile: Profile = serde_json::from_value(json!({"kind":"transport","route":"direct",
            "connection_mode":"warm","concurrency":1,"warmup":0,"requests":1,"timeout_ms":1000,
            "max_response_bytes":1024,"path":"/bytes","expected_bytes":1,
            "expected_error":"unclassified","recovery_requests":0}))
        .unwrap();
        let job = Job {
            schema_version: 1,
            run_id: "classification".into(),
            candidate: "rust-ureq".into(),
            profile,
            fixture: Fixture {
                https_url: String::new(),
                bad_target_url: String::new(),
                ca_file: String::new(),
                proxy_ca_file: String::new(),
                proxies: BTreeMap::new(),
                anchor_url: String::new(),
                bundle_url: String::new(),
                trusted_keys: BTreeMap::new(),
                flow_fields: BTreeMap::new(),
            },
        };
        assert!(!sample(&job, Instant::now(), Err(Failure::new("unclassified"))).ok);
    }
}
