# 📐 Public API contract

[← Documentation index](../docs/README.md)

> **Naming rule** — `load` names a potentially networked operation, `extract` names a pure operation.

There are no `get_bg` or `get_action` aliases in this new package.

| Language | Current verified bg | Action from an anchor URL | Solve overrides |
|---|---|---|---|
| <img src="../assets/c-python.svg" height="20" align="absmiddle" alt="Python"> | `load_bg()`, `await aload_bg()`; client `.load_bg()` | `extract_action(anchor_url)` | `solve(..., bg=None, action=None, user_agent=None, hostname=None)`; same for `asolve` and clients |
| <img src="../assets/c-nodejs.svg" height="20" align="absmiddle" alt="Node"> | `await loadBg()`, `loadBgSync()`; corresponding client methods | `extractAction(anchorUrl)` | options `{ bg, action, userAgent, hostname }` for async and sync |
| <img src="../assets/c-go.svg" height="20" align="absmiddle" alt="Go"> | `LoadBG(ctx, options...)`, `client.LoadBG(ctx)` | `ExtractAction(anchorURL)` | `WithBG(bg)`, `WithAction(action)`, `WithUserAgent(ua)`, `WithHostname(hostname)` |
| <img src="../assets/c-rust.svg" height="20" align="absmiddle" alt="Rust"> | async and blocking `load_bg`; client methods | `extract_action(anchor_url)` | `solve_with_options(..., SolveOptions { bg, action, user_agent, hostname })`; use `..Default::default()` for omitted fields |

See each package README for exact language signatures. The Python API is
documented in the root README and `docs/migration.md`.

Go client TLS options distinguish `WithRootCAs` for target trust and
`WithProxyRootCAs` for HTTPS-proxy trust. Both clone the supplied pool and default
independently to system roots. They also apply to the corresponding update route.
These are client settings, not solve-only overrides.

Rust async and blocking builders use `add_root_certificate(pem)` for target
trust and `add_proxy_root_certificate(pem)` for HTTPS-proxy trust. Each adds to
its own default public roots and applies to the corresponding update route.
PEM bundles are bounded and all contained certificates are loaded. An HTTPS
proxy uses a pooled Hyper adapter with separate rustls configurations; direct,
HTTP CONNECT and SOCKS routes retain reqwest.

`load_bg` follows the client's configured update, pin, offline, trust, cache and
proxy policy. It can return an unexpired bundled or cached value; it does not
promise a network round trip or prove that a remote channel has no newer data.
Use `refresh` for an explicit update check. No remote channel is configured by
default, and import never performs a request.

`extract_action` validates the supported anchor URL and percent-decodes its `sa`
query parameter. It makes no requests, executes no JavaScript and does not guess
the site's action. An absent action is `None`/`null`/`Option::None`, or an
empty string with no error in Go. An explicitly empty `sa` is rejected by solve.

Each solve resolves values once:

1. `bg`: explicit argument, otherwise the verified bundle's `bg`.
2. `action`: explicit argument, otherwise anchor URL `sa`, otherwise omit it.
3. User-Agent: explicit `user_agent`/`userAgent`, otherwise the transport's existing default.
4. `hostname`: explicit domain replacement in the origin encoded by `co`, otherwise preserve the original `co` exactly.

Explicit values apply only to this call. They do not change later calls, bundle
fields, revision, cache contents or refresh policy. `action` is forwarded as `sa`
to both anchor and reload. It is never obtained from the global bg bundle.
An explicit action replaces `sa` from the URL rather than duplicating it.

Explicit values must be nonempty UTF-8 strings: bg is limited to 16384 bytes and
action to 256 bytes. Invalid values fail before update or CAPTCHA requests.
Explicit bg still requires a valid snapshot for the other protocol fields;
expiry, signature, pin and rollback checks remain enabled.

User-Agent is a nonempty printable ASCII string of at most 1024 bytes, containing
at least one non-space character. Controls, tabs, CR/LF, DEL and non-ASCII input
are rejected before update or CAPTCHA networking. The same value is sent on the
version, anchor and reload requests. It never changes another solve or update
request, and does not enable browser TLS impersonation.

`hostname` names the application site, not the Google request endpoint. It
replaces only the host in `co`, retaining its HTTP/HTTPS scheme and effective
port. For example, replacing the hostname in `http://old.example:8080` with
`new.example` produces `http://new.example:8080`. Accepted hostnames are ASCII DNS
names (international domains use their `xn--` form), IPv4 and IPv6 literals;
IPv6 brackets are optional on input. URLs, ports, paths, credentials, whitespace
and malformed names are rejected. DNS names are lowercased; IPv6 is normalized
and bracketed in the serialized origin.

An explicit hostname requires `co` to decode to an HTTP/HTTPS origin with a host
and valid port, no credentials, query or fragment, and no path except `/`.
Standard and URL-safe Base64, Google's dot padding and omitted padding are
accepted; malformed padding and noncanonical trailing bits are rejected. The effective origin is serialized as `scheme://host:port`, encoded
with standard Base64 and `=` replaced by `.`, and forwarded identically to
anchor and reload. Without an override, no additional decoding or normalization
is performed. Invalid input fails before updates or CAPTCHA requests. This
parameter does not change the site key's permitted domains or force the hostname
reported by Google's verifier.

For a valid anchor URL whose `co` identifies the same application, these calls
set the application hostname and the User-Agent for one solve:

```python
result = client.solve(anchor_url, hostname="example.com", user_agent="MyApp/1.0")
```

```js
const result = await client.solve(anchorUrl, { hostname: 'example.com', userAgent: 'MyApp/1.0' });
```

```go
result, err := client.Solve(ctx, anchorURL,
    anchorpass.WithHostname("example.com"), anchorpass.WithUserAgent("MyApp/1.0"))
```

```rust
let result = client.solve_with_options(&anchor, SolveOptions {
    hostname: Some("example.com"),
    user_agent: Some("MyApp/1.0"),
    ..Default::default()
}).await?;
```

Use the hostname of the application that will verify the token and the User-Agent
of the client using it. Omit an override when the anchor already has the correct
origin or the default header is appropriate. Arbitrary values do not guarantee
a better score or acceptance.

One operation uses one bundle snapshot and one fetched protocol version. Result
metadata reports that snapshot even when bg was overridden. Returning a token
does not prove that a site's server accepted it.
