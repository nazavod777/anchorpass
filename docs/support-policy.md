# 🛟 Protocol-generation support policy

[← Documentation index](README.md)

> **Release-operations policy** — defines how protocol generations are supported once released.

> [!WARNING]
> Registry publication is pending a separate credentials stage. Signing custody, lifetime and the GitHub channel have been approved; no end-of-support date or advance notice commitment has been set. A data lifetime is not a promise of a protocol support window.

## 🧭 Current generation

- The currently implemented generation is schema `1`, family `recaptcha-v3/1`. The initial coordinated code version is `0.1.1`.
- Package versions, generation and decimal data revision are independent identities.
- Later language-specific fixes may use independent package versions only with an updated compatibility record.
- New compatible field values use a higher signed data revision. New logic, unsupported trust, schema or family requires code/application trust delivery.

## 📋 Required decisions

Approved operating settings and the remaining release decisions:

| Required decision | Current status |
| --- | --- |
| Public package release date and repository commit | Pending registry publication |
| Production signing key IDs and public fingerprints | `production-1`; SHA256 of raw public key: `77041af18e081af7f42176116b51c25deb76671e85311bca682f361efba1e5bf` |
| Maximum signed data lifetime | 30 days (`2592000` seconds) |
| Renewal margin and alert recipients | 7 days (`604800` seconds); repository Issues notify the maintainer, `nazavod777` |
| HTTPS channel URL and operator | `https://raw.githubusercontent.com/nazavod777/anchorpass/main/channels/recaptcha-v3-1/latest.json`; GitHub Actions in `nazavod777/anchorpass` |
| End-of-support date for `recaptcha-v3/1` | Owner approval required |
| Advance notice period before retiring a generation | Owner approval required |

## 🧪 Multiple generations

- Do not replace a supported family's channel with an incompatible generation. Keep separate paths, signed snapshots and rollback floors for every supported generation.
- Before signing new data, test it against the oldest declared installed package of each language and the newest compatible one, including normal refresh, exact pins, expiry, unavailable hosting, rejected new keys and rollback.
- Store the package/family/revision/key/digest/time combination with the observed results.

> [!NOTE]
> Local vectors and synthetic signers do not establish production hosting health or current acceptance by an application server.

## 🚢 Announcement, overlap and retirement

1. **Announce** a new generation and its minimum package/trust versions before switching
   applications.
2. **Overlap:** during the approved overlap, renew compatible data for the older
generation with a key it already trusts. If the upstream protocol no longer allows
that generation to work, record the confirmed incompatibility and notify users;
do not sign data that pretends compatibility or extend expiry automatically.
3. **Retire:** at the approved retirement date, stop promising fresh data for that family,
publish the migration instructions and retain released packages, immutable data
objects, manifests and receipts for reproducibility. Do not delete old versions
   or move their tags.

> [!WARNING]
> Retained expired objects remain invalid for solving. Clients fail when their signed data expires; they have no automatic expiry grace period.

## ⚠️ Emergency corrections

Emergency corrections use a higher data revision or a new code version. Key
compromise may require a code/trust update, especially for clients trusting only
that key. Pinning, offline use or a failed network request never bypasses signature,
expiry or monotonic revision checks. Persistence guarantees depend on retained
cache state and correct local time. See [release operations](release.md) and
[the compatibility matrix](compatibility.md) for the operational boundaries.
