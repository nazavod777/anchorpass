# 🚚 Transport decisions from the expanded local matrix

[← Documentation index](README.md)

> **November 17, 2026** — select transports using complete request flows,
> capability controls and the public package API, then verify the shipped change.

The [baseline](../benchmarks/reports/speed-20261117/README.md) compares 21 raw
transport modes and eight package API modes. All 1353 jobs completed with
unchanged source/dependency hashes and no harness failures. Failed and
unsupported negative controls remain visible. The
[post-migration Python report](../benchmarks/reports/speed-20261117-python-api/README.md)
records the final implementation separately.

## ✅ Selected clients

| Package/mode | Decision | Reason |
| --- | --- | --- |
| Python sync | PycURL | Passed all seven raw controls and the package contract. The full public API confirms a benefit with reused clients. |
| Python async | aiohttp with aiohttp-socks | Passed the controls, cancellation, proxy trust and update lifecycle tests. The public API confirms a reused-client benefit. Production uses documented APIs and its bounded idempotent retry; no private retry switch is used. |
| Node.js async/sync core | Retain Undici request | Lower overhead than fetch, Axios and native HTTPS in the principal warm flow profiles. Sync retains the worker bridge; reusing that client avoids worker startup per solve. |
| Go | Retain net/http | fasthttp's direct-request improvement did not establish a proxy-flow advantage: HTTP CONNECT medians were 1.637 vs 1.585 ms, HTTPS 1.676 vs 1.709 ms, with overlapping round ranges. Resty wraps the same net/http backend. fasthttp also needs additional cancellation/lifecycle work to preserve the package contract. |
| Rust async/blocking | Retain reqwest and the pooled Hyper/rustls HTTPS-proxy adapter | Reqwest was competitive in the full flow; alternatives did not establish a qualified improvement. Raw Rust adapters lack independent proxy/target trust and remain explicitly unsupported there. The production HTTPS-proxy adapter has its own passing trust and lifecycle tests. |

The comparison includes Requests, HTTPX, curl_cffi, urllib3, PycURL and aiohttp;
Undici, global fetch, both Axios adapters, native HTTPS and Got; net/http, Resty
and fasthttp; reqwest async/blocking, isahc and ureq. Exact versions and adapters
are in the raw records. fasthttp 1.65.0 is the benchmark version compatible with
the project's Go 1.23 minimum; it is not a production dependency.

## 📊 Final Python API measurements

HTTP CONNECT with verified TLS on a loopback fixture, three rounds and 384
measured solves per cell. Preparation is outside timing. Each sync worker owns
a client; async workers share one client. These are separate sequential runs
before and after migration, not an interleaved randomized A/B experiment.

| Profile | curl_cffi sync → PycURL | curl_cffi async → aiohttp |
| --- | ---: | ---: |
| Reused client, concurrency one | 0.995 → 0.683 ms | 1.119 → 0.866 ms |
| Reused client, concurrency eight | 7.591 → 2.982 ms | 5.966 → 4.124 ms |
| Construct + solve + close, concurrency one | 2.044 → 2.083 ms | 2.292 → 2.418 ms |

At concurrency eight, observed median latency fell by **60.7% sync** and
**30.9% async**. All 18 post-migration jobs completed with zero failed samples,
zero validation errors and unchanged source hashes. Quantiles, throughput, CPU,
RSS and round ranges are retained in the
[comparison data](../benchmarks/reports/speed-20261117-python-api/comparison.json)
and [interactive comparison](../benchmarks/reports/speed-20261117-python-api/comparison.html).

The tradeoff is explicit: cold API medians increased, and sampled process peaks
at concurrency eight rose from 45.77 to 62.26 MiB for sync and from 45.06 to
59.05 MiB for async. Reuse a client for repeated solves. This migration targets
reused-client performance; it does not make every workload faster or reduce RAM.
The cold profile includes client construction and close after preparation, not
process startup/import. RSS includes the whole process lifetime.

## 🔐 Contract and implementation

Python uses a bounded PycURL handle pool for synchronous calls and a bounded,
lazily created aiohttp session for asynchronous calls. aiohttp also downloads
updates through a separate session. curl_cffi remains a development-only raw
benchmark candidate and is absent from runtime dependencies.

The existing flow and update coordinator are unchanged. Total deadlines,
operation-local cookies, response limits, local/remote SOCKS DNS, independent
proxy/target roots and rejection of ambient proxies remain verified.

The migration also closes a pre-existing libcurl POST replay defect: a consumed
reload followed by a connection reset could trigger another POST. Nonempty POST
uploads now refuse rewinding; empty POST requests use a fresh connection. Tests
count request lines, headers and bodies, not only successful responses. CONNECT
response cookies cannot become target cookies, proxy exceptions are sanitized,
and mixed-case proxy schemes preserve routing and credentials.

Per-call User-Agent and hostname are available in all four packages; see the
[API contract](../spec/api.md). Hostname changes only the application host encoded
in `co`, preserving scheme and effective port. It never changes the Google
endpoint, site key or permitted domains. No option guarantees acceptance or score.

## 🧪 Verification

- Python: canonical Ruff, formatting, mypy, 180 tests, wheel/sdist build and both
  outside-checkout installation checks passed on Python 3.13.15. Python 3.11.18
  passed the same 180 cases across the main run and three subsequently added tests.
- Node: lint, typecheck, build, 43 tests and installed tarball checks passed;
  the 43 tests also passed on Node 22.0.0.
- Go: formatting, vet, race tests, build and archive installation passed; the
  production gate also passed on Go 1.23.0.
- Rust: formatting, Clippy, all feature combinations, the isolated registry test,
  build and crate installation passed; the full gate also passed on Rust 1.92.
- Shared contract/data checks, release regressions, benchmark, pressure and live
  helper gates passed. Independent code, package and transport security reviews
  accepted their checked scopes.

Sources used by each measurement are preserved as SHA-256-verified archives in
its report directory. The baseline snapshot includes 113 unique source/dependency
paths; the post-migration snapshot includes 114. The later Node mixed-padding
validation fix did not affect measured requests without a hostname override.

## ⚠️ Interpretation limits

The fixture negotiates HTTP/1.1 on loopback. It does not measure Internet latency,
HTTP/2 or HTTP/3, live CAPTCHA quality, other platforms or long-term leak freedom.
The existing [live records](implementation-status.md) and
[pressure evidence](pressure-results.md) identify older snapshots and do not
silently qualify the new Python transports.

Delays can erase local overhead differences. Undici did not win every profile;
Got had the smallest delayed-flow median, with round variation. Raw Rust uses a
smaller fixture parser than its package API. Small cross-language differences
cannot be attributed solely to the HTTP library.

Fixture CPU fraction covers the entire adapter lifetime, including imports,
warmup and cleanup. A low lifetime average does not rule out a server ceiling
during timed samples. RSS is externally sampled and can miss brief peaks.
No universal fastest-client or CAPTCHA-score guarantee is claimed.
