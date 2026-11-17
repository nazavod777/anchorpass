<div align="center">

<img src="assets/hero.svg" width="800" alt="Terminal: install the Python package, solve() walks anchor and reload, returns a token"><br><br>

<img src="assets/b-python.svg" height="28" alt="Python"> <img src="assets/b-nodejs.svg" height="28" alt="Node.js"> <img src="assets/b-go.svg" height="28" alt="Go"> <img src="assets/b-rust.svg" height="28" alt="Rust"><br>
<img src="assets/b-api.svg" height="28" alt="sync + async"> <img src="assets/b-proxies.svg" height="28" alt="explicit proxies"> <img src="assets/b-signed.svg" height="28" alt="Ed25519 signed data"> <img src="assets/b-license.svg" height="28" alt="MIT"><br><br>

<a href="README.md">🇬🇧 English</a> · <a href="README.ru.md">🇷🇺 Русский</a> · <a href="README.zh.md">🇨🇳 中文</a>

<br><br>
<a href="https://github.com/nazavod777/anchorpass/actions/workflows/ci.yml"><img src="https://github.com/nazavod777/anchorpass/actions/workflows/ci.yml/badge.svg" alt="CI"></a>

</div>

# ⚓ Anchorpass

> **reCAPTCHA v3 invisible solver** — four native clients for <img src="assets/c-python.svg" height="20" align="absmiddle" alt="Python">, <img src="assets/c-nodejs.svg" height="20" align="absmiddle" alt="Node.js">, <img src="assets/c-go.svg" height="20" align="absmiddle" alt="Go"> and <img src="assets/c-rust.svg" height="20" align="absmiddle" alt="Rust">, with sync and async APIs, explicit proxies and signed protocol data.

Anchorpass replays the invisible reCAPTCHA v3 HTTP flow — the `anchor` and `reload` endpoints — over plain HTTPS. No browser, no webdriver, no fingerprint stack. Protocol constants ship as Ed25519-signed envelopes with expiry and rollback checks. The logic continues [xHossein/PyPasser](https://github.com/xHossein/PyPasser) and the author's [invisible_captcha_bypasser](https://github.com/nazavod777/invisible_captcha_bypasser); every package here is a new standalone implementation of one shared [`spec/`](spec/protocol.md), not a wrapper around another language.

> [!WARNING]
> Automated CAPTCHA solving violates Google's Terms of Service. Use only against your own applications or ones you are explicitly authorized to test. A returned token does not guarantee acceptance or a score — your application still verifies it server-side.

## ✨ Features

| Feature | What it means |
|---|---|
| 🧩 **Four native packages** | Python, Node.js, Go and Rust implementations of one shared spec; no FFI between them |
| ⚡ **Sync + async** | every language ships a blocking and a native asynchronous client with one behavior contract |
| 🔀 **Explicit proxy routing** | `http`, `https`, `socks5`, `socks5h`; environment proxies are ignored, a failed proxy never falls back to direct |
| 🔐 **Signed protocol data** | Ed25519-signed envelopes with expiry and rollback floors; no default remote update channel |
| 🍪 **Per-solve cookie isolation** | every solve owns its cookie jar with domain/path/Secure/expiry rules and bounded budgets |
| 📏 **Bounded by default** | total deadlines, connect/idle bounds, body limits and an in-flight cap; overload is rejected, never queued |
| 🧪 **Shared test vectors** | one spec with protocol, proxy and failure vectors drives all four packages; CI runs the full matrix |

## 🎯 What it solves

Exactly **one** thing — obtaining a reCAPTCHA v3 invisible token through the plain HTTP flow, starting from the anchor URL of the application you integrate:

> `https://www.google.com/recaptcha/api2/anchor?k=<site-key>&co=<encoded-origin>`

| ✅ Solves | ❌ Does not |
|---|---|
| the anchor → reload token flow, `api2` and `enterprise` families (local conformance for both) | v2 checkboxes, image or audio challenges |
| strict anchor URL validation, `bg` / `action` handling, signed data updates | Enterprise live qualification — the recorded Enterprise responses carried no action field |
| explicit proxy routing for the whole captcha flow | a guaranteed score or acceptance — the observing site decides |
| | server-side token verification — your application must do it |
| | public Go release — still pending |

## 🚀 Installation

`0.1.1` is available on [<img src="assets/c-pypi.svg" height="20" align="absmiddle" alt="PyPI">](https://pypi.org/project/anchorpass/0.1.1/), [<img src="assets/c-npm.svg" height="20" align="absmiddle" alt="npm">](https://www.npmjs.com/package/anchorpass/v/0.1.1) and [<img src="assets/c-cratesio.svg" height="20" align="absmiddle" alt="crates.io">](https://crates.io/crates/anchorpass/0.1.1). The <img src="assets/c-goproxy.svg" height="20" align="absmiddle" alt="Go proxy"> version tag remains unpublished; use a local checkout for Go.

<table>
<tr><td width="260"><img src="assets/c-python.svg" height="20" align="absmiddle" alt="Python"><br><sub>virtual environment recommended</sub></td><td>

```bash
python -m pip install anchorpass==0.1.1
```

</td></tr>
<tr><td width="260"><img src="assets/c-nodejs.svg" height="20" align="absmiddle" alt="Node.js"><br><sub>install from your application directory</sub></td><td>

```bash
npm install anchorpass@0.1.1
```

</td></tr>
<tr><td width="260"><img src="assets/c-go.svg" height="20" align="absmiddle" alt="Go"><br><sub>module path + local replace until publication</sub></td><td>

```bash
go mod edit -replace github.com/nazavod777/anchorpass/go=/absolute/path/to/anchorpass/go
go get github.com/nazavod777/anchorpass/go
```

</td></tr>
<tr><td width="260"><img src="assets/c-rust.svg" height="20" align="absmiddle" alt="Rust"><br><sub>exact version; default feature is `async`</sub></td><td>

```toml
[dependencies]
anchorpass = "=0.1.1"
# blocking client: add features = ["blocking"]
```

</td></tr>
</table>

> [!NOTE]
> Python, Node.js and Rust install from their public registries; Go uses the local checkout. `make prepare-release` seals standalone artifacts (wheel, sdist, npm tarball, crate, Go archive) with a digest manifest; [release operations](docs/release.md) explain exact-byte installation, publication and channel signing.

**What the install gives you:** a self-contained package per language with the signed protocol bundle embedded inside → importing or constructing a client performs **no network I/O** → no post-install scripts, no background loops.

### 📋 Requirements

- <img src="assets/c-python.svg" height="20" align="absmiddle" alt="Python"> **3.11+** (verified on 3.11.18 and 3.13.15)
- <img src="assets/c-nodejs.svg" height="20" align="absmiddle" alt="Node.js"> **22** (verified on 22.0.0 and 22.22.1)
- <img src="assets/c-go.svg" height="20" align="absmiddle" alt="Go"> **1.23+** (verified on 1.23.0 and 1.25.5)
- <img src="assets/c-rust.svg" height="20" align="absmiddle" alt="Rust"> **1.92+** (verified on 1.92.0 and 1.96 toolchains)
- <img src="assets/c-linux.svg" height="20" align="absmiddle" alt="Linux"> x86_64 is the verified platform; other operating systems and architectures remain unqualified
- 🌐 No browser, display or system services; the selected transports are Python PycURL (sync) and aiohttp (async), Node.js Undici, Go `net/http` and Rust reqwest

## ⚙️ How it works

```mermaid
flowchart LR
    A([👤 your app]) --> C{{"🧩 anchorpass client"}}
    C -->|"one explicit route<br/>http · https · socks5 · socks5h"| P["🛰️ proxy — optional"]
    P --> V["🔎 current api.js version"]
    V --> N["⚓ anchor GET"]
    N --> R["🔁 reload POST"]
    R --> T["🎫 token"]
    T --> A
```

- every solve validates the anchor URL strictly (HTTPS, `google.com`, `api2/anchor` or `enterprise/anchor`, nonempty `k` and `co`) before any network I/O
- the current protocol version is fetched fresh for every solve, even when the URL carries `v=`
- each solve owns an isolated cookie jar; pooled connections are reused, but cookies never leak between solves or into update requests
- `bg` comes from the verified signed bundle; action precedence is explicit argument → the URL's `sa` → omitted — the library never invents a site action
- redirects are rejected, and a failed proxy is an error, never a direct retry

## 🧩 Usage

Use an anchor URL from the application you are integrating — the placeholder shape is not a runnable site configuration:

> `anchor_url = "https://www.google.com/recaptcha/api2/anchor?k=site-key&co=encoded-origin"`

The same contract in every language:

<table>
<tr><td width="120"><img src="assets/c-python.svg" height="20" align="absmiddle" alt="Python"></td><td>

```python
from anchorpass import AsyncClient, Client

with Client(proxy="http://user:pass@127.0.0.1:8080") as client:
    token = client.solve(anchor_url, action="homepage").token

async with AsyncClient() as client:
    token = (await client.solve(anchor_url)).token
```

</td></tr>
<tr><td width="120"><img src="assets/c-nodejs.svg" height="20" align="absmiddle" alt="Node.js"></td><td>

```js
import { Client, SyncClient } from 'anchorpass';

const client = new Client({ proxy: 'http://user:pass@127.0.0.1:8080' });
const token = (await client.solve(anchorUrl, { action: 'homepage' })).token;
await client.closeAsync();

const sync = new SyncClient();
const token2 = sync.solveSync(anchorUrl).token;
```

</td></tr>
<tr><td width="120"><img src="assets/c-go.svg" height="20" align="absmiddle" alt="Go"></td><td>

```go
client, err := anchorpass.NewClient(
	anchorpass.WithProxy("http://user:pass@127.0.0.1:8080"))
if err != nil {
	log.Fatal(err)
}
defer client.Close()

result, err := client.Solve(ctx, anchorURL,
	anchorpass.WithAction("homepage"))
token := result.Token // one-shot: anchorpass.Solve(ctx, anchorURL)
```

</td></tr>
<tr><td width="120"><img src="assets/c-rust.svg" height="20" align="absmiddle" alt="Rust"></td><td>

```rust
let client = Client::builder()
    .proxy("socks5h://user:pass@127.0.0.1:1080")
    .build()?;
let token = client.solve(&anchor).await?.token;

// blocking: plain threads only, never inside a Tokio runtime
let token = anchorpass::blocking::Client::builder()
    .build()?
    .solve(&anchor)?
    .token;
```

</td></tr>
</table>

`extract_action` / `extractAction` / `ExtractAction` parse the URL's `sa` without any I/O; `load_bg` / `loadBg` / `LoadBG` / `load_bg` return the verified bundle's `bg` without a captcha request. Full runnable scripts that read the anchor URL and an optional proxy from command-line arguments: [examples/](examples/README.md).

One-shot helpers create and close their own clients. Reuse a client to reuse connections. Language-specific details:

| Package | Clients | One-shot helpers | Language notes |
| --- | --- | --- | --- |
| <img src="assets/c-python.svg" height="20" align="absmiddle" alt="Python"> | `Client`, `AsyncClient` | `solve`, `asolve`, `load_bg`, `aload_bg` | [python/README.md](python/README.md) |
| <img src="assets/c-nodejs.svg" height="20" align="absmiddle" alt="Node.js"> | `Client`, `SyncClient` | `solve`, `solveSync`, `loadBg`, `loadBgSync` | [js/README.md](js/README.md) |
| <img src="assets/c-go.svg" height="20" align="absmiddle" alt="Go"> | `NewClient(options...)` | top-level `Solve`, `LoadBG` | [go/README.md](go/README.md) |
| <img src="assets/c-rust.svg" height="20" align="absmiddle" alt="Rust"> | `Client::builder()`, `blocking::Client` | use client methods | [rust/README.md](rust/README.md) |

Per-call fields — `bg`, `action`, User-Agent and `hostname` are optional overrides, never client-wide configuration:

| Package | Bundle `bg` helper | URL action helper | Per-call override |
| --- | --- | --- | --- |
| <img src="assets/c-python.svg" height="20" align="absmiddle" alt="Python"> | `load_bg`, `aload_bg`, client `load_bg` | `extract_action` | `solve(..., bg=..., action=..., user_agent=..., hostname=...)` |
| <img src="assets/c-nodejs.svg" height="20" align="absmiddle" alt="Node.js"> | `loadBg`, `loadBgSync`, matching client methods | `extractAction` | `solve`/`solveSync` options `{ bg, action, userAgent, hostname }` |
| <img src="assets/c-go.svg" height="20" align="absmiddle" alt="Go"> | `LoadBG(ctx, options...)`, `Client.LoadBG(ctx)` | `ExtractAction` | `WithBG`, `WithAction`, `WithUserAgent`, `WithHostname` on `Solve` |
| <img src="assets/c-rust.svg" height="20" align="absmiddle" alt="Rust"> | client `load_bg` | `extract_action` | `solve_with_options` with `SolveOptions` |

Explicit action wins over the anchor URL's `sa`; otherwise `sa` is preserved; when neither exists, action is omitted. The selected action is sent to both anchor and reload. Overrides replace only the outgoing fields of that one call — they never mutate the bundle, the cache or later calls.


`user_agent` (Python/Rust), `userAgent` (Node.js), or `WithUserAgent` (Go) sets the same header on version, anchor and reload, without changing update requests or later solves. `hostname` / `WithHostname` replaces only the application host encoded in `co`, preserving its scheme and port. It accepts an ASCII domain or IP, not a URL; it does not change Google's site-key domain checks. Without these options, existing headers and `co` are preserved. See the [API contract](spec/api.md) for validation and examples.

## 🔀 Routing and updates

`proxy` accepts an absolute `http`, `https`, `socks5` or `socks5h` URL with explicit host and port. `socks5` resolves the target locally; `socks5h` resolves it at the proxy. Environment proxy variables are ignored. A client keeps one route for every captcha step; create a new client to change it. Your application's separate token-verification request must configure its own route, and a rotating provider can change the exit IP while its URL stays constant.

| Option | Default | Purpose | Packages |
|---|---|---|---|
| proxy | none (direct) | route for every captcha step | <img src="assets/i-python.svg" height="20" align="absmiddle" alt="Python"><img src="assets/i-nodejs.svg" height="20" align="absmiddle" alt="Node.js"><img src="assets/i-go.svg" height="20" align="absmiddle" alt="Go"><img src="assets/i-rust.svg" height="20" align="absmiddle" alt="Rust"> |
| update route | inherits proxy | separate route for update requests; explicit direct is available | <img src="assets/i-python.svg" height="20" align="absmiddle" alt="Python"><img src="assets/i-nodejs.svg" height="20" align="absmiddle" alt="Node.js"><img src="assets/i-go.svg" height="20" align="absmiddle" alt="Go"><img src="assets/i-rust.svg" height="20" align="absmiddle" alt="Rust"> |
| total timeout | 30 s | operation deadline covering all stages | <img src="assets/i-python.svg" height="20" align="absmiddle" alt="Python"><img src="assets/i-nodejs.svg" height="20" align="absmiddle" alt="Node.js"><img src="assets/i-go.svg" height="20" align="absmiddle" alt="Go"><img src="assets/i-rust.svg" height="20" align="absmiddle" alt="Rust"> |
| in-flight cap | 16 | active solves; excess work is rejected (`ResourceLimit`), never queued | <img src="assets/i-nodejs.svg" height="20" align="absmiddle" alt="Node.js"><img src="assets/i-go.svg" height="20" align="absmiddle" alt="Go"><img src="assets/i-rust.svg" height="20" align="absmiddle" alt="Rust"> |
| cache | memory | signed-envelope storage; a path enables the persistent cache with rollback history | <img src="assets/i-python.svg" height="20" align="absmiddle" alt="Python"><img src="assets/i-nodejs.svg" height="20" align="absmiddle" alt="Node.js"><img src="assets/i-go.svg" height="20" align="absmiddle" alt="Go"><img src="assets/i-rust.svg" height="20" align="absmiddle" alt="Rust"> |
| update channel | none | `bundleUrl` + `trustedKeys`; no default remote channel or background loop | <img src="assets/i-python.svg" height="20" align="absmiddle" alt="Python"><img src="assets/i-nodejs.svg" height="20" align="absmiddle" alt="Node.js"><img src="assets/i-go.svg" height="20" align="absmiddle" alt="Go"><img src="assets/i-rust.svg" height="20" align="absmiddle" alt="Rust"> |
| `offline` | off | disables update networking, not captcha networking | <img src="assets/i-python.svg" height="20" align="absmiddle" alt="Python"><img src="assets/i-nodejs.svg" height="20" align="absmiddle" alt="Node.js"><img src="assets/i-go.svg" height="20" align="absmiddle" alt="Go"><img src="assets/i-rust.svg" height="20" align="absmiddle" alt="Rust"> |
| `pin` | none | exact revision; never bypasses signature, expiry or rollback checks | <img src="assets/i-python.svg" height="20" align="absmiddle" alt="Python"><img src="assets/i-nodejs.svg" height="20" align="absmiddle" alt="Node.js"><img src="assets/i-go.svg" height="20" align="absmiddle" alt="Go"><img src="assets/i-rust.svg" height="20" align="absmiddle" alt="Rust"> |

Read [updates](docs/updates.md) before configuring a channel.

## 🔐 Signed data and trust

- 📝 envelopes are Ed25519-signed over the exact payload bytes with a domain-separated prefix (`anchorpass.bundle.v1`); duplicate JSON keys, unknown fields, wrong families, invalid revisions, oversized fields and expired data are rejected
- 🔑 the bundled `production-1` public key verifies the [GitHub data channel](docs/channel-operations.md); its private signer is held in a GitHub Actions secret
- 🚫 the GitHub channel is opt-in: configure its URL explicitly; importing or constructing a client performs no network I/O
- ↩️ an authenticated higher revision becomes the rollback floor — in memory, and on disk when a cache path is configured — before expiry or pin checks run
- 🧱 update requests travel over their own bounded connection pool without captcha cookies, headers or parameters; target and HTTPS-proxy certificates are verified independently
- 🕶️ error messages and diagnostics omit tokens and proxy credentials

## 🔧 Development

```bash
python -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.lock
npm --prefix js ci
make check                 # contract/data integrity + all language gates
make test-package-install  # built artifacts outside the source tree
```

- `make test-benchmark-adapters`, `make test-pressure-adapters` and `make test-live-helpers` check the local harnesses without contacting public CAPTCHA endpoints; [benchmark instructions](benchmarks/README.md) describe the sequential matrix runner
- `make test-live` explicitly contacts the public demo verifiers; its [instructions](tests/live/README.md) explain site selection, proxy routing and negative controls — normal CI does not run external acceptance requests
- `make prepare-release` seals standalone artifacts and a digest manifest; [release operations](docs/release.md) cover publication, channel signing and recovery
- Go archive installation checks need `make check` first to populate the toolchain's dependency cache; the module itself always comes from the archive under test
- each push and pull request runs the [verify workflow](https://github.com/nazavod777/anchorpass/actions/workflows/ci.yml); require a successful run for the exact commit selected for release

## 📚 Documentation

| Path | Start here |
|---|---|
| 🗺️ [Documentation index](docs/README.md) | every document, with reading paths by role |
| 🔐 [Signed protocol data](docs/updates.md) | key custody, channels and expiry policy |
| 🚢 [Release operations](docs/release.md) | exact-byte publication and recovery runbook |
| 🧭 [Implementation status](docs/implementation-status.md) | local evidence vs external acceptance |
| 🧬 [Compatibility](docs/compatibility.md) | verified runtimes and platforms |

The shared [`spec/`](spec/protocol.md) is the source of truth for all four implementations.

## ⚠️ Limitations

- <img src="assets/c-linux.svg" height="20" align="absmiddle" alt="Linux"> verified on x86_64 only; other platforms and architectures remain unqualified ([compatibility matrix](docs/compatibility.md))
- 🔑 embedded data expires; configure the [GitHub channel](docs/channel-operations.md) for renewed signed data, or supply a fresh trusted envelope before expiry
- 🧬 the `enterprise` family has local conformance only; the live record's Enterprise responses carried no action field
- <img src="assets/c-rust.svg" height="20" align="absmiddle" alt="Rust"> async cancellation cannot forcibly stop an already running OS DNS syscall (reqwest uses the system resolver)
- 🎫 a returned token does not establish acceptance or a score; applications must verify it and must not log it indiscriminately
- 📦 `0.1.1` is public on PyPI, npm and crates.io; Go publication remains pending

## ✅ Test status

The [verify workflow](https://github.com/nazavod777/anchorpass/actions/workflows/ci.yml) passed on every job at `edd3797`; require a green run of the exact commit you use:

| 🔍 check | <img src="assets/i-go.svg" height="20" align="absmiddle" alt="Go"> go-minimum | <img src="assets/i-python.svg" height="20" align="absmiddle" alt="Python"> python-minimum | <img src="assets/i-nodejs.svg" height="20" align="absmiddle" alt="Node.js"> node-minimum | <img src="assets/i-rust.svg" height="20" align="absmiddle" alt="Rust"> rust-current |
|:---:|:---:|:---:|:---:|:---:|
| [✅ gates](https://github.com/nazavod777/anchorpass/actions/runs/43952613614/job/133598791929) | [✅ Go 1.23](https://github.com/nazavod777/anchorpass/actions/runs/43952613614/job/133598791730) | [✅ Python 3.11](https://github.com/nazavod777/anchorpass/actions/runs/43952613614/job/133598791979) | [✅ Node 22.0](https://github.com/nazavod777/anchorpass/actions/runs/43952613614/job/133598791171) | [✅ Rust 1.92](https://github.com/nazavod777/anchorpass/actions/runs/43952613614/job/133598791747) |

The live [multi-site record](docs/live-acceptance-multisite.json) contains 28 cases on four public demos plus four invalid-token controls: Google and 2Captcha v3 passed the strict acceptance checks; seven Enterprise cases were accepted without an action field and seven ZennoLab cases returned a quota diagnostic — the matrix deliberately exits nonzero because those limited responses are not clean passes. These observations describe the recorded demos, route and time; they do not establish a stable score or acceptance on arbitrary applications. Local conformance covers both `api2` and `enterprise` for all four languages. Full evidence: [implementation status](docs/implementation-status.md), [transport decisions](docs/transport-decisions.md), [pressure results](docs/pressure-results.md).

## 🗂️ Layout

| | Path | Contents |
|:---:|---|---|
| <img src="assets/c-python.svg" height="20" align="absmiddle" alt="Python"> | `python/` | Python package (PycURL sync, aiohttp async) |
| <img src="assets/c-nodejs.svg" height="20" align="absmiddle" alt="Node.js"> | `js/` | Node.js package (Undici) |
| <img src="assets/c-go.svg" height="20" align="absmiddle" alt="Go"> | `go/` | Go module (net/http) |
| <img src="assets/c-rust.svg" height="20" align="absmiddle" alt="Rust"> | `rust/` | Rust crate (reqwest + Hyper/rustls adapter for HTTPS proxies) |
| 📐 | `spec/` | shared protocol constants and test vectors — the source of truth |
| 📚 | `docs/` | status, updates, transport decisions, release runbook |
| 🎬 | `examples/` | runnable per-language solve scripts |
| 📊 | `benchmarks/` | transport matrix runner and reports |
| 🧪 | `tests/` | shared fixtures and opt-in live checks |
| 🔧 | `Makefile` | check, package-install, benchmark and release gates |

## 🤝 Credits

| Role | Who |
|---|---|
| 👤 **Author & maintainer** | [**nazavod777**](https://github.com/nazavod777) |
| 📖 **Upstream logic** | [**xHossein/PyPasser**](https://github.com/xHossein/PyPasser) (MIT) · [invisible_captcha_bypasser](https://github.com/nazavod777/invisible_captcha_bypasser) (origin) |
| 🤖 **Built with AI tools** | <img src="assets/c-claude.svg" height="20" align="absmiddle" alt="Claude Code"> (Anthropic) · <img src="assets/c-zai.svg" height="20" align="absmiddle" alt="GLM · Z.ai"> models · <img src="assets/c-t3code.svg" height="20" align="absmiddle" alt="T3 Code"> (terminal) |

The AI tools above were used as development assistants — thanks to their teams. Design decisions, review and responsibility for the code stay with the author.

## 📄 License

[MIT](LICENSE) · protocol data and design elements derived from PyPasser — see [NOTICE](NOTICE)

---

<div align="center"><sub>Like the project? Support the author ☕</sub></div>

<div align="center">

<a href="https://nazavod.dev"><img src="assets/site.svg" height="28" alt="nazavod.dev"></a> <a href="https://t.me/bio_nazavod"><img src="assets/telegram.svg" height="28" alt="Telegram"></a> <a href="https://github.com/nazavod777"><img src="assets/github.svg" height="28" alt="GitHub"></a> <a href="https://x.com/nazavod777"><img src="assets/x.svg" height="28" alt="X"></a> <a href="https://antidrain.me"><img src="assets/antidrain.svg" height="28" alt="AntiDrain"></a>

<br><br>

<a href="https://nazavod.dev/coffee/"><img src="assets/coffee.svg" width="300" height="54" alt="Buy me a coffee"></a>

</div>
