use crate::{
    bundle::{self, Bundle},
    cache,
    cookies::OperationCookies,
    core, proxy, shared, Error, ErrorKind, Result, TrustedKeys, UpdatesProxy,
};
use std::{
    path::PathBuf,
    sync::Arc,
    time::{Duration, Instant},
};
use tokio::sync::{Mutex, Semaphore};
use url::Url;

const MAX_CA_CERTIFICATE_BYTES: usize = 65536;
const MAX_CUSTOM_CA_CERTIFICATES: usize = 32;
const MAX_BUNDLE_URL_BYTES: usize = 16384;

/// Per-operation overrides. They never modify signed data or the shared client snapshot.
#[derive(Clone, Copy, Default)]
pub struct SolveOptions<'a> {
    pub action: Option<&'a str>,
    pub bg: Option<&'a str>,
    pub user_agent: Option<&'a str>,
    pub hostname: Option<&'a str>,
}
impl std::fmt::Debug for SolveOptions<'_> {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("SolveOptions")
            .field("action", &self.action.map(|_| "[redacted]"))
            .field("bg", &self.bg.map(|_| "[redacted]"))
            .field("user_agent", &self.user_agent.map(|_| "[redacted]"))
            .field("hostname", &self.hostname.map(|_| "[redacted]"))
            .finish()
    }
}
/// A successful protocol response, without any promise about the site's score or acceptance.
#[derive(Clone)]
pub struct SolveResult {
    pub token: String,
    pub bundle_revision: String,
    pub protocol_version: String,
    pub duration: Duration,
}
impl std::fmt::Debug for SolveResult {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("SolveResult")
            .field("token", &"[redacted]")
            .field("bundle_revision", &self.bundle_revision)
            .field("protocol_version", &self.protocol_version)
            .field("duration", &self.duration)
            .finish()
    }
}
#[derive(Clone, Debug)]
pub struct BundleInfo {
    pub revision: String,
    pub expires_at: u64,
    pub persisted: bool,
}
struct State {
    bundle: Bundle,
    refreshed: Option<Instant>,
}
struct Inner {
    http: HttpClient,
    proxy: bool,
    timeout: Duration,
    update_timeout: Duration,
    max_body: usize,
    update_config: Option<shared::DownloadConfig>,
    cache_path: Option<PathBuf>,
    pin: Option<String>,
    offline: bool,
    refresh_interval: Duration,
    keys: TrustedKeys,
    state: Mutex<State>,
    permits: Semaphore,
    #[cfg(test)]
    origin: Option<Url>,
}
/// Cloneable client. Clones share bounded concurrency and a verified immutable snapshot per solve.
#[derive(Clone)]
pub struct Client {
    inner: Arc<Inner>,
}
impl std::fmt::Debug for Client {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("Client")
            .field("proxy", &self.inner.proxy)
            .field("timeout", &self.inner.timeout)
            .field("offline", &self.inner.offline)
            .finish_non_exhaustive()
    }
}
/// Client configuration. Environment proxy variables are always ignored.
#[derive(Clone)]
pub struct ClientBuilder {
    proxy: Option<String>,
    updates_proxy: UpdatesProxy,
    timeout: Duration,
    update_timeout: Duration,
    max_body: usize,
    max_in_flight: usize,
    bundle_url: Option<String>,
    cache_path: Option<PathBuf>,
    pin: Option<String>,
    offline: bool,
    refresh_interval: Duration,
    keys: TrustedKeys,
    roots: Vec<reqwest::Certificate>,
    root_identity: Vec<Vec<u8>>,
    proxy_root_identity: Vec<Vec<u8>>,
    #[cfg(test)]
    pub(crate) origin: Option<Url>,
}
fn config_error(message: &'static str) -> Error {
    Error::new(ErrorKind::InvalidInput, "build", message)
}
impl Default for ClientBuilder {
    fn default() -> Self {
        Self {
            proxy: None,
            updates_proxy: UpdatesProxy::Inherit,
            timeout: Duration::from_secs(30),
            update_timeout: Duration::from_secs(5),
            max_body: 1048576,
            max_in_flight: 16,
            bundle_url: None,
            cache_path: None,
            pin: None,
            offline: false,
            refresh_interval: Duration::from_secs(3600),
            keys: serde_json::from_str(include_str!("../data/keys.json"))
                .expect("embedded key JSON"),
            roots: Vec::new(),
            root_identity: Vec::new(),
            proxy_root_identity: Vec::new(),
            #[cfg(test)]
            origin: None,
        }
    }
}
impl ClientBuilder {
    pub fn proxy(mut self, value: impl Into<String>) -> Self {
        self.proxy = Some(value.into());
        self
    }
    pub fn updates_proxy(mut self, value: UpdatesProxy) -> Self {
        self.updates_proxy = value;
        self
    }
    pub fn timeout(mut self, value: Duration) -> Self {
        self.timeout = value;
        self
    }
    pub fn update_timeout(mut self, value: Duration) -> Self {
        self.update_timeout = value;
        self
    }
    pub fn max_response_bytes(mut self, value: usize) -> Self {
        self.max_body = value;
        self
    }
    pub fn max_in_flight(mut self, value: usize) -> Self {
        self.max_in_flight = value;
        self
    }
    pub fn bundle_url(mut self, value: impl Into<String>) -> Self {
        self.bundle_url = Some(value.into());
        self
    }
    pub fn cache_path(mut self, value: impl Into<PathBuf>) -> Self {
        self.cache_path = Some(value.into());
        self
    }
    /// Explicitly accept in-memory rollback protection, which is lost on process restart.
    pub fn memory_cache(mut self) -> Self {
        self.cache_path = None;
        self
    }
    pub fn pin(mut self, value: impl Into<String>) -> Self {
        self.pin = Some(value.into());
        self
    }
    pub fn offline(mut self, value: bool) -> Self {
        self.offline = value;
        self
    }
    pub fn refresh_interval(mut self, value: Duration) -> Self {
        self.refresh_interval = value;
        self
    }
    /// Add application-approved public keys; bundles cannot introduce their own trust.
    pub fn trusted_keys(mut self, value: TrustedKeys) -> Self {
        self.keys.extend(value);
        self
    }
    /// Add a target-server CA. This never changes HTTPS-proxy trust.
    pub fn add_root_certificate(mut self, pem: &[u8]) -> Result<Self> {
        if pem.len() > MAX_CA_CERTIFICATE_BYTES || self.roots.len() >= MAX_CUSTOM_CA_CERTIFICATES {
            return Err(config_error("custom CA input limit exceeded"));
        }
        self.roots.push(
            reqwest::Certificate::from_pem(pem)
                .map_err(|_| config_error("invalid CA certificate"))?,
        );
        self.root_identity.push(pem.to_vec());
        Ok(self)
    }
    /// Add an HTTPS-proxy CA. This never changes target-server trust.
    pub fn add_proxy_root_certificate(mut self, pem: &[u8]) -> Result<Self> {
        if pem.len() > MAX_CA_CERTIFICATE_BYTES
            || self.proxy_root_identity.len() >= MAX_CUSTOM_CA_CERTIFICATES
        {
            return Err(config_error("custom proxy CA input limit exceeded"));
        }
        reqwest::Certificate::from_pem(pem)
            .map_err(|_| config_error("invalid proxy CA certificate"))?;
        self.proxy_root_identity.push(pem.to_vec());
        Ok(self)
    }
    pub fn build(self) -> Result<Client> {
        if self.timeout.is_zero()
            || self.timeout > Duration::from_secs(3600)
            || self.update_timeout.is_zero()
            || self.max_in_flight == 0
            || self.max_in_flight > 1024
            || self.max_body == 0
            || self.max_body > 16777216
        {
            return Err(config_error("invalid resource limits"));
        }
        if self
            .pin
            .as_ref()
            .is_some_and(|p| !bundle::revision_valid(p))
        {
            return Err(config_error("invalid pinned revision"));
        }
        let bundle_url = self
            .bundle_url
            .as_ref()
            .map(|v| {
                if v.len() > MAX_BUNDLE_URL_BYTES {
                    return Err(config_error("bundle URL too long"));
                }
                let url = Url::parse(v).map_err(|_| config_error("invalid bundle URL"))?;
                let loopback = match url.host() {
                    Some(url::Host::Domain("localhost")) => true,
                    Some(url::Host::Ipv4(ip)) => ip.is_loopback(),
                    Some(url::Host::Ipv6(ip)) => ip.is_loopback(),
                    _ => false,
                };
                if !(url.scheme() == "https" || url.scheme() == "http" && loopback)
                    || !url.username().is_empty()
                    || url.password().is_some()
                    || url.fragment().is_some()
                    || url.host_str().is_none()
                    || v.contains('@')
                    || !core::valid_percent(v)
                    || v.chars().any(char::is_whitespace)
                {
                    return Err(config_error(
                        "bundle URL must be HTTPS or a loopback HTTP fixture",
                    ));
                }
                Ok(url)
            })
            .transpose()?;
        let update_proxy = match &self.updates_proxy {
            UpdatesProxy::Inherit => self.proxy.clone(),
            UpdatesProxy::Direct => None,
            UpdatesProxy::Proxy(v) => Some(v.clone()),
        };
        let http = transport(
            self.proxy.as_deref(),
            self.max_in_flight,
            &self.roots,
            &self.root_identity,
            &self.proxy_root_identity,
        )?;
        if let Some(value) = update_proxy.as_deref() {
            proxy::parse(value)?;
        }
        let update_config = bundle_url.map(|url| shared::DownloadConfig {
            url,
            proxy: update_proxy,
            roots: self.roots,
            root_identity: self.root_identity,
            proxy_root_identity: self.proxy_root_identity,
            timeout: self.update_timeout,
        });
        let initial = Bundle::verify(include_bytes!("../data/bundle.json"), &self.keys)?;
        Ok(Client {
            inner: Arc::new(Inner {
                http,
                proxy: self.proxy.is_some(),
                timeout: self.timeout,
                update_timeout: self.update_timeout,
                max_body: self.max_body,
                update_config,
                cache_path: self.cache_path,
                pin: self.pin,
                offline: self.offline,
                refresh_interval: self.refresh_interval,
                keys: self.keys,
                state: Mutex::new(State {
                    bundle: initial,
                    refreshed: None,
                }),
                permits: Semaphore::new(self.max_in_flight),
                #[cfg(test)]
                origin: self.origin,
            }),
        })
    }
}
pub(crate) enum HttpClient {
    Reqwest(reqwest::Client),
    HttpsProxy(crate::tls_proxy::HttpsProxy),
}
pub(crate) fn transport(
    proxy: Option<&str>,
    connections: usize,
    roots: &[reqwest::Certificate],
    root_identity: &[Vec<u8>],
    proxy_root_identity: &[Vec<u8>],
) -> Result<HttpClient> {
    if let Some(value) = proxy {
        proxy::parse(value)?;
        if value
            .split_once(':')
            .is_some_and(|(scheme, _)| scheme.eq_ignore_ascii_case("https"))
        {
            return crate::tls_proxy::HttpsProxy::new(
                value,
                connections,
                root_identity,
                proxy_root_identity,
            )
            .map(HttpClient::HttpsProxy);
        }
    }
    let mut builder = reqwest::Client::builder()
        .no_proxy()
        .redirect(reqwest::redirect::Policy::none())
        .connect_timeout(Duration::from_secs(5))
        .read_timeout(Duration::from_secs(5))
        .pool_max_idle_per_host(connections)
        .pool_idle_timeout(Duration::from_secs(30))
        .retry(reqwest::retry::never());
    for root in roots {
        builder = builder.add_root_certificate(root.clone());
    }
    if let Some(value) = proxy {
        builder = builder.proxy(proxy::parse(value)?);
    }
    builder
        .build()
        .map(HttpClient::Reqwest)
        .map_err(|_| config_error("HTTP transport initialization failed"))
}
impl Client {
    pub fn builder() -> ClientBuilder {
        ClientBuilder::default()
    }
    pub async fn solve(&self, anchor_url: &str) -> Result<SolveResult> {
        self.solve_with_options(anchor_url, SolveOptions::default())
            .await
    }
    pub async fn solve_with_action(&self, anchor_url: &str, action: &str) -> Result<SolveResult> {
        self.solve_with_options(
            anchor_url,
            SolveOptions {
                action: Some(action),
                bg: None,
                user_agent: None,
                hostname: None,
            },
        )
        .await
    }
    pub async fn solve_with_options(
        &self,
        input: &str,
        options: SolveOptions<'_>,
    ) -> Result<SolveResult> {
        if let Some(user_agent) = options.user_agent {
            if user_agent.is_empty()
                || user_agent.len() > 1024
                || user_agent.trim().is_empty()
                || !user_agent.bytes().all(|byte| (0x20..=0x7e).contains(&byte))
            {
                return Err(Error::new(
                    ErrorKind::InvalidInput,
                    "overrides",
                    "User-Agent must be 1-1024 printable ASCII bytes and not whitespace-only",
                ));
            }
        }
        let start = Instant::now();
        tokio::time::timeout(self.inner.timeout, async {
            if options
                .bg
                .is_some_and(|bg| bg.is_empty() || bg.len() > 16384)
            {
                return Err(Error::new(
                    ErrorKind::InvalidInput,
                    "solve",
                    "bg must be nonempty and at most 16384 UTF-8 bytes",
                ));
            }
            let mut anchor = core::parse_anchor(input, options.action)?;
            if let Some(hostname) = options.hostname {
                anchor.co = crate::hostname::origin(&anchor.co, hostname)?;
                let pairs: Vec<_> = anchor
                    .url
                    .query_pairs()
                    .map(|(key, value)| (key.into_owned(), value.into_owned()))
                    .map(|(key, value)| {
                        let value = if key == "co" {
                            anchor.co.clone()
                        } else {
                            value
                        };
                        (key, value)
                    })
                    .collect();
                anchor.url.query_pairs_mut().clear().extend_pairs(pairs);
            }
            let _permit = self.inner.permits.try_acquire().map_err(|_| {
                Error::new(
                    ErrorKind::ResourceLimit,
                    "solve",
                    "client concurrency limit reached",
                )
            })?;
            let bundle = self
                .snapshot(false, self.inner.timeout.as_secs().saturating_add(1))
                .await?;
            let mut cookies = OperationCookies::default();
            let mut api = anchor.url.clone();
            api.set_path(if anchor.variant == "enterprise" {
                "/recaptcha/enterprise.js"
            } else {
                "/recaptcha/api.js"
            });
            api.set_query(None);
            api.query_pairs_mut().append_pair("render", &anchor.key);
            let version = core::version(
                &self
                    .request(
                        self.route(api),
                        None,
                        "version",
                        &mut cookies,
                        options.user_agent,
                    )
                    .await?,
            )?;
            let pairs: Vec<_> = anchor
                .url
                .query_pairs()
                .filter(|(k, _)| k != "v")
                .map(|(k, v)| (k.into_owned(), v.into_owned()))
                .collect();
            anchor
                .url
                .query_pairs_mut()
                .clear()
                .extend_pairs(pairs)
                .append_pair("v", &version);
            let body = self
                .request(
                    self.route(anchor.url.clone()),
                    None,
                    "anchor",
                    &mut cookies,
                    options.user_agent,
                )
                .await?;
            let challenge = core::challenge(&body)?;
            let mut fields = bundle.payload.fields.clone();
            if let Some(bg) = options.bg {
                fields.insert("bg".to_owned(), bg.to_owned());
            }
            fields.extend([
                ("v".to_owned(), version.clone()),
                ("c".to_owned(), challenge),
                ("k".to_owned(), anchor.key.clone()),
                ("co".to_owned(), anchor.co),
            ]);
            if let Some((_, action)) = anchor.url.query_pairs().find(|(key, _)| key == "sa") {
                fields.insert("sa".to_owned(), action.into_owned());
            }
            let mut reload = anchor.url;
            reload.set_path(&format!("/recaptcha/{}/reload", anchor.variant));
            reload.set_query(None);
            reload.query_pairs_mut().append_pair("k", &anchor.key);
            let form = url::form_urlencoded::Serializer::new(String::new())
                .extend_pairs(fields)
                .finish();
            let response = self
                .request(
                    self.route(reload),
                    Some(form),
                    "reload",
                    &mut cookies,
                    options.user_agent,
                )
                .await?;
            Ok(SolveResult {
                token: core::token(&response)?,
                bundle_revision: bundle.payload.revision,
                protocol_version: version,
                duration: start.elapsed(),
            })
        })
        .await
        .map_err(|_| {
            Error::new(
                ErrorKind::DeadlineExceeded,
                "solve",
                "total solve deadline exceeded",
            )
        })?
    }
    /// Read bg from the selected verified bundle, using the normal update and cache policy.
    pub async fn load_bg(&self) -> Result<String> {
        tokio::time::timeout(self.inner.timeout, async {
            let _permit = self.inner.permits.try_acquire().map_err(|_| {
                Error::new(
                    ErrorKind::ResourceLimit,
                    "bundle",
                    "client concurrency limit reached",
                )
            })?;
            let snapshot = self.snapshot(false, 0).await?;
            Ok(snapshot.payload.fields["bg"].clone())
        })
        .await
        .map_err(|_| {
            Error::new(
                ErrorKind::DeadlineExceeded,
                "bundle",
                "total bundle deadline exceeded",
            )
        })?
    }
    /// Validate local data and perform a due refresh, without contacting CAPTCHA servers.
    pub async fn prepare(&self) -> Result<BundleInfo> {
        self.prepare_inner(false).await
    }
    /// Explicitly check the configured update channel. A pin still forbids changing revision.
    pub async fn refresh(&self) -> Result<BundleInfo> {
        self.prepare_inner(true).await
    }
    async fn prepare_inner(&self, force: bool) -> Result<BundleInfo> {
        tokio::time::timeout(self.inner.timeout, async {
            let _permit = self.inner.permits.try_acquire().map_err(|_| {
                Error::new(
                    ErrorKind::ResourceLimit,
                    "updates",
                    "client concurrency limit reached",
                )
            })?;
            let bundle = self.snapshot(force, 0).await?;
            Ok(BundleInfo {
                revision: bundle.payload.revision,
                expires_at: bundle.payload.expires_at,
                persisted: self.inner.cache_path.is_some(),
            })
        })
        .await
        .map_err(|_| {
            Error::new(
                ErrorKind::DeadlineExceeded,
                "updates",
                "total update deadline exceeded",
            )
        })?
    }
    fn route(&self, url: Url) -> Url {
        #[cfg(test)]
        if let Some(origin) = &self.inner.origin {
            let mut routed = origin.clone();
            routed.set_path(url.path());
            routed.set_query(url.query());
            return routed;
        }
        url
    }
    async fn request(
        &self,
        url: Url,
        form: Option<String>,
        stage: &'static str,
        cookies: &mut OperationCookies,
        user_agent: Option<&str>,
    ) -> Result<String> {
        RequestSession {
            http: &self.inner.http,
            proxied: self.inner.proxy,
            timeout: self.inner.timeout,
            cookies: Some(cookies),
            user_agent,
        }
        .request(url, form, self.inner.max_body, stage)
        .await
    }
    async fn snapshot(&self, force: bool, seconds: u64) -> Result<Bundle> {
        let mut state = self.inner.state.lock().await;
        if let Some(path) = &self.inner.cache_path {
            if let Some(cached) = cache::load(path, &self.inner.keys).await? {
                state.bundle.advance(&cached)?;
                state.bundle = cached;
            } else {
                state.bundle = cache::accept(path, &state.bundle, &self.inner.keys).await?;
            }
        }
        if let Some(pin) = &self.inner.pin {
            if bundle::revision_cmp(pin, &state.bundle.payload.revision).is_lt() {
                return Err(Error::new(
                    ErrorKind::ConfigRollback,
                    "bundle",
                    "pin is below accepted highwater",
                ));
            }
        }
        let current_valid = state.bundle.valid_for(seconds);
        let pin_matches = self
            .inner
            .pin
            .as_ref()
            .is_none_or(|p| p == &state.bundle.payload.revision);
        let due = force
            || state
                .refreshed
                .is_none_or(|t| t.elapsed() >= self.inner.refresh_interval)
            || current_valid.is_err()
            || !pin_matches;
        let may_refresh = !self.inner.offline
            && self.inner.update_config.is_some()
            && due
            && (!pin_matches || self.inner.pin.is_none());
        if may_refresh {
            let config = self.inner.update_config.clone().expect("checked channel");
            let mut authenticated = false;
            let attempt = tokio::time::timeout(self.inner.update_timeout, async {
                let bytes = shared::download(config).await?;
                let candidate = Bundle::verify(bytes.as_bytes(), &self.inner.keys)?;
                state.bundle.advance(&candidate)?;
                // Authentication establishes the rollback floor before usability.
                authenticated = true;
                state.bundle = candidate;
                if let Some(path) = &self.inner.cache_path {
                    state.bundle = cache::accept(path, &state.bundle, &self.inner.keys).await?;
                }
                state.bundle.valid_for(seconds)?;
                if self
                    .inner
                    .pin
                    .as_ref()
                    .is_some_and(|pin| pin != &state.bundle.payload.revision)
                {
                    return Err(Error::new(
                        ErrorKind::ConfigUnavailable,
                        "bundle",
                        "update does not match pinned revision",
                    ));
                }
                Ok(())
            })
            .await
            .unwrap_or_else(|_| {
                Err(Error::new(
                    ErrorKind::DeadlineExceeded,
                    "updates",
                    "update deadline exceeded",
                ))
            });
            state.refreshed = Some(Instant::now());
            match attempt {
                Ok(()) => {}
                Err(error) if authenticated || force || current_valid.is_err() || !pin_matches => {
                    return Err(error)
                }
                Err(_) => {}
            }
        }
        state.bundle.valid_for(seconds)?;
        if self
            .inner
            .pin
            .as_ref()
            .is_some_and(|p| p != &state.bundle.payload.revision)
        {
            return Err(Error::new(
                ErrorKind::ConfigUnavailable,
                "bundle",
                "pinned revision is unavailable",
            ));
        }
        Ok(state.bundle.clone())
    }
}

pub(crate) async fn request(
    http: &HttpClient,
    url: Url,
    form: Option<String>,
    limit: usize,
    stage: &'static str,
    proxied: bool,
    timeout: Duration,
) -> Result<String> {
    RequestSession {
        http,
        proxied,
        timeout,
        cookies: None,
        user_agent: None,
    }
    .request(url, form, limit, stage)
    .await
}

struct RequestSession<'a> {
    http: &'a HttpClient,
    proxied: bool,
    timeout: Duration,
    cookies: Option<&'a mut OperationCookies>,
    user_agent: Option<&'a str>,
}

impl RequestSession<'_> {
    async fn request(
        self,
        url: Url,
        form: Option<String>,
        limit: usize,
        stage: &'static str,
    ) -> Result<String> {
        let http = match self.http {
            HttpClient::HttpsProxy(http) => {
                return http
                    .request(
                        url,
                        form,
                        limit,
                        stage,
                        self.timeout,
                        crate::tls_proxy::RequestHeaders {
                            cookies: self.cookies,
                            user_agent: self.user_agent,
                        },
                    )
                    .await
            }
            HttpClient::Reqwest(http) => http,
        };
        let mut request = if let Some(form) = form {
            http.post(url.clone())
                .header(
                    reqwest::header::CONTENT_TYPE,
                    "application/x-www-form-urlencoded",
                )
                .body(form)
        } else {
            http.get(url.clone())
        };
        if let Some(user_agent) = self.user_agent {
            request = request.header(reqwest::header::USER_AGENT, user_agent);
        }
        if let Some(cookies) = &self.cookies {
            if let Some(header) = cookies.header(&url, stage)? {
                request = request.header(reqwest::header::COOKIE, header);
            }
        }
        let map = |error: reqwest::Error| {
            let kind = if error.is_timeout() {
                ErrorKind::DeadlineExceeded
            } else if self.proxied && error.is_connect() {
                ErrorKind::ProxyConnectError
            } else {
                ErrorKind::TransportError
            };
            Error::new(kind, stage, "HTTP request failed")
        };
        let mut response = request.timeout(self.timeout).send().await.map_err(map)?;
        if let Some(cookies) = self.cookies {
            cookies.receive(&url, response.headers(), stage)?;
        }
        if response.status() == reqwest::StatusCode::PROXY_AUTHENTICATION_REQUIRED {
            return Err(Error::new(
                ErrorKind::ProxyAuthError,
                stage,
                "proxy authentication rejected",
            ));
        }
        if !response.status().is_success() {
            return Err(Error::new(
                ErrorKind::HttpStatusError,
                stage,
                "HTTP status is not successful",
            ));
        }
        if response.content_length().is_some_and(|v| v > limit as u64) {
            return Err(Error::new(
                ErrorKind::ResourceLimit,
                stage,
                "response body limit exceeded",
            ));
        }
        let mut body = Vec::new();
        while let Some(chunk) = response.chunk().await.map_err(map)? {
            if chunk.len() > limit.saturating_sub(body.len()) {
                return Err(Error::new(
                    ErrorKind::ResourceLimit,
                    stage,
                    "response body limit exceeded",
                ));
            }
            body.extend_from_slice(&chunk);
        }
        String::from_utf8(body)
            .map_err(|_| Error::new(ErrorKind::ProtocolError, stage, "response is not UTF-8"))
    }
}

#[cfg(test)]
#[path = "tests.rs"]
mod tests;
