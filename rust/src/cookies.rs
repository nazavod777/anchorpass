use crate::{Error, ErrorKind, Result};
use cookie_store::{Cookie, CookieDomain, CookieStore};
use reqwest::header::{HeaderMap, SET_COOKIE};
use url::Url;

const MAX_OPERATION_COOKIES: usize = 64;
const MAX_COOKIE_BYTES: usize = 16384;

#[derive(Default)]
pub(crate) struct OperationCookies {
    store: CookieStore,
    count: usize,
    bytes: usize,
}

fn limit_error(stage: &'static str) -> Error {
    Error::new(ErrorKind::ResourceLimit, stage, "cookie limit exceeded")
}

impl OperationCookies {
    pub fn header(&self, url: &Url, stage: &'static str) -> Result<Option<String>> {
        let mut header = String::new();
        for (name, value) in self.store.get_request_values(url) {
            if !header.is_empty() {
                header.push_str("; ");
            }
            header.push_str(name);
            header.push('=');
            header.push_str(value);
            if header.len() > MAX_COOKIE_BYTES {
                return Err(limit_error(stage));
            }
        }
        Ok((!header.is_empty()).then_some(header))
    }

    pub fn receive(&mut self, url: &Url, headers: &HeaderMap, stage: &'static str) -> Result<()> {
        for value in headers.get_all(SET_COOKIE) {
            self.count += 1;
            self.bytes += value.as_bytes().len();
            if self.count > MAX_OPERATION_COOKIES || self.bytes > MAX_COOKIE_BYTES {
                return Err(limit_error(stage));
            }
            let Ok(raw) = value.to_str() else {
                continue;
            };
            let Ok(cookie) = Cookie::parse(raw.to_owned(), url) else {
                continue;
            };
            // Production URLs are restricted to google.com and www.google.com.
            // Only their exact hosts or the google.com parent can set Domain;
            // this also rejects public suffixes without loading a mutable PSL.
            if let CookieDomain::Suffix(domain) = &cookie.domain {
                if Some(domain.as_str()) != url.host_str()
                    && !(domain == "google.com" && url.host_str() == Some("www.google.com"))
                {
                    continue;
                }
            }
            // The store ignores invalid scope and applies expiry/deletion semantics.
            let _ = self.store.insert(cookie.into_owned(), url);
        }
        Ok(())
    }
}
