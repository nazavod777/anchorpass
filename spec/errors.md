# 🚨 Error categories

[← Documentation index](../docs/README.md)

> **Shared categories** — every public error reports a category and stage, without response bodies, tokens or proxy credentials.

The shared category names are in `contract.json`.
Python represents cancellation using native `asyncio.CancelledError`; other
APIs expose their idiomatic cancellation representation or `Cancelled`.

`InvalidInput` and `InvalidProxy` describe inputs rejected before networking.
`ProxyConnectError`/`ProxyAuthError` are used when the transport can identify that
failure; ambiguous network failures use `TransportError`. `DeadlineExceeded` is
the total operation deadline. `HttpStatusError` and `ProtocolError` distinguish
HTTP status from a successful HTTP response with invalid protocol data.

`ConfigUnavailable`, `ConfigInvalid`, `ConfigExpired` and `ConfigRollback`
distinguish missing data, failed verification, expired data and downgrade.
`ResourceLimit` sheds work at a configured bound. `ClientClosed` rejects work
after close; Node's blocking bridge can also return `WorkerUnavailable`.

## 🔁 Retries

- Automatic retry policy is conservative: no application-level reload POST retry.
- Transport-specific GET handling and remaining release checks are recorded in
  `../docs/implementation-status.md`.
- Callers should not retry unknown POST outcomes merely because a network failure occurred.

> [!NOTE]
> Retryability is not yet a uniform public field across all languages.
