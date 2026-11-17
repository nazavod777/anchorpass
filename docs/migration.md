# 🔄 Migration from invisible_captcha_bypasser

[← Documentation index](README.md)

> **Migration draft** — for the local Anchorpass development candidate.

> [!WARNING]
> Anchorpass has not been declared publicly published here. Do not deprecate the old project until the new public artifacts have been installed and verified.

- No compatibility wrapper is provided; older versions of the original project remain separate.

## <img src="../assets/c-python.svg" height="20" align="absmiddle" alt="Python"> call sites

Replace calls to the old `solve_captcha` entry point with Anchorpass `solve` or `asolve`. Preserve your application's anchor URL, intended action and routing policy. Anchorpass returns a result object; obtain the token from `.token` rather than treating the whole result as a string.

```python
from anchorpass import AnchorpassError, solve

try:
    result = solve(
        anchor_url,
        proxy="http://user:pass@127.0.0.1:8080",
        action="homepage",
    )
except AnchorpassError as error:
    handle_failure(error.code, error.stage)
else:
    verify_in_your_application(result.token)
```

- `anchor_url`, `handle_failure` and `verify_in_your_application` are application placeholders. Use the real action expected by that application.
- If `action` is omitted, Anchorpass uses URL `sa`, or omits action when `sa` is absent. It never supplies an invented default.
- `extract_action(anchor_url)` only parses and validates the URL.
- Explicit `bg`, `action`, `user_agent` and `hostname` belong to an individual solve call; they are not client configuration.
- `user_agent` sets the same HTTP User-Agent on version, anchor and reload requests. Omitting it keeps the transport default; it does not enable TLS impersonation.
- `hostname` replaces only the application host encoded by the anchor URL's `co`, retaining the original scheme and effective port. Use an ASCII domain (international names in `xn--` form) or IP literal; do not pass a URL or port. The site key must still permit the intended domain.

**Repeated operations**

- Reuse `Client` in a `with` block or `AsyncClient` in `async with`. `asolve` is the one-shot asynchronous function.
- `load_bg` and `aload_bg` return `bg` from a verified selected bundle; they make no captcha request. A custom `bg` does not disable validation of the other signed bundle fields.

## 📋 Behavior to review

- **Inputs:** follow strict Google HTTPS anchor URL validation. Duplicate query keys, credentials and fragments fail instead of being repaired.
- **Proxies:** URLs need explicit host and port. `socks5` uses local target DNS; `socks5h` uses proxy target DNS. Environment proxy variables are ignored; failures do not fall back to direct traffic.
- **Token verification:** your application's separate token-verification call needs its own route configuration. Receipt of a token is distinct from server acceptance and score.
- **Errors:** Python errors expose stable `code` and `stage` fields; the other packages also expose retryability metadata. Do not assume every error is transient or retry a reload POST after an uncertain outcome.
- **Signed data:** protocol data can expire. Memory cache is the default; persistent cache is opt-in. No default remote channel exists. `offline` and pins do not override expiry or trust checks.

Install from the local paths in the [root README](../README.md) while public registry artifacts are unavailable. See [compatibility](compatibility.md) for tested runtimes and [implementation status](implementation-status.md) before planning a production transition.

## 🚢 Draft notice for the old repository

The following copy is intended for a future separately authorized update, after a public Anchorpass release has been installed and verified. Replace placeholders with verified public links before publishing it:

> This project is deprecated and will no longer be maintained. Use Anchorpass for new integrations: `<verified Anchorpass repository URL>`. Follow `<verified migration guide URL>` to replace `solve_captcha`, configure proxy routing and handle the new result and error types. Existing versions remain available; no compatibility wrapper is provided.

> [!NOTE]
> This draft does not modify the external repository, publish packages or discontinue its maintenance by itself.
