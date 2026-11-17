# 📊 Transport pilot

> **Rust transport pilot** — a bounded reqwest versus isahc comparison on a plaintext loopback fixture, run with rustc 1.96.0 on Linux x86_64.

## 🔧 Method

Command from this directory: `cargo run --locked --release > pilot.csv`.

- **Fixture:** `transport.rs` contains the exact local HTTP fixture and alternating candidate loop.
- **Dependencies:** `Cargo.lock` pins benchmark-only dependencies; isahc is not a library dependency.
- **Summary:** `summary.json` contains computed quantiles and runtime/limit metadata.
- **Load:** one cold request, then 100 warm sequential keepalive GET requests per candidate; response is two bytes, plaintext loopback.
- **Clients:** both ignore environment proxies, use a five second timeout, and read the entire body.
- **TLS:** no TLS certificate checks were disabled.

## 📊 Results

Measured warm p50/p95/p99 (microseconds): reqwest 0.12.28: 59/89/105;
isahc 1.8.3: 106/136/155. Cold requests: 303 and 337 microseconds, respectively.
All 202 requests succeeded.

> [!NOTE]
> These are a bounded pilot's raw observations, not a
> universal speed ranking or statistical claim. Concurrency, proxy latency, CPU/RSS,
> full protocol overhead and external network performance were not benchmarked.

## ✅ Decision

Reqwest was initially selected for the shared async and blocking core because its
local pilot was satisfactory and its tested adapter passes the Rust conformance
suite: HTTP CONNECT, TLS-to-proxy and independent target TLS validation, authenticated
SOCKS5 local/proxy DNS, explicit environment isolation, cancellation, bounded body
streaming, and separate update routing. Isahc has not run that full capability suite,
so this is not evidence that it lacks those capabilities. The blocking API drives the
same asynchronous client with an owned current-thread runtime, preserving the same
proxy semantics without detached network work. The package now uses a narrow pooled
Hyper/rustls adapter for HTTPS proxies because reqwest shares target/proxy TLS roots.
The adapter has separate immutable TLS policies, a bounded CONNECT header, no retries,
bounded streaming, and cancellation that closes CONNECT/target-handshake sockets.
Same-host certificate cross-trust failures are covered by crate regression tests.

> [!WARNING]
> Release readiness still requires controlled site-side acceptance and broader
> repeatable transport profiles from PLAN.md. No such live check was performed.

## 🧭 Local matrix adapter (new)

The historical pilot above remains a separate binary. The matrix uses the shared
[`CONTRACT.md`](../../benchmarks/CONTRACT.md), one process per JSON job, and only
loopback targets/proxies. Build from the repository root:

```sh
cargo build --locked --release --manifest-path rust/benchmarks/Cargo.toml --bin transport-matrix
cargo build --locked --release --manifest-path rust/benchmarks/api/Cargo.toml --target-dir rust/benchmarks/target
rust/benchmarks/target/release/transport-matrix --job /absolute/job.json --output /absolute/result.json
# Use this separate binary for rust-api-async and rust-api-blocking:
rust/benchmarks/target/release/transport-matrix-api --job /absolute/job.json --output /absolute/result.json
```

### 🗂️ Candidates

Raw candidates are `rust-reqwest-async`, `rust-reqwest-blocking`, `rust-isahc`, and
`rust-ureq`. Both API candidates use the public local `anchorpass` crate. The API
manifest has an independent lockfile and does not enable reqwest gzip, preserving
the package's transport feature set. The raw manifest enables gzip to enforce the
limit on decompressed bodies. Do not enable `transport-gzip` on the API manifest.
API builders supply target and proxy roots separately and select the requested HTTP
or HTTPS proxy. The API adapter accepts successful `http` and `https` routes; fault
controls for the HTTPS adapter run in crate tests rather than this timing harness.

### 📊 Measurement

Async candidates use bounded native Tokio tasks on one current-thread runtime;
synchronous candidates use exactly the configured bounded worker threads. Reqwest
blocking and isahc also maintain their own library I/O threads, visible in external
resource sampling. Warmup is distributed across workers. A shared barrier prevents any timed request
from starting before every worker finishes warmup.
Cold samples include client construction and cleanup; warm samples reuse clients.
All bodies are streamed into bounded buffers; reqwest blocking requests explicitly
set a per-request timeout so the asynchronous body retains the total deadline
instead of relying on the blocking builder's timeout, which resets per `Read`.

### 🔧 API preparation

API preparation explicitly refreshes a fresh signed bundle into a temporary
persistent cache before warmup/timing; timed clients use that cache offline. Cold
construction/cache read and protocol operations are timed, update networking is
excluded. Synchronous API workers have separate prepared cache files. Preparation
errors are harness errors. No embedded-bundle expiration is required for selection.
Temporary cache state is removed before output is written. `wall_ns` and `cpu_ns`
cover only the measured batch, including start/end barrier coordination. Process
CPU uses `CLOCK_PROCESS_CPUTIME_ID` across all process threads. Snapshots are taken
before permitting recovery and cleanup; preparation and warmup are excluded. Failed samples
remain failures even when recovery succeeds.

### ⚠️ Adapter limitations

> [!NOTE]
> These are exact adapter limitations, not claims that a different integration is impossible.

- HTTPS-proxy and nested target/proxy trust controls return `unsupported` for raw
  transport candidates: no independent proxy-CA option is exposed there. Reqwest 0.12.28
  `connect.rs` clones the same rustls config/root store for proxy TLS. Isahc 1.8.3
  `Configurable` exposes `ssl_ca_certificate` but no proxy CA option; this adapter
  does not bypass its public interface to configure a raw libcurl handle. Ureq
  3.1.4 uses one `TlsConfig` in this adapter. Trust stores are never unioned.
- Isahc CONNECT authentication rejection returns `unsupported`: the observed
  `ConnectionFailed`/curl code 7 does not establish an authentication error. It is
  not relabeled from the requested negative-control category.
- Ureq SOCKS routes explicitly use `ProxyBuilder::resolve_target`: `true` for
  `socks5`, `false` for `socks5h`, independent of its URL parser's remote-DNS default.
- Public API clients do not maintain cookies; the loopback fixture validates the
  stateless challenge flow. Raw adapters reject redirects and disable ambient
  proxy selection, including isahc's inherited no-proxy blacklist.

### 🧪 Verification

Verification for the matrix is scoped to these benchmark crates. Root package
conformance gates remain the repository owner's responsibility:

```sh
cargo fmt --manifest-path rust/benchmarks/Cargo.toml --check
cargo clippy --locked --manifest-path rust/benchmarks/Cargo.toml --all-targets -- -D warnings
cargo test --locked --manifest-path rust/benchmarks/Cargo.toml --bin transport-matrix classification_tests
cargo fmt --manifest-path rust/benchmarks/api/Cargo.toml --check
cargo clippy --locked --manifest-path rust/benchmarks/api/Cargo.toml --all-targets --target-dir rust/benchmarks/target -- -D warnings
cargo test --locked --manifest-path rust/benchmarks/api/Cargo.toml --bin transport-matrix-api --target-dir rust/benchmarks/target classification_tests
```

Loopback smoke jobs exercise successful routes, both real APIs, expected faults,
and recovery without using their timings for rankings. Shared runner measurements
must run sequentially after builds and checks finish.

### 🚨 Error classification

Classification uses explicit network I/O kinds and typed HTTP/TLS/libcurl causes.
Unknown adapter, configuration, input, or local I/O errors remain `unclassified`,
and can never satisfy a negative control. `BodyExceedsLimit` is a response-limit
error. Reqwest's private tunnel-auth variant is identified by its exact pinned
hyper-util display message inside a typed connect error; arbitrary matching text
and broad catch-all transport classifications are not accepted.

### Three-request raw flow

`kind: flow` runs version GET, anchor GET and reload form POST for each raw
candidate, using one total deadline. The version and challenge come from the
fixture responses; form fields come from `fixture.flow_fields`, with selected
protocol fields overwritten as in the package. Each operation starts without a
cookie and carries only its fresh fixture operation cookie across its three
same-origin requests. `flow_delay_ms` delays each step. This synthetic raw flow
uses the fixture's exact HTML shape, without reproducing the package's full HTML5
parser, signature verification or cache work; public API profiles remain separate.
Raw HTTPS-proxy trust limitations still apply to flow profiles.

No additional native Hyper candidate was introduced: Hyper is the HTTP protocol
layer already underlying reqwest and the production HTTPS-proxy pool. A complete
standalone all-route client would require new pooling, DNS, proxy, TLS, deadline,
body and cookie integration and qualification. Comparing an unqualified direct-only
microclient would not identify a suitable replacement for the current contract.
