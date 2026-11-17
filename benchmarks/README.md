# 📊 Local transport and package matrix

> **Loopback-only benchmark runner** — extends the historical pilots with a common TLS fixture, authenticated HTTP/HTTPS CONNECT, SOCKS DNS-policy checks, controlled failures, recovery, raw samples and external process resource observations.

All requests stay on loopback.

> [!NOTE]
> It does not measure live CAPTCHA acceptance.

## 🔧 Run

Install the locked development dependencies and toolchains listed in the root
README, then run from the repository root:

```sh
make test-benchmark-adapters
make bench-matrix-smoke BENCH_OUTPUT=/tmp/anchorpass-smoke-new
make bench-matrix BENCH_OUTPUT=/tmp/anchorpass-matrix-new
.venv/bin/python benchmarks/report_matrix.py /tmp/anchorpass-matrix-new/raw.json --output benchmarks/reports/matrix
```

- **New output only:** output directories must be new; evidence is never overwritten or mixed with an earlier run.
- **Build order:** the runner builds all selected adapters before starting a fixture, then runs jobs sequentially.
- **Quiet host:** do not run builds, tests or another benchmark on the same host during comparative measurement.
- **Smoke:** a smoke reduces operation counts and uses concurrency one; it verifies wiring, not the full concurrency matrix or a ranking.
- **Subsets:** select candidates/profiles with `--candidates` and `--profiles` when reproducing a particular observation.

## 📋 Workload design

[`profiles.json`](profiles.json) fixes workload budgets before observing results;
[`CONTRACT.md`](CONTRACT.md) defines adapters and error semantics.

- **Isolation:** each measured performance job has a separate process, warmup, bounded worker count and one measured batch.
- **Order:** candidate order rotates across rounds.
- **Concurrency model:** sync-only transports use bounded threads; native async candidates use bounded tasks.
- **Cold:** includes client construction and close.
- **Separation:** single HTTP requests, complete three-request flows and real package API results are separate.
- **Flow:** fetch version, fetch anchor with an operation-local cookie jar, then POST reload with that cookie and the fixture's protocol fields. One deadline covers all three requests. Delayed flow profiles delay every step.
- **Node:** the blocking bridge is measured only at concurrency one.
- **API updates:** update preparation is excluded from solve samples and uses fresh locally signed data, avoiding dependence on the embedded bundle's expiry.

## 📊 Measurements

- The parent records `/proc` RSS, threads and file descriptors at a fixed sampling interval.
- Peaks include process startup, setup, warmup, recovery and cleanup, and can miss short spikes.
- Adapter CPU/wall figures cover only the measured batch; barrier coordination is included.
- Fixture CPU and event-loop lag help identify a server ceiling.
- Fixture CPU fraction covers the adapter's whole lifetime, including imports, setup, warmup and cleanup. A low lifetime average does not rule out saturation during the measured batch.

> [!NOTE]
> End-of-process resource snapshots are diagnostics, not proof of
> zero memory leaks or long-term stability. Tail quantiles from these finite samples
> are descriptive. Different workloads can prefer different clients.

## ✅ Evidence and results

Every job retains its inputs, raw observations, command, original exit status,
stdout/stderr and fixture counters outside the packages.
The runner also records source and dependency hashes before and after measurement,
writes an exit record and rejects source changes during a comparative run.

- `status: unsupported` is a capability gap, never a successful test.
- A failed negative observation can be a valid result: for example, an idle timeout that permits a slow-drip response does not satisfy the total deadline.
- Harness validation failures instead invalidate that job and make the runner exit nonzero.

> [!WARNING]
> Do not rank a client as qualified when
> its required capability controls fail or are unsupported.

## 🗂️ Adapters

Adapters and their extra dependencies are benchmark-only.

- **Python:** Requests, HTTPX, curl_cffi, urllib3, PycURL and aiohttp are compared in their synchronous or asynchronous modes. Requests and urllib3 use PySocks; HTTPX uses httpx-socks. Aiohttp uses aiohttp-socks for CONNECT/SOCKS with separate proxy and target TLS contexts. Its pinned raw adapter disables the internal `_retry_connection` switch to meet the no-retry workload. The API candidate retains aiohttp's bounded retry for idempotent methods; reload POST is never retried.
- **Node:** Undici request, global fetch and Axios's fetch adapter use Undici. Native `node:https`, Axios's HTTP adapter and Got use `node:https.Agent`. All share the qualified Anchorpass connection hook for CONNECT/SOCKS and independent TLS trust.
- **Go:** Resty and net/http share the qualified Go transport; fasthttp has a separate adapter and connection pool.
- **Rust:** raw and API binaries use separate manifests to avoid additive compression features changing the production API baseline.

See each language's benchmark README for limits.

## ⚠️ Out of scope

The matrix does not replace package conformance, installation checks, controlled
server-side acceptance, broader platforms, update-loader concurrency tests or
release operations. Those have their own evidence and completion criteria.
