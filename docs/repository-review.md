# 🔍 Repository review and multi-site acceptance

[← Documentation index](README.md)

> **Review of November 16, 2026** — covers the four packages, their public contracts, signed data, proxy routing, build and release tooling, CI configuration, benchmark harnesses, live helpers and documentation.

- **Starting commit:** `51a5bd4597e1be9d3f0912c939526dbdccddd0d4`.

> [!NOTE]
> Historical measurement files and lockfiles were inspected structurally; their old timings were not reinterpreted as measurements of the new code.

The change inventory comes from the initial tracked-file hash snapshot, the edit
record and Git diff. Separate reviewers reproduced the original defects and
checked the fixes. [Verification evidence](verification-review.json) records
commands, original exit statuses, source identities and remaining limits.

## 📋 Confirmed defects

| Surface | Original failure | Correction and regression evidence |
| --- | --- | --- |
| Cookies, all packages | Node/Go/Rust lost cookies between requests; Python's flat map ignored cookie scope and expiry | An operation owns its cookie jar while connections remain reusable; scope, expiry, isolation and bounded input/output are tested |
| Signed updates, all packages | An authenticated expired higher revision did not prevent a later lower revision | Authentication establishes the revision floor before expiry/pin usability; memory, disk, restart, pin and invalid-signature controls pass |
| Node/Go HTML | Regex parsing accepted decoy input inside comments or script; valid unquoted attributes and entities were mishandled | Node tokenizer and Go HTML5 tokenizer pass the original reproductions |
| Go API validation | Oversized URL action reached the network, constructor options retained per-call overrides, and `issued_at:null` became zero | Invalid values fail before I/O; solve-only overrides remain per-call; null and integer zero are distinguished |
| Rust protocol and fixtures | An incoming version could suppress current script discovery; IPv6 loopback update URLs were rejected | Both protocol variants refresh the version; bracketed loopback fixtures work |
| Benchmark routing | Rust could use a direct route when a requested proxy was missing; Go accepted external fixture addresses | Missing routes fail before connecting, and fixture addresses must be loopback |
| Pressure evidence | Safety counters and mixed SOCKS address types could escape rejection; an old expiry test expected rollback fallback | Strict counters and DNS checks, explicit revision-floor semantics, newer-revision recovery and exact sample counts |
| Process cleanup | Interrupted package checkers and an exited live-helper leader could leave children running | Owned process groups are terminated; independent SIGINT and exited-leader regressions pass |
| Live classification | An enormous JSON integer crashed classification; the older checker could return a clean pass for a malformed score or quota diagnostic | Numeric range is checked safely; clean acceptance, degraded acceptance and rejection have distinct statuses and exit behavior |
| Go artifact installation | Adding `x/net` exposed a file-only package proxy with no transitive dependency archive | An offline proxy overlay supplies cached dependencies, excludes the candidate's cache subtree and uses checksums from the local archive; installation and substitution controls pass |
| Documentation | Older benchmark limitations and CI state were described as current | Historical claims are labelled by snapshot, and current qualification is linked separately |

The owner approved `golang.org/x/net v0.43.0` and `cookie_store 0.22.1`.
Nested Go/Rust consumers and lockfiles were updated with them. No new Python or
Node runtime dependency was added. Public API changes were not introduced; the
Go constructor correction enforces the existing per-call override contract.

## ✅ Live results

The [checked-in suite](../tests/live/README.md) discovers each demo's active
configuration and first requires an explicit rejection of an invalid token.
It then exercises Python sync/async, Node async/sync, Go, and Rust async/blocking.
The run used a direct route, verified TLS, sequential requests and no solve retry.

| Public demo | Cases | Observed result | Score range |
| --- | ---: | --- | --- |
| [Google](https://recaptcha-demo.appspot.com/recaptcha-v3-request-scores.php) | 7 | `accepted`; expected action and hostname, no diagnostics | 0.9–0.9 |
| [2Captcha v3](https://2captcha.com/demo/recaptcha-v3) | 7 | `accepted`; expected action and hostname, no diagnostics | 0.1–0.9 |
| [2Captcha Enterprise](https://2captcha.com/demo/recaptcha-v3-enterprise) | 7 | `accepted_action_unreported`; server acceptance, no action in the response | 0.3–0.9 |
| [ZennoLab](https://lessons.zennolab.com/captchas/recaptcha/v3.php?level=beta) | 7 | `degraded`; expected action/hostname, but an Enterprise quota diagnostic | 0.9–0.9 |

The [machine-readable record](live-acceptance-multisite.json) contains 28 unique
cases and four successful negative controls: 14 clean acceptances, seven limited
Enterprise observations and seven degraded responses. Its original exit status
is **1**, as required when any selected case is not a clean acceptance.

> [!WARNING]
> It must not be reported as a fully passing matrix.

> [!NOTE]
> A low score can still fail a real application's threshold; neither score stability nor an application threshold is promised by this suite.

Package sources, classifier, runner and built helpers stayed unchanged during
that run. The subsequent correction to the older `check_demo.py` affected its
`check()`/CLI path; the matrix imports only its unchanged `DemoError` class.
Independent review confirmed that distinction and preserved the original hashes.
The corrected single-demo path was also run against Google and returned clean
acceptance. Neither run logged response tokens or proxy credentials.

### 🔧 Reproduce

Reproduce explicitly from the repository root:

```sh
make test-live
make test-live LIVE_ARGS="--sites google two-captcha-v3"
```

> [!NOTE]
> Ordinary CI runs the offline regression suite and builds the helpers. It does not contact these public CAPTCHA endpoints.

## 🧪 Verification and limits

Python passed its language gates with 88 tests on Python 3.13.15 and 3.11.18.
Node passed 37 tests, lint, typecheck, build and installed-package checks on
Node 22.22.1 and 22.0.0. Go passed vet, race tests and build on Go 1.25.5 and
1.23.0. Rust passed formatting, Clippy and all declared feature combinations:
50 all-features/blocking tests, 46 async tests, and the registry-isolation test
run separately. Rust 1.92.0 also passed its minimum-toolchain checks.

The [first remote review run](https://github.com/nazavod777/anchorpass/actions/runs/43941720659)
passed four jobs, including the full repository and installation gates, but the
Rust-current job exposed a pre-existing test timing assumption. The cache-lock
test reused a 50 ms timeout for successful recovery involving real filesystem
syncs. Its original code is identical to the starting commit. Injecting a 100 ms
cache `fsync` delay reproduced the exact failing assertion; no injected delay
passed. Lock and permit release, followed by recovery of the same client, were
independently confirmed. The test now allows two seconds while retaining every
assertion. The delayed control and the complete Rust gate pass after that change.
Production deadlines and runtime code were not changed. The original CI log does
not establish whether disk latency or scheduling caused that specific delay, and
this is not evidence of a Rust compiler regression. The follow-up is recorded in
[verification evidence](verification-review.json); the workflow records the result
for each subsequent commit.

Installed wheel/sdist, npm package and extracted Rust crate were checked outside
the source tree. The Go consumer additionally exercises the dependency-resolution
fix described above. Source-level tests do not substitute for these install gates.

The current finite [pressure record](../benchmarks/reports/pressure-review/summary.json)
has four runs and 3764 operations, including intentionally failing timeout,
authentication, expiry and overload cases. It requires the current revision-floor
contract. The earlier [pressure measurements](pressure-results.md) remain unchanged
and explicitly historical. This short rerun does not establish long-term memory
stability or a new performance ranking.

No public registry publication, release tags, production update-channel deployment
or legacy-repository deprecation was performed. Earlier sealed `0.1.0` candidates
do not contain these production fixes and are not current release artifacts.
Broader operating systems, dependency vulnerability databases, power-loss cache
durability, long-running leak freedom, real third-party proxy providers and
arbitrary production applications were not qualified by this review. Enterprise
action confirmation and a quota-free ZennoLab result remain external limitations
of the chosen public demos, not clean passing observations.

Demo discovery used one `perplexity_search` call and direct inspection of the
primary demo pages and their active scripts; no higher-tier research calls were
used.
