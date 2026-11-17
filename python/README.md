# Anchorpass for Python

> Development implementation of the reCAPTCHA v3 HTTP flow, with native synchronous and asynchronous clients, explicit proxies and signed protocol data. No public registry release or default remote update channel is configured by this checkout.

## 🚀 Quick start

```python
from anchorpass import Client, extract_action

anchor_url = "https://www.google.com/recaptcha/api2/anchor?k=site-key&co=encoded-origin"
with Client(proxy="http://user:pass@127.0.0.1:8080") as client:
    current_bg = client.load_bg()
    result = client.solve(anchor_url, action="homepage")
    token = result.token

action = extract_action(anchor_url)  # None if the URL has no sa parameter.
```

Use your application's actual anchor URL and action. The example URL is a shape, not a working site configuration.

> [!NOTE]
> A returned token requires server verification by your application; it does not establish acceptance or a score.

```python
from anchorpass import AsyncClient, aload_bg, asolve


async def obtain_token(anchor_url: str) -> str:
    async with AsyncClient(proxy=None) as client:
        result = await client.solve(anchor_url)
        return result.token
```

## 🧩 API

| Item | Behavior |
|---|---|
| One-shot helpers | `solve`, `asolve`, `load_bg` and `aload_bg` |
| Clients | Reuse clients for connection pooling. Close them using `with`/`async with`. |
| `load_bg` | Follows cache and update policy and may perform an update request. |
| `extract_action` | Only validates/parses the URL and performs no I/O. |
| `refresh` | Explicitly checks a configured update source. |

### Per-call overrides

`bg`, `action`, `user_agent` and `hostname` are optional per-call solve keywords.

- **`bg`:** an explicit bg replaces only that operation's form field. Otherwise the verified selected bundle supplies it. Limited to 16384 UTF-8 bytes.
- **`action`:** an explicit action wins over URL `sa`; absent both, no action is invented. Empty selected action is invalid. Limited to 256 UTF-8 bytes.
- **Both:** must be nonempty when given. Overrides do not mutate the bundle or later calls.
- **`user_agent`:** sets the `User-Agent` header for the version, anchor and reload requests of that solve only. Updates keep their own headers. Omit it or pass `None` to preserve the transport default. Values must contain printable ASCII, include a non-space character and fit within 1024 bytes.
- **`hostname`:** replaces only the host encoded in the anchor URL's `co` value, preserving the original HTTP/HTTPS scheme and effective port. Accepts an ASCII DNS name, IPv4 or IPv6 address; pass international names as punycode. The resulting `co` is shared by anchor and reload. Omit it or pass `None` to preserve `co` exactly. It does not change the Google endpoint or proxy route.

### Errors

`AnchorpassError` exposes `code` and `stage`. Error messages omit tokens and proxy credentials.

## 🔀 Routing and proxies

- **Schemes:** proxy URLs support `http`, `https`, `socks5` (local target DNS) and `socks5h` (proxy target DNS), with explicit host and port.
- **Environment:** environment proxy settings are ignored; failed proxies never fall back to direct.
- **Updates:** omitted `updates_proxy` inherits `proxy`; explicit `None` selects direct updates. Updates have separate sessions and no CAPTCHA cookies or parameters.

## 🔐 Signed data and updates

- **Cache:** memory cache is the default. Set `cache_path` for persistent signed-envelope storage.
- **Update channel:** configure `bundle_url` and `trusted_keys` to use an operator-managed Ed25519 data channel; custom keys replace bundled trust. Import performs no I/O.
- **Modes:** `offline` disables updates only; `pin` does not bypass signature, expiry or rollback.

> [!WARNING]
> The embedded `production-1` envelope expires. Configure `bundle_url` with the [GitHub channel](../docs/channel-operations.md) to receive renewed signed data. No update URL is enabled by default; the bundled production public key needs no `trusted_keys` override for that channel.

## 🍪 Cookies and limits

- **Cookie jar:** each solve has an isolated cookie jar with domain, path, Secure and expiry checks.
- **Budgets:** at most 64 Set-Cookie headers and 16384 cookie-header bytes per solve; outgoing Cookie headers are also limited to 16384 bytes.
- **Connections:** remain pooled.

## 🧵 Concurrency and cancellation

- **Threads and loops:** sync clients support concurrent calls through a bounded handle pool; async clients belong to their event loop. Async clients may be constructed before their loop starts.
- **Cancellation:** async cancellation uses native `asyncio.CancelledError`.
- **Update DNS:** signed updates use bounded system DNS work; timeout, cancellation and close do not wait for a blocked lookup. Unfinished lookups retain their slots, and exhaustion reports `ResourceLimit`. See [update DNS limits](../docs/updates.md#python-update-dns).
- **Retries:** async GET requests may retry once after a connection failure within the same deadline. Reload POST requests are never retried.

## ⚠️ Limitations

- Python 3.11+ is declared; local tests exercise Python 3.11 and 3.13 on Linux.
- The selected transports are PycURL for synchronous requests and aiohttp with aiohttp-socks for asynchronous requests and signed updates. The repository's transport decision records the capability tests and comparison methodology.
