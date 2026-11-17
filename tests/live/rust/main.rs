//! One explicit public-API operation; the parent consumes the token without logging it.
use serde::Deserialize;
use serde_json::json;
use std::{
    io::{Read, Write},
    time::Duration,
};

#[derive(Deserialize)]
struct Input {
    mode: String,
    anchor: String,
    action: String,
    proxy: Option<String>,
    timeout_ms: u64,
}

fn run(input: Input) -> anchorpass::Result<anchorpass::SolveResult> {
    let timeout = Duration::from_millis(input.timeout_ms);
    if input.mode == "rust-blocking" {
        let mut builder = anchorpass::blocking::Client::builder().timeout(timeout);
        if let Some(proxy) = input.proxy {
            builder = builder.proxy(proxy);
        }
        builder
            .build()?
            .solve_with_action(&input.anchor, &input.action)
    } else {
        let mut builder = anchorpass::Client::builder().timeout(timeout);
        if let Some(proxy) = input.proxy {
            builder = builder.proxy(proxy);
        }
        let runtime = tokio::runtime::Builder::new_current_thread()
            .enable_all()
            .build()
            .expect("live-check runtime");
        runtime.block_on(async {
            builder
                .build()?
                .solve_with_action(&input.anchor, &input.action)
                .await
        })
    }
}

fn main() {
    let mut raw = String::new();
    if std::io::stdin()
        .take(65537)
        .read_to_string(&mut raw)
        .is_err()
        || raw.len() > 65536
    {
        std::process::exit(2);
    }
    let input: Input = match serde_json::from_str(&raw) {
        Ok(input) => input,
        Err(_) => std::process::exit(2),
    };
    if input.timeout_ms == 0 || input.timeout_ms > 60000 {
        std::process::exit(2);
    }
    let (output, status) = match run(input) {
        Ok(result) => (
            json!({"token": result.token, "bundle_revision": result.bundle_revision}),
            0,
        ),
        Err(error) => (
            json!({"error": format!("{:?}", error.kind), "stage": error.stage}),
            1,
        ),
    };
    println!("{output}");
    let _ = std::io::stdout().flush();
    std::process::exit(status);
}
