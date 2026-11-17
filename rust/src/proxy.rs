#[cfg(any(feature = "async", feature = "blocking"))]
use crate::{core::valid_percent, Error, ErrorKind, Result};
#[cfg(any(feature = "async", feature = "blocking"))]
use percent_encoding::percent_decode_str;
#[cfg(any(feature = "async", feature = "blocking"))]
use url::Url;

#[derive(Clone, Default)]
pub enum UpdatesProxy {
    #[default]
    Inherit,
    Direct,
    Proxy(String),
}
impl std::fmt::Debug for UpdatesProxy {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(match self {
            Self::Inherit => "Inherit",
            Self::Direct => "Direct",
            Self::Proxy(_) => "Proxy([redacted])",
        })
    }
}
#[cfg(any(feature = "async", feature = "blocking"))]
pub(crate) fn parse(input: &str) -> Result<reqwest::Proxy> {
    let invalid = || Error::new(ErrorKind::InvalidProxy, "proxy", "invalid proxy URL");
    if input.len() > 8192
        || input.is_empty()
        || input.chars().any(|c| c.is_whitespace() || c.is_control())
        || !valid_percent(input)
    {
        return Err(invalid());
    }
    let raw_authority = input.split_once("://").ok_or_else(invalid)?.1;
    if raw_authority.contains('\\')
        || raw_authority
            .find('/')
            .is_some_and(|index| &raw_authority[index..] != "/")
    {
        return Err(invalid());
    }
    let url = Url::parse(input).map_err(|_| invalid())?;
    if !matches!(url.scheme(), "http" | "https" | "socks5" | "socks5h")
        || url.host_str().is_none()
        || url.query().is_some()
        || url.fragment().is_some()
        || !matches!(url.path(), "" | "/")
    {
        return Err(invalid());
    }
    let authority = input
        .split_once("://")
        .ok_or_else(invalid)?
        .1
        .split('/')
        .next()
        .ok_or_else(invalid)?;
    let host_port = authority.rsplit('@').next().ok_or_else(invalid)?;
    let (_, port) = host_port.rsplit_once(':').ok_or_else(invalid)?;
    if port.is_empty()
        || !port.bytes().all(|b| b.is_ascii_digit())
        || port.parse::<u16>().ok().filter(|p| *p > 0).is_none()
    {
        return Err(invalid());
    }
    let decode = |value: &str| -> Result<String> {
        let value = percent_decode_str(value)
            .decode_utf8()
            .map_err(|_| invalid())?
            .into_owned();
        if !value.is_ascii() || value.bytes().any(|b| b.is_ascii_control()) {
            return Err(invalid());
        }
        Ok(value)
    };
    let username = decode(url.username())?;
    let password = decode(url.password().unwrap_or(""))?;
    if matches!(url.scheme(), "socks5" | "socks5h")
        && (username.len() > 255 || password.len() > 255)
    {
        return Err(invalid());
    }
    // Validation decodes credentials once; the transport receives the original encoded URL
    // and performs its own single decode. Rebuilding Basic auth would decode literal % twice.
    reqwest::Proxy::all(url).map_err(|_| invalid())
}
