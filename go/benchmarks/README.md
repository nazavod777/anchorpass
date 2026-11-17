# 📊 Local transport pilot

> **Go transport pilot** — net/http versus Resty on a loopback fixture, measured 2026-11-16T11:37:48Z with go1.25.5 on linux/amd64.

Resty v2.16.5 is a benchmark-only dependency.

## 🔧 Method

Reproduce from this directory: `go run .`.

- **Fixture:** serves a fixed 1024-byte HTTP response on loopback.
- **Deadline:** requests have a one-second context deadline.
- **Shared setup:** both candidates use the same bounded `net/http.Transport`, disabled redirects, no environment proxy, no retries configured, and the same response reader.
- **Warmup:** each profile warms up before measurement.
- **Order:** candidate order alternates across rounds.
- **Cold:** a fresh connection/pool per request; requester construction happens before the timed request.
- **Warm:** a reused client.

## 📊 Results

| Candidate | Profile | Concurrency | Median p50 (ns) | Median p95 (ns) | Median p99 (ns) | Median operations/s | Errors |
|---|---|---:|---:|---:|---:|---:|---:|
| net/http | warm | 1 | 32222 | 63711 | 184049 | 26428.47 | 0 |
| resty | warm | 1 | 36078 | 79772 | 159452 | 23046.01 | 0 |
| net/http | warm | 8 | 193647 | 501421 | 581793 | 35438.88 | 0 |
| resty | warm | 8 | 219346 | 492514 | 621698 | 33137.80 | 0 |
| net/http | warm | 32 | 914373 | 2819315 | 4106896 | 27202.08 | 0 |
| resty | warm | 32 | 889386 | 2821399 | 3740000 | 27770.32 | 0 |
| net/http | cold | 1 | 96753 | 233673 | 290802 | 8258.56 | 0 |
| resty | cold | 1 | 100300 | 137821 | 304267 | 8312.87 | 0 |
| net/http | cold | 8 | 392614 | 970029 | 1341163 | 15030.80 | 0 |
| resty | cold | 8 | 402533 | 880048 | 1114533 | 15640.55 | 0 |
| net/http | cold | 32 | 1291800 | 3953716 | 4994730 | 18179.18 | 0 |
| resty | cold | 32 | 1107109 | 4497617 | 5646154 | 15997.24 | 0 |

The program recorded 36 profile runs and 7200 measured requests, with 0 errors. Raw per-request observations are in `reports/samples.json`; summaries include process CPU and end-of-profile heap, goroutine and file-descriptor snapshots.

> [!NOTE]
> These snapshots are not peak measurements.

## ✅ Decision

Keep the library on `net/http`, whose proxy, DNS, cancellation, body limits, and lifecycle adapters are exercised by the package tests. This pilot shows mixed results and does not establish a speed winner. Resty wraps the same transport and adds dependencies without providing a capability needed by this implementation.

## ⚠️ Limitations

> [!WARNING]
> This is a short shared-host pilot with no confidence interval, latency injection, TLS/proxy benchmark, full Solve benchmark, upstream request, throughput saturation proof, or peak-memory capture. Resty was evaluated as a request wrapper, not independently qualified for the complete Anchorpass transport contract. Production selection remains provisional.

An inspection after the run found no CPU quota in the readable cgroup ancestor chain, a memory limit of 62711640064 bytes, a process limit of 13250, and an open-file limit of 65535. These observations are recorded separately from timing metadata and are not peak usage measurements.
