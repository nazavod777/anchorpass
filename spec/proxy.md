# 🌐 Proxy contract

[← Documentation index](../docs/README.md)

> **Explicit routing** — omitted/null proxy means direct; proxy environment variables never select or bypass a route.

An explicit proxy applies to version, anchor and reload; failure
does not trigger direct fallback.

Accepted URLs have an explicit host and positive port, optional ASCII credentials,
and an empty or `/` path. Query, fragment, whitespace, invalid percent encoding
and other paths are rejected before networking. Credentials are decoded exactly
once; `+` is literal. IPv6 hosts use brackets.

| Scheme | Target connection |
|---|---|
| `http` | CONNECT then target TLS |
| `https` | TLS to proxy, CONNECT, then independently verified target TLS |
| `socks5` | Local target DNS; SOCKS receives an IP, target Host/SNI are preserved |
| `socks5h` | Proxy target DNS; SOCKS receives the target hostname |

HTTP Basic and SOCKS username/password are supported. Proxy credentials are
never sent to the target. Certificate verification remains enabled. Package
options provide explicit custom certificate trust for local/private issuers.

The updater uses a separate transport with no CAPTCHA cookies or fields.
Omitted updates proxy inherits the CAPTCHA proxy; explicit null selects direct;
another proxy URL selects that route. A rotating proxy service can still change
its external IP while the configured URL remains the same.

> [!NOTE]
> Proxy routing for a site's own server-verification request belongs to the calling
> application. The library does not send the token to that server automatically.
