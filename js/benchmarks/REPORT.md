# Node transport pilot

Generated from raw.json on 2026-11-16T12:09:08.592Z. Run: `npm run build && npm run bench`.

Runtime v22.22.1; external Undici 6.29.1; Axios 1.21.1; HTML attribute decoder html-entities 2.6.0.
OS linux 6.8.0-147-generic, x64; CPU AMD Ryzen 9 7950X 16-Core Processor.
TLS backend OpenSSL 3.5.5. Built-in fetch reports Undici 6.23.0.
CPU/memory control-group ancestors and descriptor limits are recorded verbatim in raw.json.

Each warm cell combines 3 interleaved runs of 90 successful requests after 15 sequential warmup requests.
All candidates use a keep-alive pool bounded to the declared concurrency, the same 72-byte API script over local HTTP, no redirects or retries, and a 5000ms deadline.
The child server runs outside the client's event loop. Cold runs create and close a new pool per request.
Quantiles and throughput below are calculated by this report script; raw per-request samples remain in raw.json.

| Candidate | Concurrency | Samples | p50 ms | p95 ms | p99 ms | Requests/sec | Errors |
|---|---:|---:|---:|---:|---:|---:|---:|
| fetch | 1 | 270 | 0.2393 | 0.3459 | 0.8006 | 4200.2 | 0 |
| fetch | 8 | 270 | 1.0970 | 1.9070 | 3.0770 | 6752.0 | 0 |
| fetch | 32 | 270 | 3.7064 | 5.1122 | 5.2016 | 7213.2 | 0 |
| undici.request | 1 | 270 | 0.1316 | 0.2001 | 0.2523 | 7403.1 | 0 |
| undici.request | 8 | 270 | 0.4941 | 0.7529 | 1.2468 | 15418.4 | 0 |
| undici.request | 32 | 270 | 1.5783 | 2.6180 | 2.7509 | 16977.6 | 0 |
| axios | 1 | 270 | 0.2199 | 0.3481 | 0.7932 | 4107.2 | 0 |
| axios | 8 | 270 | 1.1386 | 2.2764 | 2.6535 | 5979.8 | 0 |
| axios | 32 | 270 | 4.3785 | 7.6737 | 9.4419 | 6323.3 | 0 |

| Full local API | Concurrency | Samples | p50 ms | p95 ms | Errors |
|---|---:|---:|---:|---:|---:|
| full-api-async | 1 | 30 | 0.4982 | 0.8987 | 0 |
| full-api-async | 8 | 30 | 2.5364 | 3.6948 | 0 |
| full-api-async | 32 | 30 | 8.5970 | 9.1202 | 0 |
| full-api-sync | 1 | 30 | 0.7646 | 1.7563 | 0 |

This short pilot supports the provisional choice of `undici.request` for the direct local HTTP path.
Its adapter also passed local HTTP/HTTPS CONNECT, target TLS verification, proxy authentication, SOCKS local/remote DNS,
negative no-direct-fallback controls, bounded streamed/decompressed bodies, cancellation and lifecycle tests.
Those capability tests cover the selected adapter; raw fetch and Axios have not passed the same full capability matrix.
Axios would need an additional SOCKS agent; built-in fetch can share Undici dispatchers but has a different response/decompression API.
No claim is made that this selection is universally fastest or that the unsupported candidates satisfy the package contract.

Limits: this is a pilot, not statistically stable production load evidence. The sample count was fixed before measurement,
and was not chosen to force a winner. No server saturation test or controlled CPU quota run was performed.
Raw CPU times, observed RSS, thread/descriptor counts and new connection counts are included; RSS observations are not peak RSS.
Proxy/TLS/DNS performance, updater throughput, subprocess bridge comparisons, prolonged failure/resource growth, and real-site
acceptance remain unmeasured. The sync row measures a warm persistent worker; cold worker startup and teardown are not
isolated as performance profiles. No live captcha calls were made by this benchmark.
