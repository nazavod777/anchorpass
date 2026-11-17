# 🔐 Signed protocol data

[← Documentation index](README.md)

> **Embedded, signed, Ed25519-verified data** — the canonical envelope and trusted public keys are in [`data/bundled`](../data/bundled).

- Each standalone package includes a synchronized copy.
- [`spec/contract.json`](../spec/contract.json) defines the shared family and signed-message contract.

> [!NOTE]
> The [GitHub channel](channel-operations.md) renews signed data automatically. Clients opt in by configuring its HTTPS URL; no remote source is enabled by default.

## ✅ Verification

An envelope contains `key_id`, base64 `payload` and an Ed25519 `signature`. Verification uses the exact decoded payload bytes, not reserialized JSON. The signed message is:

```text
UTF8(anchorpass.bundle.v1) || 0x00 || ASCII(key_id) || 0x00 || exact payload bytes
```

The loader validates signature, trusted key, schema, closed field set, family, revision and timestamps before using fields. Duplicate JSON keys and oversized data are rejected. Unknown keys are not trusted merely because a bundle supplies them. Each solve retains one immutable snapshot, whose expiry must cover its allowed operation deadline. A custom `bg` changes one outgoing field; signature, expiry and the remaining bundle checks still apply.

### 🔐 Current envelope

| Field | Value |
| --- | --- |
| Key ID | `production-1` |
| Revision | `1` |
| Family | `recaptcha-v3/1` |
| Issued at | `2026-11-16T23:10:12+00:00` |
| Expires at | `2026-12-16T23:10:12+00:00` |

These timestamps were decoded from the actual envelope.

> [!WARNING]
> The private `production-1` signer is held in the repository's `ANCHORPASS_SIGNING_KEY_PEM` Actions secret. Public packages contain only its public key. The initial channel revision is `2`; later renewals use higher immutable revisions. Clients without an update URL must obtain fresh trusted data before the embedded envelope expires.

## 🔧 Source and cache policy

- Set an explicit HTTPS bundle URL to enable remote checks. Plain HTTP is restricted to loopback fixtures; redirects are rejected.
- Imports and construction perform no update request. Preparation, solves and explicit refresh apply the configured bounded update policy; no perpetual background updater runs.

**Cache**

- Memory mode is the default in every package. It retains accepted state only within its process/client-sharing lifetime and cannot guarantee rollback protection after restart.
- Configure a cache path to opt into persistent signed-envelope storage and revision history. Writes use bounded locking and atomic replacement.
- Cache corruption, lock failures and write failures are errors rather than an implicit downgrade to memory. Deleting persistent state resets its accumulated rollback floor.

**Refresh and rollback**

- An automatic refresh may retain a currently valid compatible snapshot when a candidate fails, provided that snapshot still meets the observed revision floor.
- An authenticated higher revision establishes that floor even when it is expired or does not match the pin; an older snapshot then cannot be used as fallback. Invalid signatures do not raise the floor.
- Recovery requires a compatible valid revision at or above that floor, with the same verified bytes when the revision is equal.
- Refresh cannot extend signed expiry. Explicit `refresh` reports candidate failures.
- `offline` disables only update networking. An exact revision pin does not bypass signature, expiry or rollback validation.

> [!WARNING]
> An expired snapshot is not usable, and there is no expiry grace period. Correct local clocks are required.

### Options by language

| Language | Source | Persistent cache | Explicit offline / pin |
| --- | --- | --- | --- |
| <img src="../assets/c-python.svg" height="20" align="absmiddle" alt="Python"> | `bundle_url` | `cache_path` | `offline`, `pin` |
| <img src="../assets/c-nodejs.svg" height="20" align="absmiddle" alt="Node.js"> | `bundleUrl` | `cachePath` | `offline`, `pin` |
| <img src="../assets/c-go.svg" height="20" align="absmiddle" alt="Go"> | `WithBundleURL` | `WithCachePath` | `WithOffline`, `WithPinnedRevision` |
| <img src="../assets/c-rust.svg" height="20" align="absmiddle" alt="Rust"> builder | `bundle_url` | `cache_path` | `offline`, `pin` |

### Update route

- The update route inherits the captcha route unless explicitly set to direct or a separate proxy. A failed inherited proxy never causes a direct retry.
- Python and Node distinguish an omitted route from explicit `None`/`null`; Go and Rust expose explicit route modes.
- Updates use a separate session/pool without captcha cookies or parameters.

### Concurrent clients

- Concurrent clients share update work for matching configuration. Python and Rust share raw downloads across synchronous and asynchronous execution contexts; signature, pin and cache acceptance remain individual.
- Their update workers are finite and bounded, with no permanent update thread.
- Cancelling one subscriber does not cancel the others; the last subscriber cancels and drains its worker.
- Python snapshots configured CA file contents when constructing a client, so CA changes require a new client.
- The operation deadline and the documented limits of operating-system DNS still apply.

### Python update DNS

Python bounds operating-system DNS work for update origins, proxy hosts and local
SOCKS5 target resolution to 16 one-shot daemon threads per process, without a queue.
Timeout, cancellation and client close drain the update worker and close its sockets
without waiting for an uninterruptible system DNS call. Such a call keeps its DNS slot
until it actually returns; it does not keep the process alive or retain its update loop.
When all DNS slots remain occupied, new lookups fail with `ResourceLimit` at the
`updates` stage. Slots become available again as those calls finish. This bound is
specific to signed updates; CAPTCHA requests retain their transport's DNS behavior.

### Trust configuration

Application trust configuration differs: Python `trusted_keys` and Go's `WithTrustedKeys` replace the bundled trust set; Node and Rust augment it. A bundle cannot grant trust to its own signer.

## 🚢 Managed GitHub channel

Compatible data changes can be shipped independently of package code; incompatible schemas, protocol logic or unavailable trust roots require a code release. A corrective data release must use a higher revision rather than overwrite previously published bytes. Pins remain exact.

The [channel operations guide](channel-operations.md) documents the HTTPS URL, client configuration and scheduled workflow. Daily checks renew data for 30 days when 7 days or less remain, after repository tests and controlled Google demo acceptance. Protocol field changes remain reviewed changes to `data/upstream-fields.json`; the workflow does not discover new protocol fields. The [release runbook](release.md) and [generation support policy](support-policy.md) cover signing custody, rotation and retirement. `scripts/release_channel.py` also supports local staging with bounded reads, monotonic revisions and immutable objects.

> [!WARNING]
> GitHub availability and Actions scheduling are external dependencies. A failed renewal cannot extend signed expiry. Protocol-generation support dates remain unset until an explicit support commitment is recorded.
