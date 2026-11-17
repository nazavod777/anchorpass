# 📐 Protocol family `recaptcha-v3/1`

[← Documentation index](../docs/README.md)

> **Supported input** — an HTTPS `google.com` or `www.google.com` anchor URL, processed in a fixed five-step request sequence.

The supported input is an HTTPS `google.com` or `www.google.com` URL with path
`/recaptcha/api2/anchor` or `/recaptcha/enterprise/anchor`. Credentials, fragments,
duplicate query keys, malformed percent escapes and ports other than 443 are
rejected. `k` and `co` must be nonempty. Unknown query fields are preserved.

The request sequence is:

1. Fetch `/recaptcha/api.js` or `/recaptcha/enterprise.js` and read its release
   version from the `releases/<version>/` references.
2. Fetch the anchor with the current version and resolved action.
3. Extract the HTML input with id `recaptcha-token`, independent of attribute
   order, and decode HTML entities.
4. POST form-encoded fields to the corresponding reload endpoint: verified
   bundle fields plus version, anchor token, site key, origin and optional action.
5. Parse the bounded JSON/XSSI response and require a nonempty `rresp` token.

Native form encoders handle bg and action. Neither value is interpolated into
a pre-encoded request template. Python, Go and Rust reject redirects. Node allows
at most two GET redirects within the same origin and `/recaptcha/` path; updates
and reload POST redirects are rejected. Redirects never select a new route.
Each solve has its own
cookies; connections can be reused without sharing cookies between operations.

Per-call User-Agent overrides apply to all three HTTP requests, without changing
update traffic or later calls. A per-call hostname override replaces the host in
the origin encoded by `co`, preserving its scheme and effective port; the same
resolved `co` is sent to anchor and reload. Input rules and language-specific
names are defined in [the public API contract](api.md).

> [!NOTE]
> Only the api2 demo flow has external server-acceptance evidence. Enterprise uses
> the same locally tested request contract, with no live acceptance claim.
