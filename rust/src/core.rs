use crate::{Error, ErrorKind, Result};
use regex::Regex;
use std::{collections::HashSet, sync::LazyLock};
use url::Url;

pub(crate) struct Anchor {
    pub url: Url,
    pub variant: String,
    pub key: String,
    pub co: String,
}
fn invalid() -> Error {
    Error::new(ErrorKind::InvalidInput, "anchor", "invalid anchor URL")
}
pub(crate) fn parse_anchor(input: &str, action: Option<&str>) -> Result<Anchor> {
    parse_anchor_with_selection(input, action, true)
}
pub(crate) fn extract_action(input: &str) -> Result<Option<String>> {
    let anchor = parse_anchor_with_selection(input, None, false)?;
    Ok(anchor
        .url
        .query_pairs()
        .find(|(key, _)| key == "sa")
        .map(|(_, value)| value.into_owned()))
}
fn parse_anchor_with_selection(
    input: &str,
    action: Option<&str>,
    select_action: bool,
) -> Result<Anchor> {
    if input.len() > 16384
        || input.chars().any(|c| c.is_whitespace() || c.is_control())
        || !valid_percent(input)
        || percent_encoding::percent_decode_str(input)
            .decode_utf8()
            .is_err()
    {
        return Err(invalid());
    }
    if input.split_once("://").is_some_and(|(_, rest)| {
        rest.split(['/', '?', '#'])
            .next()
            .is_some_and(|authority| authority.contains('@'))
    }) {
        return Err(invalid());
    }
    let raw_path = input
        .split_once("://")
        .and_then(|(_, rest)| rest.find('/').map(|index| &rest[index..]))
        .and_then(|path| path.split(['?', '#']).next());
    if input.contains('\\')
        || !matches!(
            raw_path,
            Some("/recaptcha/api2/anchor" | "/recaptcha/enterprise/anchor")
        )
    {
        return Err(invalid());
    }
    let mut url = Url::parse(input).map_err(|_| invalid())?;
    if url.scheme() != "https"
        || !matches!(url.host_str(), Some("www.google.com" | "google.com"))
        || url.port().is_some()
        || !url.username().is_empty()
        || url.password().is_some()
        || url.fragment().is_some()
    {
        return Err(invalid());
    }
    let variant = match url.path() {
        "/recaptcha/api2/anchor" => "api2",
        "/recaptcha/enterprise/anchor" => "enterprise",
        _ => return Err(invalid()),
    }
    .to_owned();
    let mut seen = HashSet::new();
    let mut pairs = Vec::new();
    for (key, value) in url.query_pairs() {
        if !seen.insert(key.to_string()) || value.len() > 8192 {
            return Err(invalid());
        }
        pairs.push((key.to_string(), value.to_string()));
    }
    let get = |key: &str| pairs.iter().find(|(k, _)| k == key).map(|(_, v)| v.clone());
    let key = get("k").filter(|v| !v.is_empty()).ok_or_else(invalid)?;
    let co = get("co").filter(|v| !v.is_empty()).ok_or_else(invalid)?;
    if let Some(action) = action {
        if action.is_empty() || action.len() > 256 {
            return Err(invalid());
        }
        pairs.retain(|(k, _)| k != "sa");
        pairs.push(("sa".to_string(), action.to_string()));
    }
    if select_action
        && pairs
            .iter()
            .any(|(key, value)| key == "sa" && (value.is_empty() || value.len() > 256))
    {
        return Err(invalid());
    }
    url.query_pairs_mut().clear().extend_pairs(pairs);
    Ok(Anchor {
        url,
        variant,
        key,
        co,
    })
}
pub(crate) fn valid_percent(value: &str) -> bool {
    let bytes = value.as_bytes();
    let mut i = 0;
    while i < bytes.len() {
        if bytes[i] == b'%' {
            if i + 2 >= bytes.len()
                || !bytes[i + 1].is_ascii_hexdigit()
                || !bytes[i + 2].is_ascii_hexdigit()
            {
                return false;
            }
            i += 3;
        } else {
            i += 1;
        }
    }
    true
}
pub(crate) fn version(body: &str) -> Result<String> {
    static RE: LazyLock<Regex> = LazyLock::new(|| {
        Regex::new(r"/recaptcha/releases/([A-Za-z0-9_-]{1,128})/").expect("constant regex")
    });
    RE.captures(body).map(|c| c[1].to_owned()).ok_or_else(|| {
        Error::new(
            ErrorKind::ProtocolError,
            "version",
            "version missing from api.js",
        )
    })
}
pub(crate) fn challenge(body: &str) -> Result<String> {
    use html5ever::tokenizer::{
        states::RawKind, BufferQueue, StartTag, Token, TokenSink, TokenSinkResult, Tokenizer,
    };
    use std::cell::{Cell, RefCell};
    #[derive(Default)]
    struct ChallengeSink {
        matched: Cell<bool>,
        value: RefCell<Option<String>>,
    }
    impl TokenSink for ChallengeSink {
        type Handle = ();
        fn process_token(&self, token: Token, _line_number: u64) -> TokenSinkResult<()> {
            let Token::TagToken(tag) = token else {
                return TokenSinkResult::Continue;
            };
            if tag.kind != StartTag {
                return TokenSinkResult::Continue;
            }
            match tag.name.as_ref() {
                "input" if !self.matched.get() => {
                    let id = tag
                        .attrs
                        .iter()
                        .find(|attribute| attribute.name.local == html5ever::local_name!("id"));
                    if id.is_some_and(|attribute| attribute.value.as_ref() == "recaptcha-token") {
                        self.matched.set(true);
                        if let Some(value) = tag
                            .attrs
                            .iter()
                            .find(|attribute| {
                                attribute.name.local == html5ever::local_name!("value")
                            })
                            .map(|attribute| &attribute.value)
                            .filter(|value| !value.is_empty() && value.len() <= 65536)
                        {
                            self.value.replace(Some(value.to_string()));
                        }
                    }
                }
                // Tokenization never executes scripts. Their contents must not create fake inputs.
                "script" => return TokenSinkResult::RawData(RawKind::ScriptData),
                "style" | "xmp" | "iframe" | "noembed" | "noframes" | "noscript" => {
                    return TokenSinkResult::RawData(RawKind::Rawtext)
                }
                "textarea" | "title" => return TokenSinkResult::RawData(RawKind::Rcdata),
                "plaintext" => return TokenSinkResult::Plaintext,
                _ => {}
            }
            TokenSinkResult::Continue
        }
    }
    let input = BufferQueue::default();
    input.push_back(body.into());
    let tokenizer = Tokenizer::new(ChallengeSink::default(), Default::default());
    let _ = tokenizer.feed(&input);
    tokenizer.end();
    tokenizer.sink.value.into_inner().ok_or_else(|| {
        Error::new(
            ErrorKind::ProtocolError,
            "anchor",
            "challenge token missing or invalid",
        )
    })
}
pub(crate) fn token(body: &str) -> Result<String> {
    let value: serde_json::Value =
        serde_json::from_str(body.strip_prefix(")]}'").unwrap_or(body).trim()).map_err(|_| {
            Error::new(
                ErrorKind::ProtocolError,
                "reload",
                "invalid reload response",
            )
        })?;
    let array = value.as_array().ok_or_else(|| {
        Error::new(
            ErrorKind::ProtocolError,
            "reload",
            "invalid reload response",
        )
    })?;
    if array.first().and_then(|v| v.as_str()) != Some("rresp") {
        return Err(Error::new(
            ErrorKind::ProtocolError,
            "reload",
            "unexpected reload response",
        ));
    }
    array
        .get(1)
        .and_then(|v| v.as_str())
        .filter(|v| !v.is_empty() && v.len() <= 65536)
        .map(str::to_owned)
        .ok_or_else(|| Error::new(ErrorKind::ProtocolError, "reload", "result token missing"))
}
