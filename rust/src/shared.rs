//! Process-wide sharing of bounded, untrusted update downloads across Tokio runtimes.
use crate::{client, Error, ErrorKind, Result};
use std::{
    sync::{
        atomic::{AtomicUsize, Ordering},
        Arc, LazyLock, Mutex,
    },
    thread::JoinHandle,
    time::Duration,
};
use tokio::sync::watch;
use url::Url;

const MAX_FLIGHTS: usize = 16;
const MAX_WAITERS_PER_FLIGHT: usize = 1024;
type DownloadResult = Result<Arc<str>>;

#[derive(Clone)]
pub(crate) struct DownloadConfig {
    pub url: Url,
    pub proxy: Option<String>,
    pub roots: Vec<reqwest::Certificate>,
    pub root_identity: Vec<Vec<u8>>,
    pub proxy_root_identity: Vec<Vec<u8>>,
    pub timeout: Duration,
}
// Deliberately not Debug: the effective proxy identity may contain credentials.
#[derive(PartialEq, Eq)]
struct PolicyKey {
    url: String,
    proxy: Option<String>,
    roots: Vec<Vec<u8>>,
    proxy_roots: Vec<Vec<u8>>,
    timeout: Duration,
}
impl From<&DownloadConfig> for PolicyKey {
    fn from(config: &DownloadConfig) -> Self {
        Self {
            url: config.url.to_string(),
            proxy: config.proxy.clone(),
            roots: config.root_identity.clone(),
            proxy_roots: config.proxy_root_identity.clone(),
            timeout: config.timeout,
        }
    }
}
struct Flight {
    subscribers: AtomicUsize,
    result: watch::Sender<Option<DownloadResult>>,
    cancel: watch::Sender<bool>,
}
struct Entry {
    key: PolicyKey,
    flight: Arc<Flight>,
    worker: JoinHandle<()>,
}
#[derive(Default)]
struct Registry {
    entries: Vec<Entry>,
}
static REGISTRY: LazyLock<Mutex<Registry>> = LazyLock::new(|| Mutex::new(Registry::default()));
fn unavailable() -> Error {
    Error::new(
        ErrorKind::WorkerUnavailable,
        "updates",
        "shared update coordinator unavailable",
    )
}
fn limit() -> Error {
    Error::new(
        ErrorKind::ResourceLimit,
        "updates",
        "shared update coordinator limit reached",
    )
}
impl Registry {
    fn reap(&mut self) {
        let mut index = 0;
        while index < self.entries.len() {
            if self.entries[index]
                .flight
                .subscribers
                .load(Ordering::Acquire)
                == 0
                && self.entries[index].worker.is_finished()
            {
                let entry = self.entries.swap_remove(index);
                // is_finished guarantees join cannot wait for network, runtime shutdown or DNS.
                let _ = entry.worker.join();
            } else {
                index += 1;
            }
        }
    }
}
struct Subscription {
    flight: Arc<Flight>,
    receiver: watch::Receiver<Option<DownloadResult>>,
}
impl Drop for Subscription {
    fn drop(&mut self) {
        // This mutex only guards bounded bookkeeping and joins already finished threads.
        if let Ok(mut registry) = REGISTRY.lock() {
            if self.flight.subscribers.fetch_sub(1, Ordering::AcqRel) == 1 {
                self.flight.cancel.send_replace(true);
            }
            registry.reap();
        } else {
            // A poisoned registry cannot admit work; still cancel this subscriber's owned flight.
            if self.flight.subscribers.fetch_sub(1, Ordering::AcqRel) == 1 {
                self.flight.cancel.send_replace(true);
            }
        }
    }
}
fn subscribe(config: DownloadConfig) -> Result<Subscription> {
    let key = PolicyKey::from(&config);
    let mut registry = REGISTRY.lock().map_err(|_| unavailable())?;
    registry.reap();
    if let Some(entry) = registry.entries.iter().find(|entry| entry.key == key) {
        let count = entry.flight.subscribers.load(Ordering::Acquire);
        // A canceled worker remains counted until its runtime has actually terminated.
        if count == 0 || count >= MAX_WAITERS_PER_FLIGHT {
            return Err(limit());
        }
        entry.flight.subscribers.fetch_add(1, Ordering::AcqRel);
        return Ok(Subscription {
            flight: entry.flight.clone(),
            receiver: entry.flight.result.subscribe(),
        });
    }
    if registry.entries.len() >= MAX_FLIGHTS {
        return Err(limit());
    }
    let (result, receiver) = watch::channel(None);
    let (cancel, cancellation) = watch::channel(false);
    let flight = Arc::new(Flight {
        subscribers: AtomicUsize::new(1),
        result,
        cancel,
    });
    let owned = flight.clone();
    let worker = std::thread::Builder::new()
        .name("anchorpass-update".to_owned())
        .spawn(move || {
            let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
                run(config, cancellation)
            }))
            .unwrap_or_else(|_| Err(unavailable()));
            if owned.subscribers.load(Ordering::Acquire) > 0 {
                owned.result.send_replace(Some(result));
            }
        })
        .map_err(|_| unavailable())?;
    registry.entries.push(Entry {
        key,
        flight: flight.clone(),
        worker,
    });
    Ok(Subscription { flight, receiver })
}
fn run(config: DownloadConfig, mut cancellation: watch::Receiver<bool>) -> DownloadResult {
    // A finite worker owns its transport and runtime. No socket is bound to a subscriber's runtime.
    let runtime = tokio::runtime::Builder::new_current_thread()
        .max_blocking_threads(1)
        .enable_all()
        .build()
        .map_err(|_| unavailable())?;
    let result = runtime.block_on(async {
        let download = async {
            let http = client::transport(config.proxy.as_deref(), 1, &config.roots, &config.root_identity, &config.proxy_root_identity)?;
            client::request(&http, config.url, None, crate::bundle::MAX_BUNDLE, "updates", config.proxy.is_some(), config.timeout).await.map(Arc::<str>::from)
        };
        tokio::select! {
            biased;
            _ = async { while !*cancellation.borrow_and_update() { if cancellation.changed().await.is_err() { break; } } } => Err(Error::new(ErrorKind::Cancelled, "updates", "all shared update subscribers cancelled")),
            result = tokio::time::timeout(config.timeout, download) => result.unwrap_or_else(|_| Err(Error::new(ErrorKind::DeadlineExceeded, "updates", "shared update deadline exceeded"))),
        }
    });
    // Preserve the documented OS DNS limitation. The registry retains this worker's slot
    // until shutdown really finishes, rather than detaching an unbounded DNS operation.
    drop(runtime);
    result
}
pub(crate) async fn download(config: DownloadConfig) -> Result<Arc<str>> {
    let mut subscription = subscribe(config)?;
    loop {
        let completed = { subscription.receiver.borrow_and_update().clone() };
        if let Some(result) = completed {
            loop {
                let finished = {
                    let registry = REGISTRY.lock().map_err(|_| unavailable())?;
                    registry
                        .entries
                        .iter()
                        .find(|entry| Arc::ptr_eq(&entry.flight, &subscription.flight))
                        .is_none_or(|entry| entry.worker.is_finished())
                };
                if finished {
                    return result;
                }
                tokio::time::sleep(Duration::from_millis(1)).await;
            }
        }
        subscription
            .receiver
            .changed()
            .await
            .map_err(|_| unavailable())?;
    }
}
#[cfg(test)]
pub(crate) fn active_workers() -> usize {
    let mut registry = REGISTRY.lock().expect("test registry lock");
    registry.reap();
    registry.entries.len()
}

#[cfg(test)]
pub(crate) fn subscribers(config: &DownloadConfig) -> usize {
    let key = PolicyKey::from(config);
    let mut registry = REGISTRY.lock().expect("test registry lock");
    registry.reap();
    registry
        .entries
        .iter()
        .find(|entry| entry.key == key)
        .map_or(0, |entry| entry.flight.subscribers.load(Ordering::Acquire))
}
#[cfg(test)]
pub(crate) fn present(config: &DownloadConfig) -> bool {
    let key = PolicyKey::from(config);
    let mut registry = REGISTRY.lock().expect("test registry lock");
    registry.reap();
    registry.entries.iter().any(|entry| entry.key == key)
}
