//! Bounded reCAPTCHA HTTP protocol client. Token acceptance requires an independent site check.
#[cfg(feature = "blocking")]
pub mod blocking;
#[cfg(any(feature = "async", feature = "blocking"))]
mod bundle;
#[cfg(any(feature = "async", feature = "blocking"))]
mod cache;
#[cfg(any(feature = "async", feature = "blocking"))]
mod client;
#[cfg(any(feature = "async", feature = "blocking"))]
mod cookies;
#[cfg(any(feature = "async", feature = "blocking"))]
mod core;
mod error;
#[cfg(any(feature = "async", feature = "blocking"))]
mod hostname;
mod proxy;
#[cfg(any(feature = "async", feature = "blocking"))]
mod shared;
#[cfg(any(feature = "async", feature = "blocking"))]
mod tls_proxy;
#[cfg(any(feature = "async", feature = "blocking"))]
pub use client::{BundleInfo, SolveOptions, SolveResult};
#[cfg(feature = "async")]
pub use client::{Client, ClientBuilder};
pub use error::{Error, ErrorKind, Result};
pub use proxy::UpdatesProxy;
pub type TrustedKeys = std::collections::BTreeMap<String, String>;

/// Extract `sa` from a validated anchor URL without performing any I/O.
#[cfg(any(feature = "async", feature = "blocking"))]
pub fn extract_action(anchor_url: &str) -> Result<Option<String>> {
    core::extract_action(anchor_url)
}
