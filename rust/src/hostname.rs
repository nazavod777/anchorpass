use crate::{Error, ErrorKind, Result};
use base64::{
    engine::general_purpose::{STANDARD, STANDARD_NO_PAD, URL_SAFE_NO_PAD},
    Engine,
};
use std::net::IpAddr;
use url::Url;

fn invalid() -> Error {
    Error::new(
        ErrorKind::InvalidInput,
        "overrides",
        "invalid hostname or encoded origin",
    )
}

pub(crate) fn normalize(value: &str) -> Result<String> {
    if value.is_empty()
        || value.len() > 253
        || !value.bytes().all(|byte| (0x21..=0x7e).contains(&byte))
    {
        return Err(invalid());
    }
    let ip = value
        .strip_prefix('[')
        .and_then(|inner| inner.strip_suffix(']'))
        .unwrap_or(value);
    if let Ok(ip) = ip.parse::<IpAddr>() {
        if value.starts_with('[') && ip.is_ipv4() {
            return Err(invalid());
        }
        if let IpAddr::V6(ip) = ip {
            if ip.to_ipv4_mapped().is_some() {
                let segments = ip.segments();
                return Ok(format!("::ffff:{:x}:{:x}", segments[6], segments[7]));
            }
        }
        return Ok(ip.to_string());
    }
    if value.contains(['[', ']', ':', '/', '?', '#', '@', '%', '\\']) {
        return Err(invalid());
    }
    for label in value.strip_suffix('.').unwrap_or(value).split('.') {
        if label.is_empty()
            || label.len() > 63
            || label.starts_with('-')
            || label.ends_with('-')
            || !label
                .bytes()
                .all(|byte| byte.is_ascii_alphanumeric() || byte == b'-')
        {
            return Err(invalid());
        }
    }
    Ok(value.to_ascii_lowercase())
}

pub(crate) fn origin(encoded: &str, hostname: &str) -> Result<String> {
    let hostname = normalize(hostname)?;
    let bytes = decode_origin_base64(encoded)?;
    let text = std::str::from_utf8(&bytes).map_err(|_| invalid())?;
    if text.contains(['?', '#', '\\']) || text.chars().any(|c| c.is_whitespace() || c.is_control())
    {
        return Err(invalid());
    }
    let (scheme, rest) = text.split_once("://").ok_or_else(invalid)?;
    if !matches!(scheme, "http" | "https") {
        return Err(invalid());
    }
    let (authority, path) = rest
        .split_once('/')
        .map_or((rest, ""), |(authority, path)| (authority, path));
    if !path.is_empty() || authority.contains(['@', '%']) || authority.ends_with(':') {
        return Err(invalid());
    }
    let url = Url::parse(text).map_err(|_| invalid())?;
    normalize(url.host_str().ok_or_else(invalid)?)?;
    let port = url
        .port_or_known_default()
        .filter(|port| *port != 0)
        .ok_or_else(invalid)?;
    let host = if hostname.contains(':') {
        format!("[{hostname}]")
    } else {
        hostname
    };
    Ok(STANDARD
        .encode(format!("{scheme}://{host}:{port}"))
        .replace('=', "."))
}

fn decode_origin_base64(encoded: &str) -> Result<Vec<u8>> {
    let raw = encoded.trim_end_matches(['=', '.']);
    let padding = &encoded[raw.len()..];
    if !padding.is_empty()
        && (padding.len() > 2
            || !encoded.len().is_multiple_of(4)
            || !padding
                .bytes()
                .all(|byte| Some(&byte) == padding.as_bytes().first()))
    {
        return Err(invalid());
    }
    for engine in [&STANDARD_NO_PAD, &URL_SAFE_NO_PAD] {
        if let Ok(bytes) = engine.decode(raw) {
            if engine.encode(&bytes) == raw {
                return Ok(bytes);
            }
        }
    }
    Err(invalid())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn shared_solve_option_vectors() {
        let vectors: serde_json::Value =
            serde_json::from_str(include_str!("../tests/data/solve-options.json")).unwrap();
        for value in vectors["hostname"].as_array().unwrap() {
            let result = origin(
                value["co"].as_str().unwrap(),
                value["hostname"].as_str().unwrap(),
            );
            assert_eq!(
                result.is_ok(),
                value["valid"].as_bool().unwrap(),
                "{value}: {result:?}"
            );
            if let Ok(result) = result {
                assert_eq!(result, value["expected_co"].as_str().unwrap());
            }
        }
    }
}
