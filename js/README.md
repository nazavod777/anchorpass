# Anchorpass for Node.js

> Provisional standalone Node.js client for the signed `recaptcha-v3/1` HTTP protocol.

| Fact | Value |
|---|---|
| Required runtime | Node.js 22 |
| Currently verified environment | Linux x64 with Node 22.22.1 |
| Remote update channel | None configured by default |
| Bundled key | `production-1` verifies the [GitHub channel](../docs/channel-operations.md); set `bundleUrl` to opt in. |

> [!WARNING]
> Local contract tests do not establish a site's acceptance or score. Production release readiness depends on a separate controlled integration check.

## 🚀 Quick start

```js
import { Client, SyncClient, extractAction, loadBg, loadBgSync } from 'anchorpass';
// CommonJS: const { Client, SyncClient } = require('anchorpass');
const client = new Client({ proxy: 'http://user:pass@localhost:8080', timeout: 30 });
try {
  const result = await client.solve(anchorUrl, { action: 'login' });
  console.log(result.bundle_revision); // Do not log result.token in production.
} finally {
  await client.closeAsync();
}
const sync = new SyncClient({ timeout: 30 });
try {
  const result = sync.solveSync(anchorUrl, { action: 'login', bg: customBg });
} finally {
  await sync.closeAsync();
}
```

## 🧩 API

- **One-shot helpers:** `solve(anchorUrl, options)` and `solveSync(anchorUrl, options)` create and close their own client. Reuse `Client` or `SyncClient` to reuse connections.
- **Result:** contains `token`, `bundle_revision`, `protocol_family`, and `duration_ms`. A token does not promise acceptance or a score.
- **Anchor URLs:** must be Google HTTPS `api2/anchor` or `enterprise/anchor` endpoints, with nonempty `k` and `co`; credentials, fragments, duplicate query members, malformed encoding and normalized path tricks are rejected.
- **HTML handling:** HTML attribute entities are decoded using `html-entities` with HTML5 attribute semantics; response markup is never executed.
- **Enterprise:** local protocol coverage only; no live acceptance claim is made.

### Bundle loading

`await client.loadBg()` and `sync.loadBgSync()` load the signed bundle according to normal update, signature, expiry and pin rules and return its `bg`. The one-shot `loadBg(options)` and `loadBgSync(options)` behave the same way. They do not request a captcha.

### Per-call overrides

- **`action`:** defaults to the URL's `sa` and explicitly supplied `action` wins. It is sent as `sa` to both anchor and reload. Explicit action must be nonempty and at most 256 UTF-8 bytes. A selected empty URL `sa` is rejected by solve; an explicit valid action can replace it.
- **`extractAction(anchorUrl)`:** validates the same URL and returns its parsed `sa` (including an empty string) or `null`, with no network or bundle loading.
- **`bg`:** a per-call `bg` overrides only that solve's form field. It must be a nonempty string of at most 16384 UTF-8 bytes; validation happens before network access. Overrides never mutate the bundle or cache, and never bypass trust or expiry.
- **`userAgent`:** an optional per-call User-Agent header for version, anchor and reload requests, including permitted redirects. It must contain 1–1024 printable ASCII characters and cannot be whitespace only. Omit it to retain the transport's native header behavior. Update downloads never receive this override.
- **`hostname`:** an optional per-call ASCII domain, punycode name or IP address that replaces the hostname encoded in the anchor URL's `co` origin. The original HTTP/HTTPS scheme and port are preserved; an omitted port becomes 80/443. The resulting Google Base64 value is used by both anchor and reload. A hostname override requires a valid encoded origin; omitting it preserves the original `co` unchanged. Supply a host only, without a URL scheme, path or port. Both overrides work with `Client.solve`, `SyncClient.solveSync`, `solve` and `solveSync`, and do not affect other calls.

### Errors

Errors are `AnchorpassError` instances with stable `code`, `stage`, and `retryable` fields; messages omit network credentials.

## 🔀 Routing and proxies

- **`proxy`:** omitted or `null` means direct. Supported absolute URLs are `http://`, `https://`, `socks5://` (local target DNS), and `socks5h://` (remote target DNS), all with explicit host and port. HTTP(S) uses CONNECT, including for HTTP fixtures. ASCII username/password authentication is supported with exact-once percent decoding. Errors never trigger a direct retry. Environment proxy variables are ignored.
- **`updatesProxy`:** omitted inherits `proxy`; `null` explicitly uses direct routing; a URL chooses an independent route. The updater has a separate connection pool and never receives captcha cookies, headers or parameters. No cookie jar is used.
- **Your own submission:** the app that submits a token to its own site must preserve its intended network route itself. A fixed proxy URL does not guarantee a fixed egress IP from a rotating provider.

## 🔐 Signed data and updates

- **`bundleUrl`:** explicit HTTPS signed-envelope endpoint. HTTP is allowed only for loopback fixtures. Update redirects are rejected.
- **`trustedKeys`:** maps key IDs to base64 raw 32-byte Ed25519 public keys and augments the bundled trust roots.
- **`offline`:** disables update network access, not captcha requests.
- **`pin`:** exact decimal revision. Neither `offline` nor `pin` overrides expiry, signature or rollback checks.
- **`await client.prepare()`:** validates/selects a bundle.
- **`await client.refresh()`:** forces a bounded update check and reports candidate errors. Automatic refresh can retain a current signed compatible bundle only while its expiry covers the operation deadline.
- **Shared downloads:** concurrent clients with the same channel and network policy share a bounded download; closing one waiter preserves the other. Downloads admit at most 16 channels and 16 client waiters per channel per isolate. Disk publication is coordinated by its lock.

### Cache

`cachePath` is the signed-envelope cache filename. Omitted or `null` explicitly uses memory only and provides no rollback persistence across process restarts.

- Disk writes use a bounded exclusive lock, fsync and atomic rename.
- A crashed writer can leave a lock file; lock waiting is bounded by the update deadline.
- Deleting cache state resets the locally accumulated high water mark.
- Corrupted caches fail closed; write failures are errors, never an implicit switch to memory.

## 🍪 Cookies and limits

- **Cookie jar:** each solve has an isolated cookie jar with domain, path, Secure and expiry checks.
- **Budgets:** at most 64 Set-Cookie headers and 16384 cookie-header bytes per solve; outgoing Cookie headers are also limited to 16384 bytes.
- **Redirects and pools:** allowed GET redirects retain that solve's cookies, and connection pools remain reusable.

## ⚙️ Configuration

| Option | Default | Meaning |
|---|---|---|
| `timeout` | 30 seconds | Total solve deadline |
| `connectTimeout` | 5 seconds | Connect timeout |
| `updateTimeout` | 5 seconds | Update timeout |
| `maxInFlight` | 16 | Concurrent operations; overload is rejected immediately, there is no unbounded admission queue |
| `maxResponseBytes` | 1048576 | Response size bound; bodies are bounded before accumulation, including gzip, deflate and Brotli decoded bytes |
| `refreshInterval` | 3600 seconds | Automatic refresh interval |
| `ca`, `proxyCa` | — | Separate PEM trust roots for the target and HTTPS proxy. TLS verification remains enabled. |

- GET/POST requests are not retried automatically.
- Reads have a fixed 5-second idle/header bound as well as the total solve deadline.
- See also `proxy`, `updatesProxy`, `bundleUrl`, `trustedKeys`, `cachePath`, `offline` and `pin` in the sections above.

## 🧵 Concurrency and cancellation

- **Cancellation:** `Client.solve` accepts `signal: AbortSignal`.
- **Closing:** `close()` stops admission and releases owned resources; `closeAsync()` confirms completion.
- **`SyncClient`:** close may block up to one second to observe worker exit, then requests termination. A timeout permanently closes that sync client.
- **Results:** fresh bounded shared buffers carry results, and late completion cannot overwrite cancellation.
- **Workers:** at most four starting, running or terminating workers exist per JavaScript isolate. The sync parent waits on Atomics and monotonic time, without relying on event callbacks. One-shot sync helpers release workers even when the caller never yields the event loop.
- **After failures:** after failed workers, use `closeAsync()` to confirm cleanup before replacing clients.

> [!WARNING]
> Node's system DNS resolver cannot be forcibly interrupted; sockets and waiting calls are cancelled and connection handshakes have finite limits.

## 🔧 Development

Development checks: `npm ci`, `npm run lint`, `npm run typecheck`, `npm test`, `npm run build`, `npm pack`. `npm run bench` writes direct local HTTP pilot raw data in `benchmarks/raw.json`; see the benchmark report for scope and limitations.
