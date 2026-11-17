//! Solve a reCAPTCHA v3 anchor URL with the anchorpass Rust clients.
//!
//! Copy this file into your application crate (with the `anchorpass`
//! dependency), then:
//!
//! ```text
//! cargo run --release -- ANCHOR_URL [PROXY_URL]
//! ```
//!
//! PROXY_URL is any absolute http/https/socks5/socks5h URL with an explicit
//! host and port; omit it for a direct route. The action defaults to the
//! URL's `sa`. The async client needs a Tokio runtime (`tokio` with `rt`
//! and `time` features); the blocking client must run on plain threads
//! outside a Tokio runtime.

fn main() {
    let mut args = std::env::args().skip(1);
    let anchor = args
        .next()
        .expect("usage: cargo run --release -- ANCHOR_URL [PROXY_URL]");
    let proxy = args.next();

    let async_token = tokio_run(anchor.clone(), proxy.clone());
    println!("async    token: {}…", truncate(&async_token, 16));

    // The blocking client must not run inside a Tokio runtime: use a thread.
    let blocking_token = std::thread::spawn(move || blocking_solve(&anchor, proxy))
        .join()
        .expect("blocking thread panicked");
    println!("blocking token: {}…", truncate(&blocking_token, 16));
    println!("verify the token server-side in your application");
}

/// A minimal Tokio runtime; real applications usually use `#[tokio::main]`.
fn tokio_run(anchor: String, proxy: Option<String>) -> String {
    tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()
        .expect("tokio runtime")
        .block_on(async move {
            let mut builder = anchorpass::Client::builder();
            if let Some(proxy) = proxy.as_deref() {
                builder = builder.proxy(proxy);
            }
            let client = builder.build()?;
            let result = client.solve(&anchor).await?;
            Ok::<_, anchorpass::Error>(result.token)
        })
        .expect("async solve failed")
}

fn blocking_solve(anchor: &str, proxy: Option<String>) -> String {
    let mut builder = anchorpass::blocking::Client::builder();
    if let Some(proxy) = proxy.as_deref() {
        builder = builder.proxy(proxy);
    }
    let client = builder.build().expect("blocking client");
    let result = client.solve(anchor).expect("blocking solve");
    result.token
}

fn truncate(token: &str, n: usize) -> &str {
    &token[..n.min(token.len())]
}
