# anchorpass (Rust)

> Development release candidate for the reCAPTCHA v3 `api2` and `enterprise` HTTP flow.

- Local synthetic conformance is verified; no live CAPTCHA acceptance or site score is claimed.
- The embedded `production-1` public key verifies the [GitHub channel](../docs/channel-operations.md).
- No remote update channel is enabled by default.
- Constructing/importing the crate performs no network requests.

## 🚀 Quick start

```rust,no_run
# #[cfg(feature = "async")]
# async fn example() -> anchorpass::Result<()> {
use anchorpass::{Client, SolveOptions, extract_action};
use std::time::Duration;
let client = Client::builder()
    .proxy("socks5h://user:password@127.0.0.1:1080")
    .timeout(Duration::from_secs(30))
    .memory_cache()
    .build()?;
let anchor = "https://www.google.com/recaptcha/api2/anchor?k=site-key&co=encoded-origin";
let action = extract_action(anchor)?; // Pure URL validation and extraction; no network.
let bg = client.load_bg().await?; // Verified bundle, normal update/cache policy.
let result = client.solve_with_options(anchor, SolveOptions {
    bg: Some(&bg),
    action: action.as_deref(),
    ..Default::default()
}).await?;
assert!(!result.token.is_empty());
# Ok(()) }
```

Enable `blocking` for a blocking API with matching builder and method names:

```rust,no_run
# #[cfg(feature = "blocking")]
# fn example() -> anchorpass::Result<()> {
let client = anchorpass::blocking::Client::builder().memory_cache().build()?;
let bg = client.load_bg()?;
let result = client.solve("https://www.google.com/recaptcha/api2/anchor?k=site-key&co=encoded-origin")?;
# Ok(()) }
```

## 🧩 API

| Call | Behavior |
|---|---|
| `solve(anchor).await` | Uses bundle `bg` and the URL's optional `sa` action. |
| `solve_with_action(anchor, action).await` | Overrides the URL action. |
| `solve_with_options` | Accepts independent optional `bg` and `action`. |
| `load_bg()` | Returns `String`. |
| `extract_action()` | Returns `Result<Option<String>>` and uses the same strict anchor URL rules as solve. Extraction preserves an empty URL `sa` as `Some("")`; solve validates the selected action separately. |

### Per-call overrides

- **`user_agent`:** optional borrowed string applied to version, anchor and reload requests for one solve, including the blocking API. Accepts 1–1024 printable ASCII bytes and rejects whitespace-only values. Updates retain their default headers; omission preserves the transport default.
- **`hostname`:** optional borrowed ASCII DNS/punycode name or IP address. Replaces only the hostname of the decoded `co` origin, preserving HTTP/HTTPS scheme and effective port, and sends the same encoded origin to anchor and reload. A selected override requires a valid encoded HTTP/HTTPS origin; omission preserves the original `co`, including legacy values. Both overrides are validated before updates/networking and never mutate the shared client.
- **`action`:** explicit action wins and is forwarded to both anchor and reload. A selected action must contain 1–256 UTF-8 bytes. An empty URL `sa` fails unless an explicit valid action replaces it.
- **`bg`:** a `bg` override must contain 1–16384 UTF-8 bytes.
- **Validation:** values are validated before updates or network I/O and only change this operation's form fields. NUL and control characters are data encoded by the native URL/form encoder; raw anchor and proxy URLs still reject whitespace/control characters.

### Errors

Errors expose `kind`, `stage`, and `retryable` (currently always false: the crate performs no automatic retries). Client/result diagnostics exclude tokens, proxy credentials and form data.

### Features

| Features | Exposed network client |
| --- | --- |
| default (`async`) | `anchorpass::Client` |
| `--no-default-features --features async` | `anchorpass::Client` |
| `--no-default-features --features blocking` | `anchorpass::blocking::Client` |
| `--all-features` | Both |
| `--no-default-features` | Error types and configuration types only |

## 🔀 Routing and proxies

- **TLS:** uses rustls; target and HTTPS-proxy certificates and hostnames are verified independently. `add_root_certificate(pem)` adds a private CA for target servers only; `add_proxy_root_certificate(pem)` adds one for HTTPS proxies only. Both builders expose these methods. Configure both when the two hops use private CAs. Each policy retains public WebPKI roots, and certificates never cross between the two policies, even when both hops use the same hostname.
- **Transports:** direct, HTTP-proxy and SOCKS routes use reqwest; HTTPS-proxy routes use a pooled Hyper/rustls adapter with separate TLS configurations and a bounded CONNECT response head. It owns no extra threads.
- **Compression:** HTTP compression is not advertised or automatically decoded; unexpected encoded responses fail protocol parsing rather than bypass body limits.
- **Proxy URLs:** require an explicit host and nonzero port. Schemes are `http`, `https`, `socks5` (local target DNS), and `socks5h` (proxy target DNS). HTTP Basic and SOCKS5 username/password are supported, including percent-encoded ASCII credentials (decoded once; `+` remains `+`). Paths other than `/`, query, fragment, whitespace and malformed percent encoding are rejected before requests.
- **Environment:** environment proxy variables, including `NO_PROXY`, are ignored. Proxy failures never retry directly. All protocol steps use the same route.
- **Protocol version:** every solve fetches the current protocol version even when the input URL supplies `v`. Redirects are rejected.
- **Your own verification request:** site-side token verification is the application's responsibility: configure that application's HTTP client with the required proxy too. A rotating proxy provider may change exit IP even while its URL stays constant.
- **Updates route:** `UpdatesProxy::Inherit` by default, `Direct` for an explicit direct route, or `Proxy(url)` for an independent route.

## 🔐 Signed data and updates

- **Updates:** `bundle_url(https_url)` explicitly configures updates; HTTP is restricted to loopback fixtures. Updates have their own pool with no cookies or CAPTCHA headers.
- **Preparation:** `prepare()` validates local data and performs a due refresh; `refresh()` explicitly attempts a channel check.
- **Modes:** `offline(true)` disables data networking, while CAPTCHA requests still require a network. `pin("revision")` selects exact data and never bypasses signature, expiry or accepted revision checks.
- **Envelopes:** signed envelopes are verified using Ed25519 over the exact original payload bytes and `anchorpass.bundle.v1\0 || key_id || \0 || payload`. Duplicate JSON keys, unknown fields, unknown signing keys, wrong family/schema, invalid revisions, oversized or expired data are rejected.
- **Fields:** revisions are positive decimal strings with 1–20 digits and no leading zeros. Timestamps are integers bounded by 9007199254740991. Each of the six signed fields must contain 1–16384 UTF-8 bytes; control characters remain ordinary form data.
- **HTML:** anchor input attributes use html5ever's HTML5 tokenizer, including named/numeric references, legacy semicolonless references, C1 numeric mappings and replacement characters before form encoding. HTML is never executed and no document tree or token list is accumulated.
- **Trusted keys:** `trusted_keys(BTreeMap<String,String>)` adds application-approved base64 Ed25519 public keys; bundles cannot introduce keys.
- **Snapshots:** a solve keeps one immutable data snapshot and requires it to remain valid for its allowed deadline. A failed automatic update can retain an unexpired matching snapshot; explicit refresh reports the failure.

### Cache

- **Default:** this development release defaults to an in-memory signed snapshot, consistently with the other language packages. Use `cache_path(path)` to enable persistent rollback protection, or `memory_cache()` to explicitly select the default memory mode. Memory mode never reads a cache location from the environment.
- **Writes:** cache updates hold a nonblocking process lock within the operation deadline and replace a fully written, synced envelope atomically. The signed cached revision is the persisted highwater.
- **Rollback floor:** an authenticated higher revision is retained in memory and, when configured, persisted before checking expiry or a requested pin; expired data establishes the floor without becoming usable.
- **Paths:** cache and lock paths must be regular files; Unix opens use `O_NONBLOCK` and `O_NOFOLLOW` and validate the opened descriptor before reading.
- **Failures:** cache write failures are errors rather than silent memory fallback. Removing cache state resets its highwater; memory mode loses its highwater on restart. Trusted local clocks are required.
- **Crashes:** a crash/cancellation during cache replacement can leave a harmless temporary file beside the cache; it is never treated as accepted data.

### Shared update downloads

- **Sharing:** concurrent update downloads are shared process-wide between independently built clients, including clients using different Tokio runtimes and the blocking API. The exact identity is the channel URL, effective update proxy configuration, target and proxy TLS root bytes kept in separate fields, and update timeout.
- **Workers:** a finite worker owns a current-thread Tokio runtime and a fresh update HTTP pool, independent of any subscriber's runtime. Canceling one subscriber or dropping its runtime leaves other subscribers running; the last subscriber signals cancellation.
- **Local acceptance:** only raw response bytes are shared: signature/trust, expiry, pin, rollback and cache acceptance remain local to each client. No signed state is accepted on another client's behalf.
- **Limits:** the process coordinator admits at most 16 active or draining workers and 1024 subscribers per download, with no worker admission queue; overload returns `ResourceLimit` before networking. Completed results and watch notifications are bounded by those limits.
- **Draining:** finished workers are joined without blocking the async executor; canceled workers retain their slots until they finish and are reaped on the next admission. A new request for a still-draining canceled channel can return `ResourceLimit`. Each worker has the configured update deadline and at most one Tokio blocking thread; the OS DNS caveat above remains.
- **Custom CAs:** each custom CA policy accepts at most 32 PEM inputs of at most 65536 bytes each. A PEM input may contain a certificate bundle; all its certificates belong only to the selected policy.

## 🍪 Cookies and limits

- **Cookie jar:** each solve owns a `cookie_store` jar while retaining the client's pooled connections, including the HTTPS-proxy pool. Domain, host-only, path, Secure and expiry rules apply; the permitted Domain scope is the request host or `google.com` from `www.google.com`.
- **Scope:** cookies never enter update requests or proxy CONNECT headers.
- **Budgets:** a solve processes at most 64 Set-Cookie fields with 16384 aggregate raw bytes; an outgoing Cookie header is also limited to 16384 bytes. Exceeding these budgets returns `ResourceLimit`.

## ⚙️ Configuration

| Option | Default | Meaning |
|---|---|---|
| `timeout` | 30 seconds | Total operation deadline |
| connect/read-idle deadlines | 5 seconds | Fixed per-stage bounds |
| `update_timeout` | 5 seconds | Update deadline |
| `max_in_flight` | 16 | Active operations |
| `max_response_bytes` | 1048576 | Response bytes |
| bundle bytes | 65536 | Update bundle size |
| attempts | one per GET/POST | No automatic retries |

Builders expose `timeout`, `update_timeout`, `max_in_flight`, and `max_response_bytes`. Deadlines cover data selection, cache locks, DNS, HTTP headers, body streaming and parsing.

## 🧵 Concurrency and cancellation

- **Blocking client:** owns a current-thread Tokio runtime and reusable HTTP pools. Create, use, and drop it on synchronous threads outside a Tokio runtime; calls made inside a Tokio runtime return `InvalidInput`. Use the async API for Tokio applications.
- **Async:** calls must run within a Tokio runtime with I/O/time enabled. Dropping an async solve future drops its owned request future and releases its concurrency permit.
- **Background work:** there are no periodic background refresh loops. Tokio provides DNS/filesystem blocking facilities.
- **Sharing:** calls may share a client; `max_in_flight` bounds active solves and update calls with no waiting queue, and excess calls return `ResourceLimit`.

> [!WARNING]
> DNS cancellation limitation in this development snapshot: reqwest uses the operating system resolver through Tokio's blocking pool. A returned async deadline/cancellation does not forcibly stop an already running OS DNS syscall. Dropping the blocking client may wait for that syscall to finish; a strict wall-clock bound on DNS teardown is not promised. This case was identified from the installed transport source, and a hung OS resolver was not simulated. No alternate DNS backend is introduced in this release. A shared-update worker retains its capacity slot until its runtime actually exits, even if OS DNS delays teardown.

## ⚠️ Limitations

- Validated here on Linux x86_64 with Rust 1.92 and 1.96. Other operating systems and CPU architectures remain unverified.

## 🔧 Development / verification

```sh
cargo fmt --check
cargo clippy --all-features --all-targets -- -D warnings
cargo test --no-default-features
cargo test --no-default-features --features async
cargo test --no-default-features --features blocking
cargo test --all-features
cargo test --all-features process_refresh_registry_limit_and_cleanup -- --ignored
cargo build --all-features
cargo package --allow-dirty
```

The registry exhaustion test runs separately so it does not consume other tests' global capacity. Tests run private loopback origins and proxies; production cannot override CAPTCHA origins. No test contacts a live CAPTCHA. Benchmark source and raw pilot measurements are in `benchmarks/` in the repository and excluded from published crate contents.
