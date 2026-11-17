# 🧭 Implementation status

[← Documentation index](README.md)

> **Status recorded on November 17, 2026** — separates local implementation evidence from external acceptance and release readiness.

- The subsequent [repository review](repository-review.md) records fixes and current verification; earlier counts and sealed artifacts below identify their historical snapshots.
- [`PLAN.md`](../PLAN.md) is the development plan and includes historical proposals; its unchecked milestones are not delivery claims.

## ✅ Implemented locally

The repository contains:

- independent Python, Node.js, Go and Rust packages;
- synchronous and asynchronous usage;
- shared protocol/proxy vectors and explicit proxy routing;
- per-call `bg`, action, User-Agent and hostname overrides, and verified bundle helpers;
- signed update loaders, with memory-default and opt-in persistent caching;
- local protocol, proxy and failure fixtures;
- package build tooling and CI configuration.

### 🔐 Signed data

The embedded Ed25519 envelope is shared through `data/bundled` and copied into each package. `development-1` is a development public key; its private signing key was discarded. The current expiry is recorded in [updates](updates.md).

> [!NOTE]
> No production signer, default remote channel or deployed update endpoint is claimed.

### 🧪 Verification

- Local fixtures exercise TLS/CONNECT, SOCKS, target DNS distinctions and negative controls against direct fallback.
- Independent implementation, integration and security reviews accepted their checked scopes.
- [verification.json](verification.json) and [verification-matrix.json](verification-matrix.json) preserve earlier snapshots; [verification-release.json](verification-release.json) records the closing candidate, exact artifacts and subsequent checks.
- The repository gates include contract/data integrity, formatting, types, unit and race tests, feature combinations, builds and release-tooling regressions.

The following historical results and their original statuses are linked in the closing verification record:

| Scope | Observed result |
|---|---|
| <img src="../assets/c-python.svg" height="20" align="absmiddle" alt="Python"> 3.13.15 | 77 tests passed; Ruff, strict mypy and build passed |
| <img src="../assets/c-python.svg" height="20" align="absmiddle" alt="Python"> 3.11.18 | The same 77 tests passed |
| <img src="../assets/c-nodejs.svg" height="20" align="absmiddle" alt="Node.js"> Node 22.0.0 and 22.22.1 | 25 tests passed; lint, typecheck and build passed |
| <img src="../assets/c-go.svg" height="20" align="absmiddle" alt="Go"> Go 1.23.0 and 1.25.5 | `go test -race`, vet, format and build passed, including the final TLS-policy isolation fix |
| <img src="../assets/c-rust.svg" height="20" align="absmiddle" alt="Rust"> Rust 1.92.0 | All-features/blocking: 42 passed, 1 deliberately isolated test; that test passed separately; async-only: 38 passed; no-features, fmt, Clippy, build and package passed |
| Isolated artifacts | Wheel, sdist, npm tarball, Go module archive and Rust crate passed their outside-checkout checks |

### 📦 Sealed `0.1.0` candidate

- **Contents:** seven artifact files: wheel, sdist, npm tarball, crate and Go proxy ZIP/MOD/INFO.
- **Outside-checkout checks passed:** the exact wheel and sdist, npm CJS/ESM async/sync entrypoints, Go proxy consumer and extracted crate.
- Python, Node and Go consumers used authenticated loopback CONNECT and verified target TLS.
- Rust's crate tests passed and its canonical publication dry-run reproduced the approved crate digest.
- The signing CLI generated valid data and rejected overwriting it.

> [!NOTE]
> These are local results, not registry publication or remote CI execution.

## 🔧 Private repository verification

- **Repository:** [nazavod777/anchorpass](https://github.com/nazavod777/anchorpass), with private visibility. Pushes and pull requests start the [verification workflow](https://github.com/nazavod777/anchorpass/actions/workflows/ci.yml).

**First remote run.** It exposed IPv4-only test origins combined with an assumption that `localhost` resolves to IPv4. Controlled IPv6-first resolution reproduced the Node SOCKS failure and the same five Rust proxy failures. The fixtures now use deterministic DNS results or the actual bound listener address while retaining DNS, TLS, credential and routing assertions. The npm installed-package check also derives its archive name from `package.json` and rejects a mismatching installed version; an old untracked archive previously concealed that check's stale default filename.

**Extracted-crate check.** It exposed a separate race: the unavailable-proxy test released its selected port before connecting, allowing another test server to occupy it. A controlled reuse schedule reproduced both affected failures. The negative test now keeps a bound, non-listening socket alive through the request and verifies that another listener cannot claim the port. Parallel execution and the original connection-refusal assertion remain enabled.

Those CI repair changes affected tests and packaging verification; the later repository review also changes production implementations and dependencies.

- The original closing snapshot remains in [verification-release.json](verification-release.json); [verification-github.json](verification-github.json) records the CI repair snapshot and its revised unpublished candidate.
- Remote results are available through the workflow link and are specific to each commit.

> [!WARNING]
> Those sealed artifacts do not contain the later cookie, parser and rollback fixes and must not be published as the reviewed source.

## 📊 External acceptance evidence

- **Probe.** On November 16, 2026, the feasibility probe against Google's public demo failed its action check in the upstream baseline. Supplying `sa=examples/v3scores` to both anchor and reload produced a demo response with `success: true`, score `0.9` and the expected action.
- **Installed Python check.** A subsequent check used `Client.solve` from an actually installed Python distribution. The final installed code also passed through an authenticated loopback CONNECT relay: the demo returned `success: true`, score `0.9`, hostname `recaptcha-demo.appspot.com`, action `examples/v3scores` and no error codes. The relay recorded connections to the demo and `www.google.com`; target TLS remained enabled. The reproducible explicit live checker is [`scripts/check_demo.py`](../scripts/check_demo.py); it is excluded from ordinary unit-test gates.
- **Earlier API record.** The [earlier API acceptance record](live-acceptance.json) additionally confirms direct-route `api2` acceptance through Python sync/async, Node async/sync, Go, and Rust async/blocking. Every response had the expected action and hostname, no error codes and score `0.9`. The new Node sync checking helper initially called the wrong method; it was corrected, protected by an offline regression and rechecked successfully. [`check_demo_matrix.py`](../scripts/check_demo_matrix.py) is now an opt-in multi-site checker, excluded from normal unit tests; [its instructions](../tests/live/README.md) and [current results](live-acceptance-multisite.json) record the expanded checks.

**Expanded multi-site run:** 28 cases and four successful invalid-token controls.

- Fourteen cases passed the strict checks on Google and standard 2Captcha.
- Seven Enterprise cases were accepted without an action field.
- Seven ZennoLab cases were accepted with a quota diagnostic.
- The complete matrix deliberately exited one because those limited responses are not clean passes.

> [!WARNING]
> These observations concern the recorded demos, direct route and time; they do not establish a stable score or acceptance on arbitrary applications. Enterprise is not fully live-qualified for release.

## 📊 Transport matrix evidence

The expanded [November 17 transport comparison](../benchmarks/reports/speed-20261117/README.md) selects PycURL for Python sync and aiohttp for async, retains Undici for Node.js, `net/http` for Go, and reqwest plus a pooled Hyper/rustls HTTPS-proxy adapter for Rust. It preserves 1353 jobs with unchanged source hashes during measurement and no harness failures. See [transport decisions](transport-decisions.md) for migration verification and the final public-API comparison.

The historical [common matrix report](../benchmarks/reports/matrix/REPORT.md) preserves 618 jobs, 66624 measured observations, 1440 successful recovery observations, 144 failed capability observations and 30 unsupported jobs. Healthy performance and public API profiles had no failed samples. Harness validation recorded no routing, credential, protocol or capacity violations.

The matrix covers verified TLS, HTTP/HTTPS CONNECT, SOCKS local/remote DNS, warm/cold clients, concurrency, delayed/larger bodies, total deadlines, decoded-body limits, authentication, rate limits, resets and recovery. Resource peaks include process startup and are sampled. Raw transport adapters and full public package APIs are measured separately; Node blocking is measured at concurrency one. Only the affected Go API jobs were repeated after the final trust fix. The archive preserves the selected combined observations, both source identities and replacement metadata; the replaced original observations remain at their recorded local source path and are not included in that archive. See [transport decisions](transport-decisions.md) for conclusions and unresolved tradeoffs.

Go now has separate target/proxy roots. Independent security review also found that `CertPool.Equal` ignores custom verification constraints; every client with any custom trust pool now has its own update state. Tests prove a strict client cannot consume another client's download after rejecting the same TLS channel. Default system-root sharing, bounded registry capacity and explicit signed disk-cache semantics remain tested.

The final Rust adapter closes independent target/proxy trust. Same-host cross-trust negatives, PEM bundles, CONNECT authentication and malformed headers, total/idle deadlines, cancelled-socket EOF, credential isolation, single POST and independent update policies passed. Local HTTP/HTTPS public API smoke passed in both Rust modes. The raw Rust adapters retain their recorded limitations. Node's expanded adapters now retain a sanitized TLS error code and pass the TLS-category controls; older records remain unchanged.

The separate [loader and pressure evidence](pressure-results.md) covers all four languages, repeated failures and recovery, immediate overload rejection, DNS controls and resources after close. An additional higher-revision expiry check distinguishes expiry from revision conflict. Node's longer ordinary run retained RSS while descriptors and active resources stayed bounded; diagnostic explicit-GC controls support bounded live JavaScript heap in that finite series. They do not establish steady RSS or long-term leak freedom. Overlapping Rust builds make the main pressure latency observations descriptive, without a ranking.

The shared benchmark reader now bounds complete JSON-line responses by both bytes and deadline; a partial line cannot leave the runner blocked. Independent review also closed release path traversal, directory-link, tar/PAX allocation, FIFO input and workflow retry defects, with negative regression tests.

## 📋 Release gaps

- Require a successful remote verification workflow for the exact release commit. The private repository runs it on every push; local checks and earlier commits do not substitute for that result.
- Local minimum-runtime evidence is present; broader operating systems and architectures remain unqualified. Release claims must stay within the [tested matrix](compatibility.md).
- Establish production key custody, rotation, data expiry operations, channel hosting and old-family support policy before enabling a public update channel.
- Separately authorize registry publication, configure publisher access, verify installation from every public registry and record the published bytes and versions. Exact local artifacts, resumable publication tooling, protected manual workflow, channel staging and the [release runbook](release.md) are prepared. Registry availability is not claimed.
- After the public release is verified, separately authorize the external project's deprecation update using the [migration draft](migration.md).

> [!WARNING]
> The whole development plan is not complete. No package publication, external repository deprecation or production deployment is implied by the local implementation.
