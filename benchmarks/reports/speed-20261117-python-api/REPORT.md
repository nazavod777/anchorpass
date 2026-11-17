# Local TLS/proxy transport matrix

Original evidence: `/tmp/anchorpass-speed-root-20261117/post-python-api`. Reproduce with `make bench-matrix BENCH_OUTPUT=/tmp/anchorpass-matrix-new`, then `benchmarks/report_matrix.py`. Full per-job results are retained in `raw.json.gz`; synthetic proxy credentials are omitted from that distributable copy.

These observations come from one baseline run.

Recorded jobs: 18; executed: 18; unsupported: 0; invalid harness jobs: 0. Measured observations: 2304; failed: 0. Recovery observations: 0; failed: 0.

Each performance cell combines independently launched processes across the configured rounds. p50/p95/p99 use successful samples only; failures remain separately visible and disqualify capability controls. Quantiles are descriptive. Round median ranges expose variation; there is no confidence-interval or universal-winner claim. Peak RSS includes startup, warmup and cleanup and is externally sampled, not an exact high-water mark.

## Capability outcomes

`passed` means the expected negative result and successful recovery. `unsupported` is unverified in this exact adapter, not a pass. A raw candidate qualifies only when every required control passes and its selected workload has no failures. These are candidate-adapter qualifications, distinct from package conformance tests.

| Candidate | total-deadline | bad-proxy-auth | separate-target-trust | separate-proxy-trust | decompressed-body-limit | rate-limit | connection-reset |
| --- | --- | --- | --- | --- | --- | --- | --- |
| python-api-async | missing | missing | missing | missing | missing | missing | missing |
| python-api-sync | missing | missing | missing | missing | missing | missing | missing |

## api-cold-c1

| Candidate | Samples/failures | p50 / p95 / p99 ms | Round p50 range ms | ops/s | CPU ms/op | Sampled peak MiB |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| python-api-async | 384/0 | 2.418 / 2.684 / 2.859 | 2.374–2.488 | 407.7 | 1.581 | 57.35 |
| python-api-sync | 384/0 | 2.083 / 2.341 / 2.433 | 2.056–2.158 | 476.3 | 1.168 | 60.76 |

## api-warm-c1

| Candidate | Samples/failures | p50 / p95 / p99 ms | Round p50 range ms | ops/s | CPU ms/op | Sampled peak MiB |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| python-api-async | 384/0 | 0.866 / 1.040 / 1.134 | 0.829–0.910 | 1139.0 | 0.628 | 56.59 |
| python-api-sync | 384/0 | 0.683 / 0.840 / 0.945 | 0.663–0.702 | 1394.1 | 0.468 | 60.77 |

## api-warm-c8

| Candidate | Samples/failures | p50 / p95 / p99 ms | Round p50 range ms | ops/s | CPU ms/op | Sampled peak MiB |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| python-api-async | 384/0 | 4.124 / 4.245 / 4.313 | 4.118–4.139 | 1930.8 | 0.518 | 59.05 |
| python-api-sync | 384/0 | 2.982 / 4.143 / 4.832 | 2.902–3.034 | 2552.7 | 0.704 | 62.26 |

## Interpretation limits

- All traffic is loopback and uses verified TLS. The fixture blocks external forwarding. No public service was load-tested.
- Node clients share the qualified TLS/proxy connector. Fetch, Undici and Axios/fetch share Undici HTTP machinery; native HTTPS, Axios/http and Got use node:https. Resty shares net/http; fasthttp is a separate HTTP backend. These distinctions are part of the comparison.
- Flow profiles measure version GET, anchor GET and reload POST as one operation, with one total deadline, per-operation cookies and the same fixture protocol fields. Delayed flow profiles apply the configured delay to every step. Raw requests, full flows and public package APIs are reported separately.
- Raw adapters use their language's parsers; Rust uses a minimal fixture parser while package API profiles use the production parser. Small cross-language differences cannot be attributed solely to the HTTP library.
- Sync transports use bounded workers; async uses native tasks. Node blocking concurrency one only. Runtime/framework costs and thread topology are part of those measured configurations.
- Warm means a reused client after warmup; cold includes construction and cleanup. Update preparation is outside solve timing. Package API settings and cache strategies are documented in each adapter README.
- Raw Rust compression features are isolated from the API binary. Unsupported raw-adapter TLS/proxy controls remain explicit capability gaps; package-level trust qualification is separate.
- Python requests/HTTPX sync idle timeouts can fail the total deadline. HTTPX may retain a failed nested-TLS connection slot; the repeat-failure control records the actual result. Aiohttp's no-retry adapter uses a version-pinned internal switch.
- Go operation timeout returns promptly; a detached net/http handshake can live until its bounded connect timeout or Client.Close. This is separately tested and documented.
- Fixture CPU fraction and loop lag are included in summary.json. High fixture load or overlapping round medians prevent attributing small differences solely to a client.
- Fixture CPU fraction covers the entire adapter lifetime, including startup, warmup and cleanup; its low average cannot rule out a fixture ceiling during the measured batch.
- These finite batches do not prove long-term leak freedom, live acceptance, platform portability or update-server resilience. Package conformance supplies separate evidence.

## Unsupported adapter explanations


## Environment

```json
{
  "platform": "Linux-6.8.0-147-generic-x86_64-with-glibc2.39",
  "machine": "x86_64",
  "processor": "x86_64",
  "python": "3.13.15",
  "cpu_count": 32,
  "affinity_cpus": [
    0,
    1,
    2,
    3,
    4,
    5,
    6,
    7,
    8,
    9,
    10,
    11,
    12,
    13,
    14,
    15,
    16,
    17,
    18,
    19,
    20,
    21,
    22,
    23,
    24,
    25,
    26,
    27,
    28,
    29,
    30,
    31
  ],
  "cpu_model": "AMD Ryzen 9 7950X 16-Core Processor",
  "cpu_max": null,
  "memory_max": null,
  "nofile": [
    65535,
    65535
  ],
  "fixture_connections_limit": 512,
  "fixture_io_timeout_seconds": 5,
  "dependency_hashes": {
    "requirements-dev.lock": "1637ec37f748500703178b1749695eac6330865359ae979ebadf4193fd0f53f9",
    "python/pyproject.toml": "4b9c437b6556517d44945ee5c24ac48d8ef4d9e8d019d0a3c438d2a666c158df",
    "js/package.json": "706f6d85c07122c7914312b3c4e5c06e9d9a892fa1ee64c6ccd07213d194177a",
    "js/package-lock.json": "978a682f0ae0f65e83dc4af44f4cd15fb78775fe4a5000dd1492032cac708156",
    "go/go.mod": "627bacfe567904dd2ee1dcd1fe913d0194586fe105f66165a08c1d6ae71433c9",
    "go/go.sum": "c291f8eefae98a8ce17bd2176f26a724fb604f5ef92ceb4ef68a4be6aaf972ce",
    "go/benchmarks/go.mod": "b81707de4b70d83c6b2098fc5002633b14a35f69986254f664509b6c80d11bfd",
    "go/benchmarks/go.sum": "3130879c421238995844a0347510a1ed46a6d70d8add41e74b6913eec25462c3",
    "rust/Cargo.toml": "c7d86f5298c7127da6192365ba1cde6919086abcc9107b7bb2a441903e06e51f",
    "rust/Cargo.lock": "6142f742005ea87a274a2a0e0290b681f8416ba00931cda15e55d768e6505a9e",
    "rust/benchmarks/Cargo.toml": "81db5642f3859813c2816c9ea2cc7cebfd27c3c6807e194135764855bb25265e",
    "rust/benchmarks/Cargo.lock": "8dc613baa916a471756f846d6773987486f4077d02e97b3ebb00ed98601de394",
    "rust/benchmarks/api/Cargo.toml": "e7c59326841b4bac1408660e02cef675ae0fd9a4a97af559453d2b12088c9338",
    "rust/benchmarks/api/Cargo.lock": "71cb6f8e5170eba5305a93ed9548d1c0939d85051b8f560d0a778af7004bb036"
  }
}
```
