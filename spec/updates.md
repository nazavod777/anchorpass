# 🔐 Signed protocol data

[← Documentation index](../docs/README.md)

> **Signed envelope** — an envelope contains exactly `key_id`, `payload` and `signature`; the Ed25519 message is verified over exact bytes.

Payload and
signature use standard padded base64. The Ed25519 message is:

```text
UTF8("anchorpass.bundle.v1") || 0x00 || ASCII(key_id) || 0x00 || decoded_payload_bytes
```

Verification uses the exact bytes, without JSON reserialization. The key id must
already exist in configured trust. Payload has exactly the fields described in
`bundle.schema.json`: schema version 1, family `recaptcha-v3/1`, a positive decimal
revision string, Unix issue/expiry seconds, and the closed set of protocol fields.
Duplicate JSON keys, invalid UTF-8, unknown fields, booleans used as timestamps,
bad signatures, excessive lengths and future issue times beyond the allowed
clock skew are rejected. All six protocol fields are nonempty UTF-8 strings.

Revisions have at most 20 decimal digits, no leading zero, and compare first by
length and then lexicographically. A lower revision is rollback. The same
revision with different payload bytes is invalid. An expired higher revision
still establishes a floor, but cannot be used for solving.

Each operation verifies that the snapshot stays valid for its allowed duration.
Network failure can retain a valid snapshot, but cannot extend its expiry.
Offline disables update requests, not CAPTCHA requests. Pin requires an exact
revision and cannot disable expiry or rollback protection.

Default storage is memory. Explicit persistent cache stores the signed envelope
atomically and compares the disk revision again under a bounded writer lock.
Readers recheck the signed disk floor before a new operation. No silent fallback
to memory occurs when requested persistence fails. Memory mode and deleting cache
state cannot provide rollback protection across restarts.

> [!NOTE]
> The checked-in development key's private half was discarded. No default remote
> update service exists.

Operator setup, rotation limits and release prerequisites
are described in `../docs/updates.md`. The schema describes JSON shape; verifiers
also enforce byte limits, signature, timestamp ordering and trust semantics.
