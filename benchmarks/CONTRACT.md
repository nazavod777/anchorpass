# 📐 Local transport matrix contract

> **Adapter interface** — a new benchmark interface, independent of package APIs; all traffic stays on loopback.

> [!WARNING]
> Never benchmark a public CAPTCHA endpoint with this runner.

## 🧭 Overview

Adapters take `--job PATH --output PATH`. Read one JSON job and write one JSON
result, then exit. The runner starts a fresh process per job, rotates candidates
between rounds, records process resources externally, and runs timed jobs
sequentially. Adapters must not run a second timed benchmark themselves.

## 📋 Job, version 1

```json
{
  "schema_version": 1,
  "run_id": "unique-job-name",
  "candidate": "candidate-name",
  "round": 0,
  "fixture": {
    "https_url": "https://localhost:1234",
    "bad_target_url": "https://localhost:1235",
    "ca_file": "/absolute/origin-ca.pem",
    "proxy_ca_file": "/absolute/proxy-ca.pem",
    "proxies": {
      "http": "http://bench:password@127.0.0.1:2345",
      "https": "https://bench:password@localhost:2346",
      "socks5": "socks5://bench:password@127.0.0.1:2347",
      "socks5h": "socks5h://bench:password@127.0.0.1:2347",
      "bad_auth": "http://bench:wrong@127.0.0.1:2345",
      "bad_tls": "https://bench:password@localhost:2348"
    },
    "anchor_url": "https://www.google.com/recaptcha/api2/anchor?k=test-key&co=dGVzdA&sa=matrix-action",
    "bundle_url": "https://localhost:1234/bundle",
    "trusted_keys": {"fixture": "base64-public-key"}
  },
  "profile": {
    "name": "direct-warm-c8",
    "kind": "transport",
    "route": "direct",
    "connection_mode": "warm",
    "concurrency": 8,
    "warmup": 32,
    "requests": 128,
    "timeout_ms": 2000,
    "max_response_bytes": 65536,
    "path": "/bytes?size=1024",
    "expected_bytes": 1024,
    "expected_error": null,
    "recovery_requests": 0
  }
}
```

### 🌐 Routes

Routes: `direct`, `http`, `https`, `socks5`, `socks5h`, `bad_auth`, `bad_tls`,
`bad_target`.

- The latter uses a target certificate signed by the proxy CA; `bad_tls` uses a proxy certificate signed by the origin CA. They detect accidental union of origin and proxy trust.
- Route `bad_target` uses `bad_target_url` through the normal HTTPS proxy, so both independent trust policies are exercised.
- The SOCKS server records address type: `socks5` must send an IP and `socks5h` a hostname.
- The target hostname is always `localhost` in transport jobs.

### 🔧 Job kinds

`kind: api` uses the real installed/local package client, `anchor_url`, and the
HTTP CONNECT route. Prepare a fresh signed bundle with `bundle_url` and
`trusted_keys` before timing; avoid a dependency on the embedded bundle expiry.
`load_bg` API profiles may be added separately. API success requires exactly
`fixture-token`; raw transport success requires a body of `expected_bytes`
ASCII `x` bytes. Warmup uses the same operation and is excluded from samples.

`kind: flow` uses each raw candidate's HTTP backend for version GET, anchor GET
and reload POST, with one total deadline and a fresh cookie jar per operation.
Version and challenge values are parsed from their responses; reload includes
the signed bundle fields supplied as `fixture.flow_fields`. Success requires
`fixture-token` and exactly one request to each endpoint. An optional
`flow_delay_ms` applies to all three steps. These samples measure a complete
request sequence without substituting a different client's public API.

### 📋 Adapter rules

No ambient proxies, redirects, implicit retries, certificate-verification
disabling, or unbounded body reads. Use separate origin/proxy roots. If a
candidate cannot supply a contract requirement through a documented adapter,
return `status: unsupported` with a specific explanation rather than silently
changing semantics. API jobs retain the package's documented bounded retries and
body limit; identify those differences in notes. 

> [!NOTE]
> Capability limitations observed in the exact adapter/version
> do not prove that every possible integration of that library is impossible.

### ⏱️ Timing and errors

Use a fixed worker pool of `concurrency` workers and at most that many outstanding
requests. Warm mode reuses clients/connections. Cold mode constructs and closes
a client per operation, including that cost in latency. Each timed sample starts
when its worker begins the operation, excluding a hidden unbounded submission
queue. Synchronous-only candidates use bounded worker threads; report that mode.
Total deadline includes connect, headers and body. Response bytes are counted
after decompression. Close every response/client/worker on both outcomes.

### 🚨 Expected errors

Expected errors are `deadline`, `proxy`, `tls`, `response_limit`, `http_status`,
or `transport`. A successful negative control has `ok: true` and its observed
`error` category. A generic exception cannot automatically satisfy an expected
category. Record exception type/message (bounded, with no proxy credentials) in
`detail` when useful. A `deadline` that finishes beyond `timeout_ms + 100` fails.
After a failure batch, run `recovery_requests` successful `/bytes?size=1024`
requests using the same normal-route client where possible, and then close it.
Record recovery results separately. No failure samples are performance winners.

## ✅ Result, version 1

```json
{
  "schema_version": 1,
  "run_id": "unique-job-name",
  "candidate": "candidate-name",
  "status": "ok",
  "runtime": "runtime version",
  "versions": {"library": "exact version"},
  "mode": "native-async-or-bounded-threads-or-blocking",
  "notes": [],
  "samples": [{"elapsed_ns": 123, "ok": true, "error": null, "bytes": 1024}],
  "recovery": [],
  "warmup_completed": 32,
  "wall_ns": 123,
  "cpu_ns": 123,
  "resources": {"fds_before": 5, "fds_after": 5, "threads_before": 1, "threads_after": 1}
}
```

An unsupported result instead has `status: unsupported`, `reason`, empty
`samples` and `recovery`. A harness failure exits nonzero; do not turn it into
an unsupported feature. Result output must be written after cleanup. Resource
counts may be null on platforms without `/proc`. The parent also records sampled
peak RSS/threads/FDs, startup-inclusive RSS, fixture counters and original exit
status. Identify sampling interval and scope in reports.

## 📊 Profiles

Performance profiles have three measured rounds, each with 32 warmup operations
and 128 samples. The fixed sample budget gives four measured waves at concurrency
32, chosen before results. Tails are descriptive, not a claim of precise extreme
quantiles. Fault profiles use one round with repeated failures plus recovery.
The generated manifest is the source of truth for actual job/sample counts.
