# 🧭 Examples

> **Runnable per-language scripts** — each solves one anchor URL and prints the token prefix.

They read the anchor URL of the application you integrate — and an
optional explicit proxy — from the command line; nothing is hardcoded.

> [!WARNING]
> Automated CAPTCHA solving violates Google's Terms of Service. Run these only
> against your own applications or ones you are explicitly authorized to test.
> A returned token does not guarantee acceptance or a score — verify it
> server-side in your application.

## 🗂️ Scripts

| Language | File | How to run |
|---|---|---|
| <img src="../assets/c-python.svg" height="20" align="absmiddle" alt="Python"> | [python_solve.py](python_solve.py) | `.venv/bin/python examples/python_solve.py ANCHOR_URL [PROXY_URL]` after installing the package from `python/` |
| <img src="../assets/c-nodejs.svg" height="20" align="absmiddle" alt="Node.js"> | [node_solve.mjs](node_solve.mjs) | `node node_solve.mjs ANCHOR_URL [PROXY_URL]` from an application where the `anchorpass` package is installed |
| <img src="../assets/c-go.svg" height="20" align="absmiddle" alt="Go"> | [go_solve.go](go_solve.go) | copy into your module with the anchorpass dependency (local `replace` until publication), then `go run go_solve.go ANCHOR_URL [PROXY_URL]` |
| <img src="../assets/c-rust.svg" height="20" align="absmiddle" alt="Rust"> | [rust_solve.rs](rust_solve.rs) | copy into your crate with the `anchorpass` dependency, then `cargo run --release -- ANCHOR_URL [PROXY_URL]` |

## 📋 Notes

- `PROXY_URL` is any absolute `http`, `https`, `socks5` or `socks5h` URL with
  an explicit host and port; omit it for a direct route. Environment proxy
  variables are ignored, and a failed proxy is an error, never a direct retry.
- The action defaults to the anchor URL's `sa`; pass an explicit action to
  override it (`solve(..., action=...)` / `{ action }` / `WithAction` /
  `solve_with_action`).
- Rust: the async client needs a Tokio runtime (`tokio` with the `rt` and
  `time` features); the blocking client must run on plain threads outside a
  Tokio runtime — `rust_solve.rs` demonstrates both.
- Each script prints only a token prefix. Do not log full tokens.

Package installation commands live in the [repository README](../README.md).
