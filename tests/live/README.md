# 🧪 Public demo acceptance checks

> **Opt-in acceptance suite** — submits one result per selected API mode to each demo's own server verifier.

It is excluded from ordinary unit tests and GitHub Actions.
The default run is sequential, with a one-second interval, and is not a load test.

## 🗂️ Demo sites

The checked-in [site manifest](sites.json) contains:

| Demo | Protocol | Server verification |
| --- | --- | --- |
| [Google](https://recaptcha-demo.appspot.com/recaptcha-v3-request-scores.php) | `api2` | JSON verification endpoint, including action and hostname |
| [2Captcha v3](https://2captcha.com/demo/recaptcha-v3) | `api2` | Demo JSON verification endpoint |
| [2Captcha Enterprise](https://2captcha.com/demo/recaptcha-v3-enterprise) | `enterprise` | Demo assessment endpoint; action may be absent |
| [ZennoLab](https://lessons.zennolab.com/captchas/recaptcha/v3.php?level=beta) | `api2` | Verification JSON embedded in the response page |

The runner checks the current page and widget source. The 2Captcha tutorial can
show a different key from the active widget, so its compiled component is checked
against the manifest. A changed key, action or discovery structure stops that
site's checks. Only the fixed demo origins are fetched; page scripts cannot select
another host.

## 🔧 Run

Install the repository development environment described in the root README.
Then run from the repository root:

```sh
# Offline classifier, discovery and subprocess regression tests:
.venv/bin/python -m unittest discover -s tests/live -p 'test_*.py' -v

# Build helpers and exercise all seven modes on every demo:
make test-live

# Select a smaller subset:
make test-live LIVE_ARGS="--sites google two-captcha-v3 --modes python-sync go"

# Route both the library calls and demo verification through one explicit proxy:
make test-live LIVE_ARGS="--sites google --proxy http://127.0.0.1:8080"
```

`LIVE_OUTPUT` defaults to `artifacts/live-report.json`. Its parent directory must
exist. The helpers use the checkout's Python package, built Node distribution,
Go binary and Rust binary. The runner records source and helper digests before
and after the run; a changed snapshot prevents a passing exit status. Artifact
installation checks are a separate gate, `make test-package-install`.

Each site's verifier first receives a deliberately invalid token. An explicit
`success: false` is required before any library result is submitted. A timeout,
HTTP failure or malformed response is not a successful negative control.

## ✅ Interpret the report

| Status | Meaning |
| --- | --- |
| `accepted` | Server success, expected hostname and action, numeric score within `[0, 1]`, and no error codes |
| `accepted_action_unreported` | Enterprise server acceptance and expected hostname, but its response does not expose action |
| `degraded` | Otherwise accepted response containing a quota or other diagnostic |
| `rejected` | Response fails the acceptance checks |
| `unavailable` | Discovery or invalid-token control failed; solve was not attempted |
| `error` | The library, helper or verifier failed for this case |

Exit zero requires every selected case to be `accepted`, unchanged source and
helper digests, and a completed run. Limited Enterprise evidence and quota
diagnostics therefore produce exit one even when the server says `success: true`.

> [!WARNING]
> Limited evidence and diagnostics must not be relabelled as clean passes. There is no minimum-score promise:
> the finite range validates the response shape, not a production site's threshold.

The report stores verification fields, discovery hashes, durations and stable
error codes. It does not store response tokens or proxy credentials. Child output,
HTTP response size and total case duration are bounded; helper process groups are
stopped on failure or interruption. `--timeout` and `--interval` accept finite
values between one and sixty seconds. No automatic solve retries are performed.

> [!NOTE]
> Public demos and their quotas can change. These observations establish behavior
> only for the recorded source, route and time. They do not certify arbitrary sites,
> production uptime, score stability or third-party proxy providers.
