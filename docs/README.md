# 📚 Anchorpass documentation

> **One page to find everything** — start with the [repository README](../README.md) for installation and usage, then follow a reading path below.

The shared [`spec/`](../spec/protocol.md) is the source of truth for all four language implementations — every package implements it natively.

## Reading paths

| You want to… | Read |
|---|---|
| 🚀 install and call the library | the [README](../README.md) and [examples/](../examples/README.md) |
| 🔀 route through a proxy | README routing section and the [proxy contract](../spec/proxy.md) |
| 🔐 run your own data channel | [Signed protocol data](updates.md), [spec/updates.md](../spec/updates.md), [support policy](support-policy.md) |
| 🚢 publish a release | [Release operations](release.md) |
| 🧭 judge maturity honestly | [Implementation status](implementation-status.md) → [repository review](repository-review.md) → [compatibility](compatibility.md) |
| 🔧 develop this repository | [public API contract](../spec/api.md), the [Makefile](../Makefile) gates, [benchmark instructions](../benchmarks/README.md) |

## Documents

| Document | What it contains |
|---|---|
| [Implementation status](implementation-status.md) | local evidence separated from external acceptance and release readiness |
| [Repository review](repository-review.md) | the closing independent review: fixes, re-verification and multi-site acceptance |
| [Compatibility](compatibility.md) | verified runtimes, platforms and their limits |
| [Updates](updates.md) | signed protocol data: embedded envelope, keys, expiry, channels |
| [Channel operations](channel-operations.md) | GitHub URL, client setup, automatic renewal and failure recovery |
| [Support policy](support-policy.md) | protocol-generation support windows |
| [Transport decisions](transport-decisions.md) | measured selection of PycURL/aiohttp, Undici, `net/http` and reqwest |
| [Pressure results](pressure-results.md) | loader, failure/recovery and finite resource observations |
| [Release operations](release.md) | exact-byte artifacts, publication, channel signing and recovery |
| [Registry access](registry-access.md) | first-upload token permissions and where to configure them |
| [Migration](migration.md) | the draft path away from `invisible_captcha_bypasser` |

## Evidence records

Machine-readable records behind the documents above. They are historical
snapshots: each names its commit and does not silently track later changes.

| Record | Content |
|---|---|
| [live-acceptance-multisite.json](live-acceptance-multisite.json) | the 28-case multi-site run with invalid-token controls |
| [live-acceptance.json](live-acceptance.json) | earlier direct-route acceptance across all four languages |
| [verification.json](verification.json) · [verification-matrix.json](verification-matrix.json) | earlier local verification snapshots |
| [verification-release.json](verification-release.json) | the sealed `0.1.0` candidate and its exact artifacts |
| [verification-github.json](verification-github.json) | the CI-repair snapshot and revised unpublished candidate |
| [verification-review.json](verification-review.json) | the closing review: check runs and independent review evidence |

## Specification — the source of truth

| File | Contract |
|---|---|
| [spec/protocol.md](../spec/protocol.md) | anchor URL rules, `api2` and `enterprise` families |
| [spec/api.md](../spec/api.md) | public API naming: `solve`, `load`, `extract` |
| [spec/proxy.md](../spec/proxy.md) | proxy schemes, routing and failure semantics |
| [spec/updates.md](../spec/updates.md) | envelope format and the Ed25519 signature message |
| [spec/errors.md](../spec/errors.md) | shared error categories and stages |
| [spec/contract.json](../spec/contract.json) | the machine-readable shared contract |
| [spec/bundle.schema.json](../spec/bundle.schema.json) | envelope JSON schema |
| [spec/vectors/proxies.json](../spec/vectors/proxies.json) | shared proxy test vectors |

## 🧩 Per-language notes

| Package | Behavior notes |
|---|---|
| <img src="../assets/c-python.svg" height="20" align="absmiddle" alt="Python"> | [python/README.md](../python/README.md) |
| <img src="../assets/c-nodejs.svg" height="20" align="absmiddle" alt="Node.js"> | [js/README.md](../js/README.md) |
| <img src="../assets/c-go.svg" height="20" align="absmiddle" alt="Go"> | [go/README.md](../go/README.md) |
| <img src="../assets/c-rust.svg" height="20" align="absmiddle" alt="Rust"> | [rust/README.md](../rust/README.md) |

[`PLAN.md`](../PLAN.md) is the Russian-language development plan; it includes
historical proposals, and its unchecked milestones are not delivery claims.
