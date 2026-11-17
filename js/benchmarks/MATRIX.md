# 📊 Local Node matrix adapter

> **Node matrix adapter** — runs one externally supplied job against the built checkout.

## 🔧 Run

Build the checkout first, then run one job:

```sh
npm --prefix js run build
node js/benchmarks/matrix.cjs --job /absolute/job.json --output /absolute/result.json
```

The job/result schema is in `../../benchmarks/CONTRACT.md`. This adapter never
starts its own fixture or comparative matrix. No extra benchmark dependencies are
required: versions come from the installed parent package and Node runtime.

## 🗂️ Candidates

- `node-fetch`: Node global `fetch`; the name does not mean the npm `node-fetch`
  package. Its HTTP implementation is Node's bundled Undici.
- `node-undici`: installed `undici.request`, with explicit streaming decompression.
- `node-axios`: installed Axios **fetch adapter**, Node global fetch, and a custom
  dispatcher supplied via `fetchOptions`. This is not Axios's Node HTTP adapter.
- `node-api-async`: real local package `Client.solve`.
- `node-api-sync`: real local package `SyncClient.solveSync`; blocking concurrency
  one only. Higher concurrency returns `unsupported`, rather than serializing
  allegedly concurrent work.

## 🌐 Transport

All raw transport candidates share installed Undici's `Agent` and the internal
Anchorpass `Transport.connect` connector for authenticated HTTP/HTTPS CONNECT and
SOCKS. JavaScript private implementation members are accessed only by this local
benchmark. This isolates frontend overhead, not independent proxy implementations.
SOCKS local/remote DNS and separate target/proxy roots are inherited from that
connector. The connector hides underlying TLS exceptions, so the two TLS negative
controls return `unsupported`: a generic transport error cannot prove a TLS error.
Healthy direct, CONNECT, SOCKS, body-limit, deadline, HTTP-status and reset jobs are
still exercised. There are no ambient proxies, retries, or transport redirects.

## 📊 Measurement

Each worker starts its own sample immediately before creating/acquiring its client.
Cold samples include construction and awaited close; warm samples share one client
and bounded connection pool. The explicit total deadline spans connection, headers
and body. Response validation streams decoded bytes, limits their count, and checks
every byte. A stream read can temporarily allocate one decoded chunk beyond the
configured limit before rejecting; the full response is never accumulated.

## 🔧 API setup

API setup calls `Client.refresh()` against the fixture's signed bundle endpoint
before warmup/timing, into a private per-job temporary cache. Measured clients use
that cache with `offline: true`. Cold clients include cache reads, signature checks,
and worker startup/shutdown in their samples. Warm sync setup uses `loadBgSync()`
to start and prepare its persistent worker. API clients use public constructors;
the fixture HTTP CONNECT proxy maps `www.google.com:443` to the local fixture.
API redirect
and body policies are those of the installed package, rather than raw transport
adapter overrides. API updater setup latency is excluded from timed solve samples.
Its cold refresh plus client close duration is recorded separately in
`setup.refresh_and_close_wall_ns`.

## ✅ Results

Result `wall_ns` and `cpu_ns` cover the measured primary batch, excluding warmup,
recovery, fixture setup, update preparation and final cleanup. Resource snapshots
bracket setup and cleanup; they are not peak measurements. The parent runner owns
external process sampling. Failure samples retain observed categories and bounded
diagnostics. Only an exact expected category counts as a passing negative control.
Recovery reuses the failed-route warm client when possible; invalid-route controls
construct a normal-route client explicitly.

## 🧪 Checks

Focused regression smoke (not a comparative performance run):

```sh
node --test js/benchmarks/matrix.test.cjs
```

That self-contained smoke uses existing test certificates. The parent matrix's
shared fixture additionally exercises distinct origin/proxy authorities and SOCKS
address-type accounting. A connection handshake has its own finite timeout;
request deadlines cancel frontend dispatch/body consumption, and final close
destroys pending handshakes. Pooled reconnect callbacks deliberately do not retain
an earlier operation's abort signal.
