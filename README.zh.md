<div align="center">

<img src="assets/hero.svg" width="800" alt="终端：安装 Python 包，solve() 走完 anchor 和 reload，返回令牌"><br><br>

<img src="assets/b-python.svg" height="28" alt="Python"> <img src="assets/b-nodejs.svg" height="28" alt="Node.js"> <img src="assets/b-go.svg" height="28" alt="Go"> <img src="assets/b-rust.svg" height="28" alt="Rust"><br>
<img src="assets/b-api.svg" height="28" alt="sync + async"> <img src="assets/b-proxies.svg" height="28" alt="explicit proxies"> <img src="assets/b-signed.svg" height="28" alt="Ed25519 signed data"> <img src="assets/b-license.svg" height="28" alt="MIT"><br><br>

<a href="README.md">🇬🇧 English</a> · <a href="README.ru.md">🇷🇺 Русский</a> · <a href="README.zh.md">🇨🇳 中文</a>

<br><br>
<a href="https://github.com/nazavod777/anchorpass/actions/workflows/ci.yml"><img src="https://github.com/nazavod777/anchorpass/actions/workflows/ci.yml/badge.svg" alt="CI"></a>

</div>

# ⚓ Anchorpass

> **reCAPTCHA v3 隐形验证码求解器** — 四个原生客户端：<img src="assets/c-python.svg" height="20" align="absmiddle" alt="Python">、<img src="assets/c-nodejs.svg" height="20" align="absmiddle" alt="Node.js">、<img src="assets/c-go.svg" height="20" align="absmiddle" alt="Go"> 和 <img src="assets/c-rust.svg" height="20" align="absmiddle" alt="Rust">，提供同步与异步 API、显式代理路由和签名协议数据。

Anchorpass 通过普通 HTTPS 复现 reCAPTCHA v3 隐形模式的 HTTP 流程 —— `anchor` 与 `reload` 端点。不需要浏览器、webdriver 或指纹栈。协议常量以 Ed25519 签名信封分发，并带过期与回滚检查。逻辑延续了 [xHossein/PyPasser](https://github.com/xHossein/PyPasser) 与作者的 [invisible_captcha_bypasser](https://github.com/nazavod777/invisible_captcha_bypasser)；这里的每个包都是同一份 [`spec/`](spec/protocol.md) 的全新独立实现，而不是对其他语言的封装。

> [!WARNING]
> 自动求解验证码违反 Google 的服务条款。只对你自己的应用、或获得明确授权测试的应用使用。返回的令牌不保证通过或得分 —— 你的应用仍需在服务端校验它。

## ✨ 特性

| 特性 | 含义 |
|---|---|
| 🧩 **四个原生包** | Python、Node.js、Go、Rust 实现同一份规范；彼此之间没有 FFI |
| ⚡ **同步 + 异步** | 每种语言都提供阻塞式与原生异步客户端，行为契约一致 |
| 🔀 **显式代理路由** | `http`、`https`、`socks5`、`socks5h`；忽略环境变量代理，代理失败绝不回退直连 |
| 🔐 **签名协议数据** | Ed25519 签名信封，带过期与回滚下限；默认没有远程更新通道 |
| 🍪 **每次求解独立 cookie** | 每次 solve 拥有自己的 cookie jar，遵循 domain/path/Secure/expiry 规则并有预算上限 |
| 📏 **默认处处受限** | 总超时、连接/空闲上限、响应体限制与并发上限；过载直接拒绝，绝不排队 |
| 🧪 **共享测试向量** | 一份包含协议、代理与失败向量的规范驱动全部四个包；CI 运行完整矩阵 |

## 🎯 解决什么

只解决**一件事** —— 从你集成的应用的 anchor URL 出发，通过普通 HTTP 流程获取 reCAPTCHA v3 隐形令牌：

> `https://www.google.com/recaptcha/api2/anchor?k=<site-key>&co=<encoded-origin>`

| ✅ 解决 | ❌ 不解决 |
|---|---|
| anchor → reload 令牌流程，`api2` 与 `enterprise` 两个家族（本地一致性均已覆盖） | v2 复选框、图片或音频挑战 |
| 严格的 anchor URL 校验、`bg` / `action` 处理、签名数据更新 | Enterprise 的线上资格 —— 记录到的 Enterprise 响应不含 action 字段 |
| 整个验证码流程的显式代理路由 | 保证得分或通过 —— 由观察方站点决定 |
| | 服务端令牌校验 —— 必须由你的应用完成 |

## 🚀 安装

`0.1.1` 已发布到 [<img src="assets/c-pypi.svg" height="20" align="absmiddle" alt="PyPI">](https://pypi.org/project/anchorpass/0.1.1/)、[<img src="assets/c-npm.svg" height="20" align="absmiddle" alt="npm">](https://www.npmjs.com/package/anchorpass/v/0.1.1) 和 [<img src="assets/c-cratesio.svg" height="20" align="absmiddle" alt="crates.io">](https://crates.io/crates/anchorpass/0.1.1)。Go `v0.1.1` 已可通过公开的 <img src="assets/c-goproxy.svg" height="20" align="absmiddle" alt="Go proxy"> 安装。

<table>
<tr><td width="260"><img src="assets/c-python.svg" height="20" align="absmiddle" alt="Python"><br><sub>建议使用虚拟环境</sub></td><td>

```bash
python -m pip install anchorpass==0.1.1
```

</td></tr>
<tr><td width="260"><img src="assets/c-nodejs.svg" height="20" align="absmiddle" alt="Node.js"><br><sub>在应用目录中安装</sub></td><td>

```bash
npm install anchorpass@0.1.1
```

</td></tr>
<tr><td width="260"><img src="assets/c-go.svg" height="20" align="absmiddle" alt="Go"><br><sub>在应用的模块目录中安装</sub></td><td>

```bash
go get github.com/nazavod777/anchorpass/go@v0.1.1
```

</td></tr>
<tr><td width="260"><img src="assets/c-rust.svg" height="20" align="absmiddle" alt="Rust"><br><sub>固定版本；默认 feature 为 `async`</sub></td><td>

```toml
[dependencies]
anchorpass = "=0.1.1"
# 阻塞客户端：加 features = ["blocking"]
```

</td></tr>
</table>

> [!NOTE]
> 四个包均可从公开注册表或 Go 模块代理安装。`make prepare-release` 会封存独立构件（wheel、sdist、npm tarball、crate、Go 归档）并生成摘要清单；[发布操作](docs/release.md) 说明精确字节安装、发布与通道签名。

**安装后得到什么：** 每种语言一个自包含包，签名协议数据内嵌其中 → 导入或构造客户端**不产生任何网络请求** → 没有 post-install 脚本，没有后台循环。

### 📋 环境要求

- <img src="assets/c-python.svg" height="20" align="absmiddle" alt="Python"> **3.11+**（已在 3.11.18 与 3.13.15 上验证）
- <img src="assets/c-nodejs.svg" height="20" align="absmiddle" alt="Node.js"> **22**（已在 22.0.0 与 22.22.1 上验证）
- <img src="assets/c-go.svg" height="20" align="absmiddle" alt="Go"> **1.23+**（已在 1.23.0 与 1.25.5 上验证）
- <img src="assets/c-rust.svg" height="20" align="absmiddle" alt="Rust"> **1.92+**（已在 1.92.0 与 1.96 工具链上验证）
- <img src="assets/c-linux.svg" height="20" align="absmiddle" alt="Linux"> x86_64 是已验证平台；其他操作系统与架构未验证
- 🌐 不需要浏览器、显示器或系统服务；选定的传输层为 Python PycURL（同步）和 aiohttp（异步）、Node.js Undici、Go `net/http`、Rust reqwest

## ⚙️ 工作原理

```mermaid
flowchart LR
    A([👤 你的应用]) --> C{{"🧩 anchorpass 客户端"}}
    C -->|"一条显式路由<br/>http · https · socks5 · socks5h"| P["🛰️ 代理 — 可选"]
    P --> V["🔎 当前 api.js 版本"]
    V --> N["⚓ anchor GET"]
    N --> R["🔁 reload POST"]
    R --> T["🎫 令牌"]
    T --> A
```

- 每次 solve 在任何网络 I/O 之前严格校验 anchor URL（HTTPS、`google.com`、`api2/anchor` 或 `enterprise/anchor`、非空 `k` 与 `co`）
- 每次求解都重新获取当前协议版本，即使 URL 已带 `v=`
- 每次 solve 拥有隔离的 cookie jar；连接池复用，但 cookie 不会在求解之间、也不会流入更新请求
- `bg` 来自已验证的签名数据包；action 优先级为显式参数 → URL 的 `sa` → 省略 —— 库绝不臆造 site action
- 重定向一律拒绝；代理失败是错误，绝不是直连重试

## 🧩 用法

使用你集成的应用的真实 anchor URL —— 占位符形态不是可运行的站点配置：

> `anchor_url = "https://www.google.com/recaptcha/api2/anchor?k=site-key&co=encoded-origin"`

每种语言暴露同一契约：

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

// blocking：只用普通线程，绝不在 Tokio runtime 内
let token = anchorpass::blocking::Client::builder()
    .build()?
    .solve(&anchor)?
    .token;
```

</td></tr>
</table>

`extract_action` / `extractAction` / `ExtractAction` 不做任何 I/O 即可解析 URL 的 `sa`；`load_bg` / `loadBg` / `LoadBG` / `load_bg` 不发起验证码请求即可返回已验证数据包的 `bg`。从命令行参数读取 anchor URL 与可选代理的完整可运行脚本见 [examples/](examples/README.md)。

一次性辅助函数自建并关闭客户端。复用客户端即可复用连接。各语言细节：

| 包 | 客户端 | 一次性辅助 | 语言说明 |
| --- | --- | --- | --- |
| <img src="assets/c-python.svg" height="20" align="absmiddle" alt="Python"> | `Client`、`AsyncClient` | `solve`、`asolve`、`load_bg`、`aload_bg` | [python/README.md](python/README.md) |
| <img src="assets/c-nodejs.svg" height="20" align="absmiddle" alt="Node.js"> | `Client`、`SyncClient` | `solve`、`solveSync`、`loadBg`、`loadBgSync` | [js/README.md](js/README.md) |
| <img src="assets/c-go.svg" height="20" align="absmiddle" alt="Go"> | `NewClient(options...)` | 顶层 `Solve`、`LoadBG` | [go/README.md](go/README.md) |
| <img src="assets/c-rust.svg" height="20" align="absmiddle" alt="Rust"> | `Client::builder()`、`blocking::Client` | 使用客户端方法 | [rust/README.md](rust/README.md) |

按调用字段 —— `bg`、`action`、User-Agent 和 `hostname` 是单次 solve 的可选参数：

| 包 | 数据包 `bg` 辅助 | URL action 辅助 | 按调用覆盖 |
| --- | --- | --- | --- |
| <img src="assets/c-python.svg" height="20" align="absmiddle" alt="Python"> | `load_bg`、`aload_bg`、客户端 `load_bg` | `extract_action` | `solve(..., bg=..., action=..., user_agent=..., hostname=...)` |
| <img src="assets/c-nodejs.svg" height="20" align="absmiddle" alt="Node.js"> | `loadBg`、`loadBgSync`、对应客户端方法 | `extractAction` | `solve`/`solveSync` 选项 `{ bg, action, userAgent, hostname }` |
| <img src="assets/c-go.svg" height="20" align="absmiddle" alt="Go"> | `LoadBG(ctx, options...)`、`Client.LoadBG(ctx)` | `ExtractAction` | `Solve` 上的 `WithBG`、`WithAction` |
| <img src="assets/c-rust.svg" height="20" align="absmiddle" alt="Rust"> | 客户端 `load_bg` | `extract_action` | `solve_with_options` 配 `SolveOptions` |

显式 action 优先于 anchor URL 的 `sa`；否则保留 `sa`；两者都无则省略。选定的 action 同时发给 anchor 与 reload。覆盖只替换那一次调用的外发字段 —— 绝不改动数据包、缓存或后续调用。


`user_agent`（Python/Rust）、`userAgent`（Node.js）或 `WithUserAgent`（Go）为 version、anchor 和 reload 设置同一个请求头，不影响更新请求或后续调用。`hostname` / `WithHostname` 仅替换 `co` 中的应用主机，保留协议和端口；接受 ASCII 域名或 IP，不接受 URL 或端口，不改变 Google 的站点密钥域名校验。省略时保留原有请求头和 `co`。详见 [API 契约](spec/api.md)。

## 🔀 路由与更新

`proxy` 接受带显式主机与端口的绝对 URL：`http`、`https`、`socks5` 或 `socks5h`。`socks5` 在本地解析目标；`socks5h` 在代理端解析。忽略环境代理变量。一个客户端对全部验证码步骤保持一条路由；换路由就新建客户端。你的应用里独立的令牌校验请求必须自行配置路由；旋转代理供应商可能在 URL 不变时更换出口 IP。

| 选项 | 默认 | 用途 | 包 |
|---|---|---|---|
| proxy | 无（直连） | 全部验证码步骤的路由 | <img src="assets/i-python.svg" height="20" align="absmiddle" alt="Python"><img src="assets/i-nodejs.svg" height="20" align="absmiddle" alt="Node.js"><img src="assets/i-go.svg" height="20" align="absmiddle" alt="Go"><img src="assets/i-rust.svg" height="20" align="absmiddle" alt="Rust"> |
| 更新路由 | 继承 proxy | 更新请求的独立路由；可显式直连 | <img src="assets/i-python.svg" height="20" align="absmiddle" alt="Python"><img src="assets/i-nodejs.svg" height="20" align="absmiddle" alt="Node.js"><img src="assets/i-go.svg" height="20" align="absmiddle" alt="Go"><img src="assets/i-rust.svg" height="20" align="absmiddle" alt="Rust"> |
| 总超时 | 30 秒 | 覆盖全部阶段的操作截止时间 | <img src="assets/i-python.svg" height="20" align="absmiddle" alt="Python"><img src="assets/i-nodejs.svg" height="20" align="absmiddle" alt="Node.js"><img src="assets/i-go.svg" height="20" align="absmiddle" alt="Go"><img src="assets/i-rust.svg" height="20" align="absmiddle" alt="Rust"> |
| 并发上限 | 16 | 活跃 solve；超量直接拒绝（`ResourceLimit`），不排队 | <img src="assets/i-nodejs.svg" height="20" align="absmiddle" alt="Node.js"><img src="assets/i-go.svg" height="20" align="absmiddle" alt="Go"><img src="assets/i-rust.svg" height="20" align="absmiddle" alt="Rust"> |
| 缓存 | 内存 | 签名信封存储；配置路径启用带回滚历史的持久缓存 | <img src="assets/i-python.svg" height="20" align="absmiddle" alt="Python"><img src="assets/i-nodejs.svg" height="20" align="absmiddle" alt="Node.js"><img src="assets/i-go.svg" height="20" align="absmiddle" alt="Go"><img src="assets/i-rust.svg" height="20" align="absmiddle" alt="Rust"> |
| 更新通道 | 无 | `bundleUrl` + `trustedKeys`；默认无远程通道、无后台循环 | <img src="assets/i-python.svg" height="20" align="absmiddle" alt="Python"><img src="assets/i-nodejs.svg" height="20" align="absmiddle" alt="Node.js"><img src="assets/i-go.svg" height="20" align="absmiddle" alt="Go"><img src="assets/i-rust.svg" height="20" align="absmiddle" alt="Rust"> |
| `offline` | 关 | 禁用更新网络，不禁用验证码网络 | <img src="assets/i-python.svg" height="20" align="absmiddle" alt="Python"><img src="assets/i-nodejs.svg" height="20" align="absmiddle" alt="Node.js"><img src="assets/i-go.svg" height="20" align="absmiddle" alt="Go"><img src="assets/i-rust.svg" height="20" align="absmiddle" alt="Rust"> |
| `pin` | 无 | 精确版本；绝不绕过签名、过期或回滚检查 | <img src="assets/i-python.svg" height="20" align="absmiddle" alt="Python"><img src="assets/i-nodejs.svg" height="20" align="absmiddle" alt="Node.js"><img src="assets/i-go.svg" height="20" align="absmiddle" alt="Go"><img src="assets/i-rust.svg" height="20" align="absmiddle" alt="Rust"> |

配置通道前请先阅读 [updates](docs/updates.md)。

## 🔐 签名数据与信任

- 📝 信封以 Ed25519 对精确 payload 字节签名，带域分隔前缀（`anchorpass.bundle.v1`）；重复 JSON 键、未知字段、错误家族、非法版本、超限字段与过期数据一律拒绝
- 🔑 内置的 `production-1` 公钥验证 [GitHub 数据通道](docs/channel-operations.md)；私钥保存在 GitHub Actions secret 中
- 🚫 GitHub 通道需要显式配置 URL；导入或构造客户端不执行网络 I/O
- ↩️ 通过验证的更高版本成为回滚下限 —— 在内存中，配置缓存路径后也在磁盘上 —— 且先于过期与 pin 检查生效
- 🧱 更新请求走自己受限的连接池，不携带验证码 cookie、头或参数；目标与 HTTPS 代理的证书独立校验
- 🕶️ 错误信息与诊断不含令牌和代理凭据

## 🔧 开发

```bash
python -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.lock
npm --prefix js ci
make check                 # 契约/数据完整性 + 全部语言门禁
make test-package-install  # 在源码树之外检查构建产物
```

- `make test-benchmark-adapters`、`make test-pressure-adapters` 与 `make test-live-helpers` 检查本地测试架，不接触公共验证码端点；[基准测试说明](benchmarks/README.md) 描述顺序矩阵运行器
- `make test-live` 显式访问公共演示校验器；其[说明](tests/live/README.md)解释站点选择、代理路由与阴性对照 —— 常规 CI 不执行外部验收请求
- `make prepare-release` 封存独立构件与摘要清单；[发布操作](docs/release.md) 覆盖发布、通道签名与恢复
- Go 归档安装检查需要先跑 `make check` 填充工具链依赖缓存；模块本身始终来自受测归档
- 每次 push 与 pull request 都会运行 [verify workflow](https://github.com/nazavod777/anchorpass/actions/workflows/ci.yml)；为选定发布的精确提交要求一次成功运行

## 📚 文档

| 路径 | 从这里开始 |
|---|---|
| 🗺️ [文档索引](docs/README.md) | 全部文档，附按角色的阅读路径 |
| 🔐 [签名协议数据](docs/updates.md) | 密钥保管、通道与过期策略 |
| 🚢 [发布操作](docs/release.md) | 精确字节发布与恢复手册 |
| 🧭 [实现状态](docs/implementation-status.md) | 本地证据与外部验收的区分 |
| 🧬 [兼容性](docs/compatibility.md) | 已验证的运行时与平台 |

共享的 [`spec/`](spec/protocol.md) 是全部四个实现的唯一事实来源。

## ⚠️ 局限

- <img src="assets/c-linux.svg" height="20" align="absmiddle" alt="Linux"> 仅在 x86_64 上验证；其他平台与架构未验证（[兼容矩阵](docs/compatibility.md)）
- 🔑 内嵌数据会过期；请配置 [GitHub 通道](docs/channel-operations.md) 接收续期数据，或在过期前提供新的可信信封
- 🧬 `enterprise` 家族只有本地一致性；线上记录中的 Enterprise 响应不含 action
- <img src="assets/c-rust.svg" height="20" align="absmiddle" alt="Rust"> 异步取消无法强制中止已在运行的系统 DNS 调用（reqwest 使用系统解析器）
- 🎫 返回的令牌不代表通过或得分；应用必须校验它，也不应无差别记录它
- 📦 `0.1.1` 已发布到 PyPI、npm 和 crates.io；Go `v0.1.1` 已可通过公开模块代理安装

## ✅ 测试状态

[verify workflow](https://github.com/nazavod777/anchorpass/actions/workflows/ci.yml) 在 `edd3797` 上全部任务通过；请为你要使用的提交要求一次绿色运行：

| 🔍 check | <img src="assets/i-go.svg" height="20" align="absmiddle" alt="Go"> go-minimum | <img src="assets/i-python.svg" height="20" align="absmiddle" alt="Python"> python-minimum | <img src="assets/i-nodejs.svg" height="20" align="absmiddle" alt="Node.js"> node-minimum | <img src="assets/i-rust.svg" height="20" align="absmiddle" alt="Rust"> rust-current |
|:---:|:---:|:---:|:---:|:---:|
| [✅ 门禁](https://github.com/nazavod777/anchorpass/actions/runs/43952613614/job/133598791929) | [✅ Go 1.23](https://github.com/nazavod777/anchorpass/actions/runs/43952613614/job/133598791730) | [✅ Python 3.11](https://github.com/nazavod777/anchorpass/actions/runs/43952613614/job/133598791979) | [✅ Node 22.0](https://github.com/nazavod777/anchorpass/actions/runs/43952613614/job/133598791171) | [✅ Rust 1.92](https://github.com/nazavod777/anchorpass/actions/runs/43952613614/job/133598791747) |

线上[多站点记录](docs/live-acceptance-multisite.json)包含四个公共演示上的 28 个用例和四个无效令牌对照：Google 与 2Captcha v3 通过了严格验收；七个 Enterprise 用例在缺少 action 字段的情况下被接受、七个 ZennoLab 用例返回配额诊断 —— 矩阵故意以非零码退出，因为这些受限响应不算干净通过。这些观察只描述记录时的演示、路由与时间，不确立任意应用上的稳定得分或通过。本地一致性覆盖全部四种语言的 `api2` 与 `enterprise`。完整证据：[implementation status](docs/implementation-status.md)、[transport decisions](docs/transport-decisions.md)、[pressure results](docs/pressure-results.md)。

## 🗂️ 目录结构

| | 路径 | 内容 |
|:---:|---|---|
| <img src="assets/c-python.svg" height="20" align="absmiddle" alt="Python"> | `python/` | Python 包（PycURL 同步，aiohttp 异步） |
| <img src="assets/c-nodejs.svg" height="20" align="absmiddle" alt="Node.js"> | `js/` | Node.js 包（Undici） |
| <img src="assets/c-go.svg" height="20" align="absmiddle" alt="Go"> | `go/` | Go 模块（net/http） |
| <img src="assets/c-rust.svg" height="20" align="absmiddle" alt="Rust"> | `rust/` | Rust crate（reqwest + HTTPS 代理的 Hyper/rustls 适配器） |
| 📐 | `spec/` | 共享协议常量与测试向量 —— 事实来源 |
| 📚 | `docs/` | 状态、更新、传输决策、发布手册 |
| 🎬 | `examples/` | 各语言可运行的求解脚本 |
| 📊 | `benchmarks/` | 传输矩阵运行器与报告 |
| 🧪 | `tests/` | 共享夹具与可选线上检查 |
| 🔧 | `Makefile` | check、package-install、基准与发布门禁 |

## 🤝 致谢

| 角色 | 谁 |
|---|---|
| 👤 **作者与维护者** | [**nazavod777**](https://github.com/nazavod777) |
| 📖 **上游逻辑** | [**xHossein/PyPasser**](https://github.com/xHossein/PyPasser)（MIT）· [invisible_captcha_bypasser](https://github.com/nazavod777/invisible_captcha_bypasser)（起点） |
| 🤖 **借助 AI 工具构建** | <img src="assets/c-claude.svg" height="20" align="absmiddle" alt="Claude Code">（Anthropic）· <img src="assets/c-zai.svg" height="20" align="absmiddle" alt="GLM · Z.ai"> 模型 · <img src="assets/c-t3code.svg" height="20" align="absmiddle" alt="T3 Code">（终端） |

上述 AI 工具仅作为开发助手使用 —— 感谢它们的团队。设计决策、评审与代码责任归于作者。

## 📄 许可证

[MIT](LICENSE) · 协议数据与设计元素源自 PyPasser —— 见 [NOTICE](NOTICE)

---

<div align="center"><sub>喜欢这个项目？请作者喝杯咖啡 ☕</sub></div>

<div align="center">

<a href="https://nazavod.dev"><img src="assets/site.svg" height="28" alt="nazavod.dev"></a> <a href="https://t.me/bio_nazavod"><img src="assets/telegram.svg" height="28" alt="Telegram"></a> <a href="https://github.com/nazavod777"><img src="assets/github.svg" height="28" alt="GitHub"></a> <a href="https://x.com/nazavod777"><img src="assets/x.svg" height="28" alt="X"></a> <a href="https://antidrain.me"><img src="assets/antidrain.svg" height="28" alt="AntiDrain"></a>

<br><br>

<a href="https://nazavod.dev/coffee/"><img src="assets/coffee.svg" width="300" height="54" alt="Buy me a coffee"></a>

</div>
