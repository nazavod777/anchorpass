# 🚢 Release operations

[← Documentation index](README.md)

> **Executable release procedure** — the first common package version is `0.1.1`. See the current registry status below.

> [!WARNING]
> Publication uses the concrete reviewed artifacts and their manifest digest. Public GitHub hosting and the automatic signed-data channel do not establish registry availability. Legacy-project edits require separate authorization.

## Current registry status

`0.1.1` is public on [PyPI](https://pypi.org/project/anchorpass/0.1.1/) and [npm](https://www.npmjs.com/package/anchorpass/v/0.1.1/). Their public distributions matched the approved SHA256 values and passed fresh standalone installations outside the checkout. Python checks covered sync/async APIs, embedded data and an authenticated loopback CONNECT proxy with verified TLS; npm checks covered CJS/ESM, types and sync/async APIs.

Rust `0.1.1` is also public on [crates.io](https://crates.io/crates/anchorpass/0.1.1); its downloaded archive matched the sealed SHA256 and publication was confirmed in the journal. A fresh Cargo consumer downloaded the registry dependency without a local path replacement, compiled successfully and passed offline embedded-data checks for async/blocking `load_bg` and `extract_action`. Replacement registry tokens are stored in the `anchorpass-production` environment; the owner confirmed that the initial tokens were revoked. See [registry access](registry-access.md) for permissions and their verification limits.

The sealed manifest SHA256 is `390a43e8091ca4fd9dca8af5703b203d3a3c59f159b615eac3a78c7ca7600cea`; the approved source commit is `4dfcbe49ca502856754d62f3a40ae37175d8d3cf`. Preserve the sealed artifacts, including their packaged README snapshots, when updating operational documentation.

Go `v0.1.1` is public under the immutable `go/v0.1.1` tag at commit `c0b86f39e8b52598455ac2cd5267a74e6700159b`. A fresh consumer installed it through `proxy.golang.org` with `sum.golang.org`, a fresh module cache and no local replacement. Race-enabled standalone checks passed for embedded signed data, `ExtractAction`, both protocol families and an authenticated CONNECT proxy with verified target TLS; `go mod verify` passed. See the [Go release record](go-release-0.1.1.json) for origin and checksums:

- Module: `h1:HkoXOQh7IV2eKO1Yg9S0kxJlRStVp5v2o/694JA5CKE=`
- `go.mod`: `h1:Gok47rpUsDPkC2jMEKUOYgZvu6aox5G8wsBxPQ224ds=`
- Public proxy ZIP SHA256: `fb3cc14b24002071b2e2f9249f418305df9872d1c7a0e4262394cf86e466cca2`

All files in the public module ZIP match the sealed Go artifact's file contents. ZIP compression and metadata need not be byte-identical to the sealed local ZIP; its archive digest and the historical source commit above remain unchanged. Operational documentation updates do not rebuild that artifact or move its tag.

```sh
go get github.com/nazavod777/anchorpass/go@v0.1.1
```

## 🔧 Prepare exact local artifacts

Use a quiet, reviewed checkout on Linux with the repository's locked development
environment, Node dependencies and Go/Rust toolchains installed. Finish the release
gates and retain their output before sealing the candidate:

```sh
make check test-package-install
.venv/bin/python -m unittest discover -s tests/release -p 'test_*.py' -v
.venv/bin/python scripts/release.py prepare --version 0.1.1 --output artifacts/releases/0.1.1
.venv/bin/python scripts/release.py verify --output artifacts/releases/0.1.1
.venv/bin/python scripts/release_check.py --output artifacts/releases/0.1.1 --work-dir /absolute/new-sealed-check
sha256sum artifacts/releases/0.1.1/release.json
```

### `prepare`

- Checks Python, npm, npm lockfile, Rust and contract versions, and the Go module path.
- Builds standalone wheel, sdist, npm tarball, crate and Go proxy `.zip`/`.mod`/`.info` files.
- Package content checks require signed data, public keys, license notices and Node's built worker and import entrypoints.
- Builds run in owned temporary directories; the checkout's package files are not rewritten.
- The Go archive excludes nested benchmark modules. Its `.info` time is a deterministic local fixture time, not a claim about a future VCS tag timestamp.

### Release record

`release.json` records exact artifact paths, sizes, SHA256 values, the source
fingerprint and planned language tags. `SHA256SUMS` covers the artifact files.
`journal.jsonl` records the source identity before work and each actually prepared
target. A target directory is installed atomically. Resume the identical command
after a partial build: complete targets are verified and reused. Changed sources,
changed existing bytes, unexpected archive contents and inconsistent journals stop
the run. Keep one canonical release directory and its backups per version; do not
discard it and recreate that version to evade an identity conflict.

> [!NOTE]
> The manifest is an identity record, not a cryptographic signature. Its digest must be reviewed through a trusted channel before publication. Source fingerprints exclude generated output, caches and benchmark workspaces; repository gates and the reviewed commit still cover the complete release tooling and harness.

### `release_check.py`

`release_check.py` installs those exact sealed distributions in fresh consumer
directories outside the checkout. It reuses the local TLS/authenticated proxy
fixtures to check Python wheel and sdist, npm's CJS/ESM and async/sync entrypoints,
the Go proxy archive and the extracted Rust crate. It also requires a byte-identical
Cargo repack and runs `cargo publish --dry-run` without uploading. The new owned
work directory retains each command's log, exit status and a `result.json` bound to
the manifest digest. Its loopback fixtures are test infrastructure; installed
packages may not read implementation or data from the source checkout.

### Go consumer

The Go consumer uses an offline file proxy and a fresh module cache. Run the
repository gates first to populate the Go dependency download cache. The proxy
can read cached transitive archives, but excludes the Anchorpass module subtree;
the candidate ZIP/MOD/INFO and dependency checksums come only from the sealed
artifact. A missing cached dependency stops the check with a preparation hint.

### Failures and locks

Build commands have finite timeouts and owned process groups. Build logs include
the original command and exit status. A failed target retains its latest build
log as `<target>-failed.log`. If interrupted by a hard process kill, inspect the
PID in `.lock` and confirm that exact process is gone before removing only that
lock. A torn journal is a failure requiring recovery from the saved journal; the
tool does not silently truncate it or infer a completed publication.

### Local handoff

Copy the reviewed bytes into local handoff storage without registry access:

```sh
.venv/bin/python scripts/release.py stage --output artifacts/releases/0.1.1 --destination /absolute/handoff
```

Each staged target gets a receipt only after digest verification. Resume copies
only missing identical files and never replaces conflicting ones. Keep the source
directory with its journal: the handoff copy intentionally contains the manifest,
checksums and package files, while the source journal is the delivery ledger.

## 🚢 Enable the protected publication workflow

The checked-in `release.yml` runs only by `workflow_dispatch`. `prepare` runs the
repository gates, builds and uploads a reviewable artifact. `publish` downloads
that exact artifact from a selected prior release run on the same commit and
requires its reviewed manifest SHA256. It never rebuilds packages from the current
checkout for publication. The crate uploader first restores `Cargo.toml.orig` and
repackages it; any difference from the approved crate stops publication.

### 📋 Prerequisites

Before enabling `publish`, the owner must complete and record these prerequisites:

1. Create/approve the GitHub repository and `origin`, review the exact commit and
   verify ownership/availability of `anchorpass` on PyPI, npm and crates.io, plus
   the Go module path. If a name is unavailable, change manifests and consumers in
   a new reviewed candidate; the workflow does not guess another namespace.
2. Create the GitHub environment `anchorpass-production` with required reviewers,
   restricted release branches and administrator bypass disabled. Merely naming
   an environment in YAML does not create a required approval gate. Review the
   workflow commit and artifact digest before approving this environment.
3. Configure environment secrets `PYPI_API_TOKEN`, `NPM_TOKEN` and
   `CRATES_IO_TOKEN` with only the needed publish permissions. Provision them in
   the service/runner secret stores, never in repository files or workflow inputs.
   The [registry access guide](registry-access.md) lists the settings for the
   first upload and the narrower permissions to use afterwards. Credentials and
   registry publication are intentionally a separate step from candidate preparation.
   The workflow pins the canonical Python uploader to `twine==7.0.0`; the primary
   [PyPI metadata](https://pypi.org/pypi/twine/7.0.0/json) declares Python `>=3.10`.
4. Approve a current controlled acceptance record and the package/platform and
   bundle compatibility results for this commit. A successful packaging job does
   not satisfy the application's live acceptance prerequisite.
5. Archive the reviewed workflow artifact and manifest outside expiring CI
   artifact retention. Store successive partial-publication journals there too.

### 🔧 Publishing

Dispatch `operation=publish`, select the prior run ID, exact version, approved
manifest digest and registry target. The workflow uses canonical `twine upload`,
`npm publish` and `cargo publish`. It checks the public version endpoint before
upload, downloads existing artifacts from fixed registry hosts and compares their
actual SHA256 values. A fully matching existing version is confirmed and skipped.
For a partially published PyPI version, only missing distribution files are sent
after every existing file has matched. Conflicting versions stop without upload.

After upload, bounded public-registry checks must confirm the exact version and
all expected bytes before a `published` receipt is appended. A successful uploader
exit alone never creates a receipt. Uploader stdout/stderr is not persisted, to
avoid recording credentials; the local publication log retains command and exit
status. No automatic upload retry occurs after an uncertain response. Inspect the
registry and journal first; if missing files still need an upload, dispatch again
with the latest receipt artifact and explicitly select `retry_unconfirmed=true`.
The new attempt still checks all public bytes before sending anything.

### ⚠️ Failures and recovery

Publication is sequential and not atomic across registries. The workflow retains
the updated release artifact even on failure. Resume with that run ID on the same
reviewed commit. Matching published targets are skipped. If a registry removed a
previously confirmed version, operator recovery is required; the tool does not
silently recreate it. An incorrect published version needs a new code version,
not different bytes under the old version. Apply registry deprecation/yank notices
only after a separate approval; preserve previous valid artifacts.

### Local publisher

For an authorized local publisher, the equivalent command is:

```sh
.venv/bin/python scripts/release_publish.py --output artifacts/releases/0.1.1 \
  --target python --approved-manifest-sha256 REVIEWED_MANIFEST_SHA256 --execute
```

Repeat for `npm` and `rust`. This command intentionally fails without `--execute`
or with a mismatching manifest digest. There is no remote publication in the test
suite. The script fixes upload/download endpoints; it does not accept a production
registry URL from an unreviewed input.

## 🏷️ Go and language tags

The Go module lives under `go/`; the published `go/v0.1.1` tag points to
`c0b86f39e8b52598455ac2cd5267a74e6700159b`. Preserve this tag and the original
sealed artifacts. The [Go release record](go-release-0.1.1.json) records the public
proxy origin, checksums and standalone verification. File-content equality with
the sealed module does not imply identical ZIP compression or metadata.

For future versions, review the concrete root commit and remote, confirm that
candidate sources match, and record the full SHA before creating language tags.
After approval, push only explicit tag refs; never tag an implicit current `HEAD`
or move an existing tag. Read back each remote ref, then install Go from a fresh
consumer without a local replacement and record its module checksum and VCS
commit. Install other packages from their public registries and rerun standalone
public API/proxy scenarios before declaring the external release complete.
The legacy README deprecation/link edit requires its own external authorization.
See [migration](migration.md).

## 🔐 Signing custody, renewal and rotation

The approved signer is `production-1`. Its public key is checked into
`data/bundled/keys.json` and synchronized into every package. Its private Ed25519
PEM is held in the repository's `ANCHORPASS_SIGNING_KEY_PEM` Actions secret.
The public fingerprint and approved 30-day lifetime / 7-day renewal margin are
recorded in [support policy](support-policy.md). The old development key is no
longer a bundled trust root. Package builds and tests do not generate or access
the production private key.

GitHub secret storage is the currently selected custody mechanism; no local
recovery copy was retained. GitHub does not return the stored secret value.
Loss of that secret requires key rotation and a package/application trust update.
Keep signing permission separate from registry-publish permission. The automated
workflow exposes the signer only to its signing step, after validation. Never store a
private key, credential or recovery copy in the repository, release archive,
consultation packet or ordinary logs. Public key files are not secret.

### 🔧 Signing and staging

Use the existing producer with an operator-supplied PEM path in that signing
environment; it refuses an existing output filename. Determine expiry with code
from the approved lifetime, rather than mental date arithmetic:

```sh
python -c 'import os,time; print(int(time.time()) + int(os.environ["APPROVED_LIFETIME_SECONDS"]))'
# Use the printed value as EXPIRES_AT in the signing environment.
.venv/bin/python scripts/build_bundle.py --key /operator/private/signing-key.pem \
  --key-id APPROVED_KEY_ID --fields /operator/reviewed-fields.json \
  --revision NEXT_REVISION --expires-at EXPIRES_AT --output /operator/new-envelope.json
.venv/bin/python scripts/release_channel.py --envelope /operator/new-envelope.json \
  --keys /operator/approved-public-keys.json --destination /operator/staged/recaptcha-v3-1 \
  --minimum-remaining-seconds APPROVED_RENEWAL_MARGIN_SECONDS \
  --maximum-lifetime-seconds APPROVED_LIFETIME_SECONDS
```

The channel staging tool checks bounded signed bytes, trusted nondevelopment
keys, expiry coverage, the approved maximum lifetime, monotonic revisions and
immutable revision content. It emits local `revisions/<revision>.json`, an atomic
`latest.json` containing the exact signed envelope, and a journal. It performs no
upload, DNS change or service deployment. These clients consume a signed envelope
URL; a separate unsigned JSON pointer is not a supported client format.

### 🚢 GitHub delivery

The approved channel is the raw HTTPS file in the public repository:

```text
https://raw.githubusercontent.com/nazavod777/anchorpass/main/channels/recaptcha-v3-1/latest.json
```

Clients must explicitly configure this URL. The daily workflow validates the
repository and the Google demo before renewing a due envelope, commits a higher
immutable revision and updates `latest.json`. It does not publish registry packages
or discover new field values. See [channel operations](channel-operations.md) for
the schedule, controls, notifications and recovery procedure. GitHub controls raw
response caching; signed expiry and monotonic client acceptance remain authoritative.

### Alternate hosting

Before deployment, assign an HTTPS hostname, storage/service owner, TLS renewal,
monitoring receiver, immutable object retention and restore responsibility. Serve
one path per protocol family, for example `/recaptcha-v3-1/latest.json`, and retain
`/recaptcha-v3-1/revisions/<revision>.json` without overwrites. Return
`Content-Type: application/json` and `Cache-Control: no-store` for `latest.json`;
immutable revision objects may use a long cache lifetime. Disable redirects,
authentication challenges, HTML error substitution and content transformations.
TLS verification remains enabled. Cache headers never extend signed expiry.

After separately authorized deployment, read back exact HTTPS bytes, verify their
signatures/digests with all declared installed package generations, test refresh
and pin behavior, and monitor expiry and revision progress from independent
clients. Renewal monitoring must alert before the approved margin, using the
decoded signed `expires_at`, and must fail on an unverifiable object. A failed
renewal never authorizes using expired data. Publish a higher revision restoring
previous good field values when correcting data; never roll a channel backward.

### 🔐 Key rotation

For planned key rotation, ship old+new public trust first, verify installation and
the older-generation matrix, then sign a higher revision with the new key. Keep
the old channel signing capability for the announced overlap if old packages
still trust only the old key. Each envelope has one signer: rotating it in place
does not make an old-only client trust a new key. Python/Go replace their bundled
trust sets when configured; Node/Rust augment them, so applications must account
for that difference. Remove old trust only in a reviewed code release after the
support window. If the only trusted key is compromised or lost, a code/trust
update may be required; a bundle cannot repair trust in its own signer.

> [!NOTE]
> The signing key, GitHub hosting, 30-day lifetime and 7-day renewal margin are approved. Generation support dates remain a separate decision. See [support policy](support-policy.md).
