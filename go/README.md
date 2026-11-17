# Anchorpass for Go

> Standalone module `github.com/nazavod777/anchorpass/go`: public release `v0.1.1`, available through the Go module proxy; `net/http` is the selected transport.

- **Dependencies:** the implementation uses `net/http`, `golang.org/x/net/html` for HTML5 tokenization and the public suffix list for cookie scope.
- **Embedded data:** it embeds its signed protocol bundle and public keys.
- **Independence:** it does not require another Anchorpass language package.

## 🚀 Quick start

Install from your application module:

```sh
go get github.com/nazavod777/anchorpass/go@v0.1.1
```

```go
package main

import (
    "context"
    "log"
    "time"

    anchorpass "github.com/nazavod777/anchorpass/go"
)

func main() {
    client, err := anchorpass.NewClient(
        anchorpass.WithTimeout(30*time.Second),
        anchorpass.WithMemoryCache(),
        anchorpass.WithProxy("http://user:pass@127.0.0.1:8080"),
    )
    if err != nil { log.Fatal(err) }
    defer client.Close()

    result, err := client.Solve(context.Background(),
        "https://www.google.com/recaptcha/api2/anchor?k=site-key&co=encoded-origin",
        anchorpass.WithAction("homepage"),
    )
    if err != nil { log.Fatal(err) }
    // Pass result.Token to your authorized application's verification flow.
    // Token receipt alone does not establish acceptance or a score.
    _ = result.Token
}
```

## 🧩 API

| Call | Behavior |
|---|---|
| `Solve(ctx, anchorURL, options...)` | Top-level: creates and closes a temporary client. Accepts client settings and per-call overrides together. |
| `Client.Solve` | Reuse `Client` to reuse connections. The same `Solve` method can run directly or in a goroutine. |
| `LoadBG(ctx, options...)` | Creates and closes a client for a single load. |
| `Client.LoadBG(ctx)` | Returns `bg` from the verified selected bundle under the same signature, expiry, pin, update and cache rules as Solve. It sends no captcha requests. |
| `ExtractAction(anchorURL)` | Only validates and parses the URL and returns its `sa` query value, or an empty string if missing; no action can be inferred for an arbitrary site beyond what the URL supplies. |
| `Refresh(ctx)` | Explicitly checks the channel and returns candidate errors rather than hiding them behind fallback; an already valid exact pin is retained without fetching a new revision. |

`NewClient` and `LoadBG` reject solve-only options.

### Per-call overrides

- **`WithUserAgent`:** sets the same per-call `User-Agent` on version, anchor and reload requests. It accepts 1–1024 printable ASCII bytes, excluding whitespace-only values. Updates retain their default header. Omitting it retains the existing default.
- **`WithHostname`:** replaces only the hostname inside the anchor URL's encoded `co` origin, preserving its HTTP/HTTPS scheme and effective port. Accepts ASCII DNS/punycode names and IP addresses, including IPv6. It applies the same encoded origin to anchor and reload; omitting it preserves `co` verbatim. A selected override requires `co` to decode to a valid HTTP/HTTPS origin. Values remain local to that solve.
- **`WithAction`:** requires a nonempty UTF-8 string of at most 256 bytes, wins over URL `sa` and is sent to both anchor and reload. An empty URL `sa` is rejected by Solve unless replaced by an explicit valid action.
- **`WithBG`:** replaces only the outgoing `bg`; it must be nonempty valid UTF-8 and at most 16384 bytes.
- **Isolation:** the fields are copied per operation, so overrides do not affect another call. `BundleRevision` still identifies the source of the remaining signed fields.

### Errors

Errors use `*anchorpass.Error` with stable `Code`, `Stage`, and `Retryable` fields. Error strings and default Client/SolveResult formatting omit proxy credentials and tokens. Context cancellation/deadline errors support `errors.Is`.

> [!WARNING]
> Applications should not indiscriminately log or serialize returned tokens.

## 🔀 Routing and proxies

- **Anchor inputs:** require HTTPS, exactly `google.com` or `www.google.com`, and `/recaptcha/api2/anchor` or `/recaptcha/enterprise/anchor`. Credentials, fragments, ports other than 443, duplicate query keys, and missing or empty `k`/`co` are rejected. Unknown query parameters are retained. HTTP redirects are rejected for both captcha and update requests. The test origin hook is private to this package and cannot be set by consumers.
- **Proxy URLs:** must have an explicit host and port. Supported schemes are `http`, `https`, `socks5`, and `socks5h`; HTTP Basic and SOCKS username/password credentials support ASCII and percent-encoded special characters.
- **`socks5`:** resolves target names locally while preserving HTTP Host and TLS SNI.
- **`socks5h`:** sends target names to the proxy without local target lookup.
- **Environment:** proxy environment variables are ignored. A failed proxy never becomes a direct request.
- **TLS:** HTTPS proxy and target certificates are verified independently. `WithRootCAs` sets a cloned custom trust pool for target TLS, including update targets; `WithProxyRootCAs` sets a separate cloned pool for HTTPS proxies, including the update proxy. Each defaults independently to system roots. A target certificate signed only by the proxy trust pool, or a proxy certificate signed only by the target pool, is rejected. No public TLS-verification bypass is provided.
- **Updates route:** defaults to inheritance from `WithProxy`. Override explicitly using `WithUpdatesProxy(UpdatesDirect)` or `WithUpdatesProxy(UpdatesVia(proxyURL))`; `UpdatesInherit` restores inheritance. Update requests use a separate bounded transport and do not include captcha cookies or query parameters.
- **Your own verification request:** a proxy configured on this client does not route your application's separate server-verification request. Configure that application's HTTP client with the same route when required. A provider's rotating proxy URL does not promise a fixed external IP.

## 🔐 Signed data and updates

- **Default:** no remote update channel is enabled by default. Set `WithBundleURL` to an HTTPS URL; loopback HTTP is accepted for update fixtures.
- **Trust:** the bundled `production-1` public key verifies the [GitHub channel](../docs/channel-operations.md). To use another signer, explicitly provide `WithTrustedKeys(map[string]ed25519.PublicKey)`; it replaces rather than extends the bundled trust set.
- **Verification:** updates are verified against exact signed payload bytes before use, including a closed field/schema/family set, duplicate JSON rejection, positive revisions of at most 20 decimal digits, 16384-byte field limits, safe-integer timestamp bounds, expiry and a five-minute issued-at clock-skew allowance. There is no expiry grace period.
- **Rollback floor:** an authenticated higher revision is retained as the rollback floor in memory and, when configured, on disk before checking expiry or a requested pin.
- **Snapshots:** an operation selects one immutable bundle snapshot whose validity covers its entire allowed deadline.
- **Modes:** `WithOffline(true)` prevents update requests while captcha operations still require network. `WithPinnedRevision("1")` requires that exact revision and retains signature, expiry and rollback checks.

### Cache

- **Memory:** the default, and can be stated explicitly with `WithMemoryCache()`. With custom target or HTTPS proxy roots, its state belongs to that individual client. It has no rollback guarantee after the owning client closes, after all matching system-root clients close, or after process restart.
- **Disk:** `WithCachePath(path)` instead enables an atomic signed-envelope disk cache and a cross-process monotonic revision floor. The cache is read on use, not at import or construction.
- **Locks:** Unix kernel file locks bound waiting and survive process crashes without an abandoned lock; the lock file remains intentionally. Unix lock adapters are provided for Linux, macOS and BSD; only Linux was exercised here. Other platforms support memory mode and reject disk locking.
- **Paths:** cache and lock paths must be regular files, not symlinks, directories or pipes.
- **Failures:** disk corruption, read/write failures and lock errors do not silently downgrade to memory mode. Deleting the cache removes its historical rollback floor.

### Refresh

- Automatic refreshes are serialized within each client. Clients using default system roots may also share refresh state when channel, route, signing keys and cache path match.
- Any custom target or HTTPS proxy trust pool disables cross-client memory sharing, because certificate-pool equality cannot establish equivalent custom certificate constraints.
- An explicitly selected disk cache remains reusable signed application data, verified on read; it does not prove that the current client downloaded it under its TLS policy.
- The process registry is bounded to 128 active states, including one state per custom-pool client, and releases states as clients close.
- A cancelled waiter does not cancel another client's ongoing refresh.
- A failed automatic update can retain a still-valid snapshot; it cannot extend its signed expiry.

## 🍪 Cookies and limits

- **Cookie jar:** each solve owns a standard cookie jar; pooled connections are reused without sharing cookies between solves or with updates. Cookie domain, path, Secure and expiry rules apply.
- **Budgets:** a solve processes at most 64 Set-Cookie fields with at most 16384 aggregate raw bytes; an outgoing Cookie header is also limited to 16384 bytes. Exceeding a cookie budget returns `ResourceLimit`.
- **Response sizes:** a single response is limited to 1048576 decompressed bytes and an update envelope to 65536 bytes.

## ⚙️ Configuration

| Option | Default | Meaning |
|---|---|---|
| `WithTimeout` | 30 seconds | Total client timeout shared by all network stages, including update loading and response bodies |
| `WithMaxInFlight` | 16 | Maximum concurrent operations, without a waiting queue; excess work gets `ResourceLimit` |
| `WithRefreshInterval` | one hour | Normal automatic refresh interval |

- Network connect, TLS, response-header and idle socket stages have an additional five-second bound; update loading has a five-second bound.
- GET attempts are explicitly limited to two and only replay EOF/reset failures on an already reused connection. Native hidden replay is disabled with an EOF-only request body whose wire representation stays an ordinary bodyless GET; transcript tests cover direct HTTP, forward proxy, CONNECT and SOCKS/TLS routes. A reload POST is not retried.
- All network stages share the caller's context.

## 🧵 Concurrency and cancellation

- **Concurrency:** a client is safe for concurrent calls; the default admits at most 16 operations without a waiting queue.
- **`Close`:** idempotent, rejects new work, cancels active calls and closes idle connections. It initiates cancellation without waiting for callers to return.
- **Deadlines:** operation deadlines return promptly, but Go's `net/http` may continue an already started connection attempt after the operation ends. A stalled HTTPS proxy TLS handshake remains bounded by the five-second connect timeout and `MaxConnsPerHost`. `Client.Close()` cancels owned pending connection attempts.

> [!WARNING]
> An operation deadline alone does not guarantee immediate socket teardown.

## 🔧 Development / verification

Run from this module:

```sh
gofmt -l *.go
go vet ./...
go test -race ./...
go build ./...
```

Verified runtimes: Go 1.23.0 and 1.25.5, Linux/amd64. Tests cover local direct/proxied protocol flows, both families, encoding, bounds, cancellation, concurrency, signed updates, cache restart/rollback, and cross-process lock contention. They perform no live reCAPTCHA request or publication. See `benchmarks/README.md` for the local `net/http` versus Resty pilot and `benchmarks/matrix/README.md` for the shared matrix adapter and its limitations.
