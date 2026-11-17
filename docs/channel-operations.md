# Production channel operations

The production channel is `channels/recaptcha-v3-1/latest.json` on `main`.
Every published envelope also has an immutable
`channels/recaptcha-v3-1/revisions/<revision>.json` object with identical bytes.
The latest pointer is available at:

```text
https://raw.githubusercontent.com/nazavod777/anchorpass/main/channels/recaptcha-v3-1/latest.json
```

The embedded `production-1` public key already trusts this channel. Configure its
URL explicitly; no custom trust override is needed. Configuring this channel does
not change signature, expiry, pin or rollback checks in any client. For example:

```python
from anchorpass import Client

with Client(bundle_url="https://raw.githubusercontent.com/nazavod777/anchorpass/main/channels/recaptcha-v3-1/latest.json") as client:
    client.refresh()
```

```js
import { Client } from 'anchorpass';

const client = new Client({ bundleUrl: 'https://raw.githubusercontent.com/nazavod777/anchorpass/main/channels/recaptcha-v3-1/latest.json' });
try { await client.refresh(); } finally { await client.closeAsync(); }
```

```go
client, err := anchorpass.NewClient(anchorpass.WithBundleURL("https://raw.githubusercontent.com/nazavod777/anchorpass/main/channels/recaptcha-v3-1/latest.json"))
if err != nil { return err }
defer client.Close()
if _, err := client.Refresh(ctx); err != nil { return err }
```

```rust
let client = anchorpass::Client::builder()
    .bundle_url("https://raw.githubusercontent.com/nazavod777/anchorpass/main/channels/recaptcha-v3-1/latest.json")
    .build()?;
client.refresh().await?;
```

## Policy and prerequisites

- The signer is Ed25519 with key ID `production-1`. Store its unencrypted PEM only
  in the repository Actions secret `ANCHORPASS_SIGNING_KEY_PEM`, using GitHub's
  secret entry interface. Never put it in a committed file, artifact, command
  argument, workflow input or log. The renewal command reads the environment
  directly and verifies the public key before signing. There is no local backup
  signer. Losing the GitHub secret requires a separately reviewed key rotation
  and client trust update; the public key cannot recover the private signer.
- The trust file must contain `production-1` and must not contain `development-1`.
  The owner provisions the initial signed channel, including its immutable object;
  the renewal tool deliberately cannot initialize an empty channel.
- New envelopes last exactly 30 days. The renewal window and daily warnings start
  when at most seven days remain, including an already expired envelope. The
  constants are computed in `scripts/renew_channel.py`: 2592000 and 604800 seconds.
  There is no command-line override for either limit or the signing key ID.
- `data/upstream-fields.json` is the approved content for the next envelope.
  Field updates require the owner's normal review and approval in a commit on
  `main`. The validation helpers exercise these exact fields, and the signing
  job checks their digest again. They can differ from an older embedded release;
  automatic renewal does not require manually re-signing that release.
- Enable Actions and the `production-channel` workflow on `main`. Allow its
  GitHub token to write channel commits and operational issues. Branch protection
  must permit this specific bot push; otherwise renewal fails and opens the
  operational issue. The workflow cannot bypass branch protection.

## Daily automation

`.github/workflows/channel.yml` runs daily at 06:17 UTC and can be started manually
with **Run workflow** on `main`. Other branches are skipped. A concurrency group
serializes channel runs without cancelling an active renewal.

Every run authenticates the existing envelope, compares `latest.json` with its
immutable object, and checks for any higher revision in the immutable directory.
This inspection needs no private key and accepts an expired previous envelope
only as authenticated history. If more than seven days remain, it makes no
channel changes and runs no live demo requests. To qualify a new setup or a
reviewed change immediately, enable the optional **validate_now** boolean when
starting **Run workflow**. It runs all local and Google acceptance gates even
outside the renewal window; signing and publishing remain restricted to the
normal renewal window.

Inside the renewal window, the workflow:

1. Authenticates the original production embedded envelope without treating
   expired history as usable client input, and checks that all packaged copies
   match the canonical production data. It clones only committed source at the
   exact checked commit into a separate runner temporary directory. In that copy
   alone, it replaces the bundled keys and envelope with an ephemeral Ed25519
   test signer, revision `1`, the approved upstream fields, and a fresh 30-day
   lifetime, then synchronizes the package data. The test private key exists only
   in the staging process and is never serialized. Its public key has a separate
   ID and cannot authenticate the production channel.
2. Installs the validation copy into its own virtual environment and requires
   `make check` to pass there. Runtime client expiry, signature and rollback
   checks remain active; there is no clock override or test bypass.
3. Builds the live helpers in the same validation copy and runs the public Google demo, with an invalid-token
   control, in every mode: `python-sync`, `python-async`, `node-async`, `node-sync`,
   `go`, `rust-async`, and `rust-blocking`. All cases must be fully `accepted`;
   errors, unavailable responses and degraded acceptance stop renewal.
4. Confirms that the validation copy changed only the expected package data,
   that its signed data still has the approved fields, and that neither checkout
   acquired source changes or untracked modules. It checks that `main` still
   points at the checked commit. The public validation report records that
   commit and the SHA-256 digest of the approved fields.
5. Starts a separate signing job on a fresh runner, with its own exact-commit
   checkout and Python environment. It neither downloads validation artifacts
   nor runs the tests or live helpers. Before accessing the signer, it checks
   `main` again, authenticates the current production channel and original
   embedded data, confirms renewal is due, and compares the committed upstream
   fields with the validated digest.
6. Makes the production secret available only to the local renewal step. The command signs
   the next numeric revision, verifies it against the trusted public key, writes
   the immutable object without overwriting history, and atomically replaces the
   local latest pointer.
7. Checks `main` again, commits only the new immutable object and latest pointer,
   and pushes explicitly to `HEAD:refs/heads/main`. A concurrent branch update
   fails safely; there is no force push, merge, rebase or automatic signing retry.

Public expiry, validation metadata, live acceptance and renewal JSON reports are
retained as Actions artifacts. Neither checkout nor any signing key is uploaded.
The validation job has read-only repository access; only the separate signing
job can write channel commits. The notification job alone can update issues.
The workflow emits an expiry warning
and creates or updates the single issue titled
`[channel] Production renewal and expiry`. A failed run leaves that issue open;
a successful publication resolves it. The same issue is reused, including after
closure. A successful daily inspection outside the window also resolves a prior
alert. New operational issues mention `@nazavod777`; title lookup also checks the
GitHub Actions bot author. Ensure maintainers receive notifications for that issue and Actions
failures. An Actions outage can prevent both renewal and its own warning delivery.

## Read-only inspection

From the installed development environment, run:

```sh
.venv/bin/python scripts/renew_channel.py check
```

This prints only public metadata as JSON. Exit zero means the existing channel
was authenticated and inspected; check `expired` and `needs_renewal` to determine
its health. Invalid signatures, mismatched immutable bytes, rollback pointers,
missing files and excessive previous lifetimes produce a nonzero exit.

`renew` performs only the local write. Run it only after the same checks and live
acceptance gates; obtain its environment secret through an approved secret
manager. It does not publish or run the gates itself. An immediate retry outside
the renewal window is a no-op and needs no secret. File options `--keys`,
`--fields` and `--destination` support isolated synthetic tests and operator review;
they do not change the production workflow's approved paths.

## Failure and recovery

- Fix the failed gate, unavailable public demo, missing secret, key mismatch or
  push permission before starting another workflow run. Do not weaken acceptance
  or client expiry checks to make renewal pass.
- If `main` moved, start a fresh run so the new head receives all checks. A
  locally signed but unpushed object from a failed hosted run is not published.
  Review remote state before any manual publication; immutable revision bytes
  already on `main` always take precedence.
- A partial local filesystem failure can leave a complete next immutable object
  while the latest pointer still references the previous object. The tool then
  refuses further renewal because it detects a higher revision. Verify the
  retained object's signature, approved fields, lifetime and acceptance evidence,
  then finish staging those exact bytes with `scripts/release_channel.py` through
  the normal reviewed recovery procedure. Do not delete or rewrite either object
  to make the check pass. A stale `.channel.lock` similarly requires confirmation
  that its recorded process has stopped before an operator removes the lock.
- Expired history remains useful for authenticating the next revision. Clients
  still reject it for operations. Never roll the pointer back to recover expiry;
  publish a strictly higher valid revision after all gates pass.
- An expired production embedded envelope does not prevent the isolated renewal
  gates from running with fresh, test-only signed data. It remains expired for
  clients and installed packages: channel renewal does not modify installed
  embedded data. Clients must opt into the production channel or receive a
  separately released fresh envelope.

Local inspection and renewal target an owned directory and bound JSON inputs,
PEM input size and revision enumeration. The directory limit is 10000 entries;
reaching it fails rather than pruning immutable history. Neither the script nor
workflow rotates a signing key or changes client trust automatically.
