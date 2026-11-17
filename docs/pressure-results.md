# 📊 Finite loader and resource-pressure results

[← Documentation index](README.md)

> **The four public async/concurrent APIs completed the local TLS/proxy workload** — adding loader cost, repeated failure/recovery, admission overload, DNS controls and finite resource observations.

> [!WARNING]
> This does not establish production longevity or live CAPTCHA acceptance.

## ✅ Main run

- **Scale:** 12 isolated candidate rounds and 20652 operations, with original runner exit status zero.
- **Per round:** 249 fixed operations plus a minimum ten-second paced solve loop. Candidate order rotated.
- **Overload:** two operations were admitted, and six of eight overload calls were rejected before admitted calls finished.
- **Recovery:** all deadlines, proxy failures and overload batches recovered.
- **Routing:** failed proxies generated no origin request. Remote-only DNS succeeded through SOCKS5h and failed under local DNS.

## 📊 Loader timings

- The reused bg profile includes signed disk cache reads and verification: it is not a pure in-memory lookup.
- Cold-cache samples include first load on a new client, excluding construction.
- Forced refresh includes network transfer, verification and atomic cache publication.
- Warm and offline cache loads generated zero fixture requests.

| API | Reused bg p50 / p95 (µs) | Cold signed cache p50 / p95 (µs) | Forced refresh p50 / p95 (µs) |
| --- | ---: | ---: | ---: |
| <img src="../assets/c-python.svg" height="20" align="absmiddle" alt="Python"> | 90.401 / 123.825 | 120.008 / 141.218 | 6065.982 / 6690.947 |
| <img src="../assets/c-nodejs.svg" height="20" align="absmiddle" alt="Node.js"> | 268.841 / 343.853 | 450.525 / 647.338 | 7036.865 / 8295.853 |
| <img src="../assets/c-go.svg" height="20" align="absmiddle" alt="Go"> | 69.682 / 97.795 | 81.775 / 113.605 | 3794.651 / 4016.953 |
| <img src="../assets/c-rust.svg" height="20" align="absmiddle" alt="Rust"> | 98.087 / 115.499 | 101.072 / 119.947 | 5025.326 / 5574.860 |

The per-API sample counts are 192 reused loads, 48 cold-cache loads and 48 forced refreshes. p99 and raw samples are retained.

> [!NOTE]
> These are descriptive timings, with no transport ranking: another agent reported overlapping Rust gates/builds. The changed Rust code concerned HTTPS-proxy trust, whereas this workload used HTTP CONNECT. No isolated causal CPU profile was collected.

## 🧪 Expiry follow-up

The original expired envelope reused revision 2 with different payload bytes. Python/Node reported expiry; Go/Rust rejected the same-revision conflict first. That result alone did not prove their expiry path.

- **Follow-up:** a focused follow-up used expired revision 3: every API returned exactly ConfigExpired for eight explicit refreshes, then loaded the valid revision-2 fallback eight times. All four seed refreshes also passed: 68 operations total.
- **Historical:** these are historical observations of the measured snapshot. The subsequent repository review identified that fallback as a rollback-contract defect: an authenticated higher revision establishes the floor even when expired. Current cases use `expiry_semantics=monotonic-floor-v1`, require rejection of the older snapshot, and recover with a newer valid revision.

> [!NOTE]
> The archived timings and operation counts have not been rewritten to claim coverage of that fix.

## 📊 Resources after close

| API | FD after close, rounds | Threads after close, rounds | RSS after close (MiB), rounds |
| --- | --- | --- | --- |
| <img src="../assets/c-python.svg" height="20" align="absmiddle" alt="Python"> | 6, 6, 6 | 2, 2, 2 | 41.930, 41.949, 42.004 |
| <img src="../assets/c-nodejs.svg" height="20" align="absmiddle" alt="Node.js"> | 21, 21, 21 | 11, 11, 11 | 112.168, 113.367, 110.758 |
| <img src="../assets/c-go.svg" height="20" align="absmiddle" alt="Go"> | 5, 5, 5 | 15, 16, 15 | 15.941, 15.844, 15.953 |
| <img src="../assets/c-rust.svg" height="20" align="absmiddle" alt="Rust"> | 6, 6, 6 | 4, 4, 4 | 9.320, 9.250, 9.305 |

- **Sampling:** resources were sampled every twenty milliseconds with bounded storage, and after equal batches followed by a two-hundred-millisecond settling interval.
- **Descriptors:** file descriptors returned to the same per-runtime after-close count in all rounds.
- **RSS:** RSS did increase during error/solve batches; Go retained runtime threads.
- **Node:** showed the largest first-to-last ten-second solve-batch RSS increase: 20.324 MiB, 24.426 MiB, 22.301 MiB. A separate longer Node diagnostic is recorded below.

> [!WARNING]
> Stable descriptor counts do not prove no memory leaks.

## 🔧 Environment

Linux x86_64, AMD Ryzen 9 7950X, Python 3.13.15/curl_cffi 0.17.0, Node 22.22.1/Undici 6.29.1, Go 1.25.5 and Rust 1.96.0.

These are historical measurements from before the Python PycURL/aiohttp
migration. They do not qualify the new transports' pressure behavior; current
transport checks and comparisons are recorded in [transport decisions](transport-decisions.md).

- The initial common environment collector did not find controller files at the cgroup root.
- A post-run ancestor walk found no finite CPU quota and an effective memory ceiling of 62711640064 bytes; this was not continuously sampled.
- Exact ancestor observations, affinity, descriptor limits and versions are retained.

## 🗂️ Artifacts and reproduction

**Artifacts:** [raw samples](../benchmarks/reports/pressure/raw.json.gz), [computed summary](../benchmarks/reports/pressure/summary.json), [original budget and environment](../benchmarks/reports/pressure/plan.json), [expiry follow-up](../benchmarks/reports/pressure/expiry.json.gz), [commands and exit status](../benchmarks/reports/pressure/runner-exit.json), [RPC transcripts](../benchmarks/reports/pressure/rpc-transcripts.json.gz), [verification](../benchmarks/reports/pressure/verification.json). Measured pressure-adapter source snapshots and post-run source-hash differences are retained alongside them.

> [!NOTE]
> Binary hashes were not captured before the original main run; later root adapter rebuilds mean current binary hashes cannot identify those original executable bytes.

**Reproduce** with [the runner instructions](../benchmarks/pressure/README.md).

- The strict report generator rejects incomplete or duplicate rounds, mismatched fixed-operation budgets, malformed times, deadline failures and missing successful runner status.
- Twenty-eight benchmark unit tests, pressure Ruff checks and Node syntax checking passed.
- A focused Python review smoke exercised the strengthened runner and completion marker.
- Package-wide gates are owned by the integrating root agent.

> [!WARNING]
> OS DNS teardown, other platforms, pure memory-only cost, long-term leak freedom and live service acceptance remain unverified.

## 🧪 Node longer-memory diagnostic

**Ordinary run.** It completed 8320 solve operations in 260 equal batches over 60.111229 seconds. During all solve-batch observations there were 23 descriptors, 11 threads and two active PipeWrap resources. After close there were 21 descriptors and 11 threads. The final-quarter RSS change was 2469888 bytes while heapUsed fell by 4771056 bytes, showing automatic collection cycles without an RSS plateau.

**Diagnostic run.** It used Node --expose-gc, 64 warmup solves and 64 equal batches of 128 solves (8256 total solves). Explicit GC was diagnostic-only; the ordinary profile and package behavior were not changed. Both the adapter and fixture exited zero.

| Solves after warmup | Live JS heap after GC (bytes) | Reserved JS heap after GC (bytes) | RSS after GC (bytes) |
| ---: | ---: | ---: | ---: |
| 0 | 10076280 | 19824640 | 81575936 |
| 2048 | 10870000 | 30572544 | 95485952 |
| 4096 | 11096808 | 30310400 | 95977472 |
| 6144 | 11096992 | 47349760 | 107724800 |
| 8192 | 11248304 | 47349760 | 108191744 |

- Between the 4096- and 6144-solve checkpoints, live heap increased by 184 bytes while RSS increased by 11747328 bytes and reserved V8 heap increased.
- Across the final half, live heap after collection increased by 151496 bytes (1.365%).
- After close and collection it was 11044904 bytes, external memory fell and descriptors returned to 21.

**Verdict:** runtime/allocator high-water retention is a confirmed major component of the observed RSS growth; the live managed heap approached a plateau in this finite control. A small residual live-heap increase remains visible.

> [!WARNING]
> This does not prove constant future memory, identify every retained object, or establish leak freedom. No production defect was demonstrated by these observations, and no package implementation was changed for them.

**Raw evidence:** [ordinary longer soak](../benchmarks/reports/pressure/node60.json.gz), [diagnostic GC control](../benchmarks/reports/pressure/node-gc.json.gz), [computed diagnostic summary](../benchmarks/reports/pressure/node-memory-summary.json), [diagnostic RPC transcripts](../benchmarks/reports/pressure/diagnostic-transcripts.json.gz).
