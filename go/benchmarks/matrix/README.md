# 📊 Go local transport matrix adapter

> **Go matrix adapter** — implements `benchmarks/CONTRACT.md`; the older `go run .` pilot is unchanged.

It never starts a fixture or another benchmark process.

## 🔧 Build and run

Build and launch from the repository root:

```sh
(cd go/benchmarks && GOPROXY=off go build -o /tmp/anchorpass-go-matrix/matrix-bench ./matrix)
/tmp/anchorpass-go-matrix/matrix-bench --job /absolute/job.json --output /absolute/result.json
```

## 🗂️ Candidates

Candidates are `go-net-http`, `go-resty`, `go-fasthttp`, and `go-api`. Resty v2.16.5 wraps the
same configured `net/http.Transport`, separate origin/proxy TLS-root adapters,
SOCKS adapter and bounded decompressed-body reader. This is a comparison of
request wrappers over a shared backend. HTTP/2 is disabled for both transports.
The raw adapters suppress both library retries and net/http stale-connection
replay. SOCKS5 resolves locally, preferring an IPv4 result for the IPv4 fixture;
SOCKS5h passes the hostname to the proxy. Neither changes target TLS SNI.

## 📊 Measurement

Every batch uses a fixed pool of goroutines and an unbuffered submission channel.
Each worker starts a sample when it begins the operation. Cold samples include
CA loading, client construction, request/body handling and client cleanup. Warm
samples reuse the client. All requests have a context deadline covering body
consumption; custom TLS/SOCKS dialers additionally bound handshake duration.
Response headers have a fixed 65536-byte limit. Raw response limits are supplied
by each job and applied after net/http's automatic gzip decompression.

## 🌐 API candidate

The public API candidate imports the production module through the nested
module's local replacement, without modifying production code. It uses HTTP
CONNECT to the fixture's Google mapping. Before warmup a preparation client calls
`Refresh` with the fixture bundle URL and trusted Ed25519 keys, writes a fresh
signed persistent cache, then closes. Every measured client uses that cache with
updates disabled by `WithOffline(true)`. Cold clients include construction,
signed cache verification and cleanup in latency; no seed client or cross-client
memory sharing is used. This avoids timed update downloads and an embedded-bundle
expiry dependency. The adapter removes its cache and lock file before writing
results. API jobs preserve the
package's documented bounded GET retries and fixed 1048576-byte decompressed
response limit. API `bytes` means returned token bytes; raw `bytes` means bytes
read after decompression. Unsupported candidate/profile combinations return an
explicit unsupported result. Initialization/schema/fixture errors exit nonzero.

## 📋 Job bounds

Supported job bounds: concurrency 1–1024, measured operations 1–1000000, warmup
and recovery operations 0–1000000, timeout 1–60000 milliseconds, and raw body
limit 1–16777216 bytes. The shared profile manifest normally sets much smaller
budgets. CPU and wall time cover the measured batch; resource snapshots are
collected around warmup/measurement/recovery and after cleanup. The parent
runner measures process peaks separately. Invalid immutable proxy routes need a
fresh normal HTTP-proxy client for recovery; healthy-route failures reuse their
existing transport.

## 🧪 Checks

Focused checks from `go/benchmarks`:

```sh
gofmt -l matrix/*.go
go vet ./...
go test -race ./matrix
go build ./...
```

Tests check actual decompressed-body limits, total body deadlines, recovery,
redirect rejection and the absence of hidden GET replay for both wrappers.
The shared fixture smoke additionally qualifies HTTPS proxy trust separation,
SOCKS address type, proxy authentication, API preparation and the exact CLI.
Timing matrices are run sequentially by the root orchestrator, never by this
adapter's test suite.

### Additional fast transport and full flow

`go-fasthttp` pins stable v1.65.0, the last release supporting the package's Go
1.23 minimum. Registry metadata inspected on 2026-11-17 showed v1.66.0 requires
Go 1.24, v1.72.0 Go 1.25, and latest v1.79.0 Go 1.26; the installed Go 1.25.5
cannot build that latest version. This candidate uses its own pool and parser,
with bounded custom CONNECT/SOCKS dial adapters, independent target/proxy TLS
roots, one attempt, a total request deadline, and bounded gzip decoding.
Its deadline API is distinct from Go context cancellation and is not evidence
that an eventual production adapter would preserve immediate caller cancellation.
Raw adapter tests cover decompressed limits, slow-drip total deadlines, redirects,
recovery and absence of hidden stale-connection retries for all three candidates.

`kind: flow` performs the fixture's real three HTTP steps with each raw client:
version GET, anchor GET and form POST reload. It parses the fetched version and
challenge, copies fixture-supplied signed bundle fields, isolates a new cookie jar
per operation, validates the returned token and shares one operation deadline.
`flow_delay_ms` adds the same fixture delay to each request. Cold mode constructs
and closes the selected client around the entire flow. This is a synthetic raw
transport flow, separate from the public API's signed-data parsing/cache work.
