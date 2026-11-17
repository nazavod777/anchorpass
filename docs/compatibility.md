# 🧭 Compatibility

[← Documentation index](README.md)

> **Development-candidate observations** — not a general support guarantee.

> [!NOTE]
> Package manifests declare minimum versions; a declaration does not prove that runtime was exercised.

| Package | Declared minimum | Locally exercised environment | Interface |
| --- | --- | --- | --- |
| <img src="../assets/c-python.svg" height="20" align="absmiddle" alt="Python"> | Python 3.11 | Python 3.11.18 and 3.13.15, Linux x86_64 | PycURL sync and aiohttp async |
| <img src="../assets/c-nodejs.svg" height="20" align="absmiddle" alt="Node.js"> | Node.js 22.0.0 | Node.js 22.0.0 and 22.22.1, Linux x64 | ESM/CommonJS; async and blocking worker bridge; Undici |
| <img src="../assets/c-go.svg" height="20" align="absmiddle" alt="Go"> | Go 1.23.0 | Go 1.23.0 and 1.25.5, Linux/amd64 | Context-aware calls, directly or in a goroutine; `net/http` |
| <img src="../assets/c-rust.svg" height="20" align="absmiddle" alt="Rust"> | Rust 1.92 | Rust 1.96 and standalone Rust 1.92, Linux x86_64 | Tokio async and optional blocking client; `reqwest` |

- **Local:** Go 1.23.0 passed vet, race tests and build locally.

> [!WARNING]
> Other operating systems and architectures remain unverified.

- **Remote:** the [remote baseline run](https://github.com/nazavod777/anchorpass/actions/runs/43932822800) passed all jobs for commit `51a5bd4597e1be9d3f0912c939526dbdccddd0d4`; that result does not qualify later changes.
- **Transport matrix:** the expanded transport matrix and its remaining capability gaps are recorded in the [benchmark report](../benchmarks/reports/speed-20261117/README.md). Historical runtime checks qualify their recorded snapshots; the current transport verification is described in [transport decisions](transport-decisions.md).

## 📋 Protocol and fields

- **Family:** all packages target the signed-data family `recaptcha-v3/1`.
- **Fixtures:** local fixtures cover Google `api2` and `enterprise` anchor/reload flows.
- **Live record:** the [multi-site live record](live-acceptance-multisite.json) covers all seven public API modes on Google, 2Captcha v3, 2Captcha Enterprise and ZennoLab. Google and standard 2Captcha passed the strict checks.

> [!WARNING]
> Enterprise acceptance is limited evidence because its verifier omits action; ZennoLab acceptance is degraded by a quota diagnostic. Neither is full release qualification. No acceptance on arbitrary applications is promised.

**Anchor URLs**

- Require HTTPS and `google.com` or `www.google.com`, a permitted `/recaptcha/api2/anchor` or `/recaptcha/enterprise/anchor` path, and nonempty `k` and `co`.
- Credentials, fragments and duplicate query members are rejected. Unknown query fields are retained.
- Public consumers cannot replace the captcha origin with an arbitrary server; loopback origins are private test fixtures.

**`bg` and action**

- `bg` is taken from the selected verified bundle unless overridden per solve. An override leaves the remaining bundle validation in force.
- Explicit action wins over URL `sa`; absent both, action is omitted. Action is forwarded as `sa` to anchor and reload.
- Helpers only extract what the URL contains; they cannot infer an arbitrary site's action.
- Per-call User-Agent overrides reach version, anchor and reload, without changing update requests or other operations. They do not enable TLS impersonation.
- Per-call hostname overrides replace only the site host in `co`, retaining the HTTP/HTTPS scheme and effective port. The same encoded origin reaches anchor and reload; the Google endpoint and site-key domain policy remain unchanged. See the [API contract](../spec/api.md) for validation rules.

## 🔌 Proxy behavior

| Scheme | Target DNS | Connection to proxy |
| --- | --- | --- |
| `http` | Proxy for CONNECT | Plain HTTP |
| `https` | Proxy for CONNECT | Verified TLS |
| `socks5` | Local | SOCKS5 |
| `socks5h` | Proxy | SOCKS5 |

- Proxy URLs require explicit host and port.
- HTTP Basic and SOCKS5 username/password use ASCII credentials with percent-encoded special characters decoded once.
- Environment proxy settings, including `NO_PROXY`, do not change routing. Failed proxies do not fall back to direct access.
- Local fixtures exercise positive TLS/CONNECT/SOCKS flows, DNS-policy distinctions and negative controls for direct fallback.

> [!NOTE]
> This does not certify every external proxy provider.

### 🔐 Update route and trust

- The update route inherits the captcha proxy by default. An explicit direct mode or separate proxy changes only update traffic.
- Updates have separate sessions/pools and do not carry captcha parameters or cookies. Redirects are rejected.
- Target and HTTPS-proxy certificate checks stay enabled; custom trust-root configuration differs by package.

All packages expose separate target and HTTPS-proxy trust:

| Package | Target trust | HTTPS-proxy trust |
| --- | --- | --- |
| <img src="../assets/c-python.svg" height="20" align="absmiddle" alt="Python"> | `ca_bundle` | `proxy_ca_bundle` |
| <img src="../assets/c-nodejs.svg" height="20" align="absmiddle" alt="Node.js"> | `ca` | `proxyCa` |
| <img src="../assets/c-go.svg" height="20" align="absmiddle" alt="Go"> | `WithRootCAs` | `WithProxyRootCAs` |
| <img src="../assets/c-rust.svg" height="20" align="absmiddle" alt="Rust"> | `add_root_certificate` (builder) | `add_proxy_root_certificate` (builder) |

- **Go:** defaults each independently to system roots; any custom pool makes update state client-local because certificate equality does not compare custom verification constraints.
- **Rust:** uses separate rustls configurations in a pooled Hyper adapter for HTTPS proxies, while retaining reqwest for other routes.
- **Checked:** same-host cross-trust negatives, both update policies, cancellation and credential isolation.

> [!NOTE]
> Raw benchmark adapters retain their historical limitations; they do not describe the completed Rust package adapter.

## ⚠️ Lifecycle and persistence limits

- Reuse clients for connection pooling.
- **Node:** the blocking client uses a worker and blocks the caller's event loop; use `closeAsync()` to confirm worker cleanup.
- **Rust:** the blocking client must be created, used and dropped outside a Tokio runtime. Async calls require a Tokio runtime with I/O and time enabled.

### DNS and deadlines

- Node and Rust use system DNS facilities that cannot forcibly interrupt an already running operating-system lookup. Rust blocking teardown can consequently wait for that lookup.
- Go's operation deadline returns promptly, but `net/http` can continue an already started connection attempt afterward. A stalled HTTPS-proxy TLS handshake remains bounded by its five-second connect timeout and configured connection cap; `Client.Close()` cancels pending owned attempts. Independent tests observed both the deadline return and eventual socket EOF.

> [!WARNING]
> A strict DNS teardown bound is not claimed. Immediate socket teardown from the operation deadline alone is not promised.

### Persistence

- Memory cache loses rollback history after process restart.
- Persistent caches are opt-in; corruption and write failures do not silently switch to memory. Removing cache state resets its stored revision floor.
- Go's persistent lock adapters compile for several Unix platforms, but only Linux was exercised; other platforms use memory mode and reject unsupported disk locking.
- See each package README for its lock and cancellation details.

> [!NOTE]
> There is no public registry installation or deployed update-channel compatibility claim. Code changes are required for incompatible protocol logic, schemas or new trust roots that the installed package does not already accept.
