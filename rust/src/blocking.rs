//! Blocking client. Call it from ordinary synchronous threads, outside a Tokio runtime.
use crate::client::ClientBuilder as AsyncBuilder;
use crate::{
    BundleInfo, Error, ErrorKind, Result, SolveOptions, SolveResult, TrustedKeys, UpdatesProxy,
};
use std::{path::PathBuf, time::Duration};

pub struct Client {
    client: crate::client::Client,
    runtime: tokio::runtime::Runtime,
}
pub struct ClientBuilder {
    inner: AsyncBuilder,
}
impl Client {
    pub fn builder() -> ClientBuilder {
        ClientBuilder {
            inner: crate::client::Client::builder(),
        }
    }
    pub fn solve(&self, url: &str) -> Result<SolveResult> {
        self.check_context()?;
        self.runtime.block_on(self.client.solve(url))
    }
    pub fn solve_with_action(&self, url: &str, action: &str) -> Result<SolveResult> {
        self.check_context()?;
        self.runtime
            .block_on(self.client.solve_with_action(url, action))
    }
    pub fn solve_with_options(&self, url: &str, options: SolveOptions<'_>) -> Result<SolveResult> {
        self.check_context()?;
        self.runtime
            .block_on(self.client.solve_with_options(url, options))
    }
    pub fn load_bg(&self) -> Result<String> {
        self.check_context()?;
        self.runtime.block_on(self.client.load_bg())
    }
    pub fn prepare(&self) -> Result<BundleInfo> {
        self.check_context()?;
        self.runtime.block_on(self.client.prepare())
    }
    pub fn refresh(&self) -> Result<BundleInfo> {
        self.check_context()?;
        self.runtime.block_on(self.client.refresh())
    }
    fn check_context(&self) -> Result<()> {
        check_context()
    }
}
fn check_context() -> Result<()> {
    if tokio::runtime::Handle::try_current().is_ok() {
        Err(Error::new(
            ErrorKind::InvalidInput,
            "blocking",
            "blocking API cannot run inside a Tokio runtime",
        ))
    } else {
        Ok(())
    }
}
impl ClientBuilder {
    pub fn proxy(mut self, v: impl Into<String>) -> Self {
        self.inner = self.inner.proxy(v);
        self
    }
    pub fn updates_proxy(mut self, v: UpdatesProxy) -> Self {
        self.inner = self.inner.updates_proxy(v);
        self
    }
    pub fn timeout(mut self, v: Duration) -> Self {
        self.inner = self.inner.timeout(v);
        self
    }
    pub fn update_timeout(mut self, v: Duration) -> Self {
        self.inner = self.inner.update_timeout(v);
        self
    }
    pub fn max_response_bytes(mut self, v: usize) -> Self {
        self.inner = self.inner.max_response_bytes(v);
        self
    }
    pub fn max_in_flight(mut self, v: usize) -> Self {
        self.inner = self.inner.max_in_flight(v);
        self
    }
    pub fn bundle_url(mut self, v: impl Into<String>) -> Self {
        self.inner = self.inner.bundle_url(v);
        self
    }
    pub fn cache_path(mut self, v: impl Into<PathBuf>) -> Self {
        self.inner = self.inner.cache_path(v);
        self
    }
    pub fn memory_cache(mut self) -> Self {
        self.inner = self.inner.memory_cache();
        self
    }
    pub fn pin(mut self, v: impl Into<String>) -> Self {
        self.inner = self.inner.pin(v);
        self
    }
    pub fn offline(mut self, v: bool) -> Self {
        self.inner = self.inner.offline(v);
        self
    }
    pub fn refresh_interval(mut self, v: Duration) -> Self {
        self.inner = self.inner.refresh_interval(v);
        self
    }
    pub fn trusted_keys(mut self, v: TrustedKeys) -> Self {
        self.inner = self.inner.trusted_keys(v);
        self
    }
    pub fn add_root_certificate(mut self, pem: &[u8]) -> Result<Self> {
        self.inner = self.inner.add_root_certificate(pem)?;
        Ok(self)
    }
    pub fn add_proxy_root_certificate(mut self, pem: &[u8]) -> Result<Self> {
        self.inner = self.inner.add_proxy_root_certificate(pem)?;
        Ok(self)
    }
    pub fn build(self) -> Result<Client> {
        check_context()?;
        let runtime = tokio::runtime::Builder::new_current_thread()
            .enable_all()
            .build()
            .map_err(|_| {
                Error::new(
                    ErrorKind::WorkerUnavailable,
                    "build",
                    "blocking runtime initialization failed",
                )
            })?;
        let client = {
            let _entered = runtime.enter();
            self.inner.build()?
        };
        Ok(Client { client, runtime })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::{Read, Write};

    #[test]
    fn solve_options_forward_user_agent_and_hostname() {
        let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
        let address = listener.local_addr().unwrap();
        listener.set_nonblocking(true).unwrap();
        let server = std::thread::spawn(move || {
            let deadline = std::time::Instant::now() + Duration::from_secs(5);
            let mut requests = Vec::new();
            while requests.len() < 3 {
                let mut stream = match listener.accept() {
                    Ok((stream, _)) => stream,
                    Err(error)
                        if error.kind() == std::io::ErrorKind::WouldBlock
                            && std::time::Instant::now() < deadline =>
                    {
                        std::thread::sleep(Duration::from_millis(1));
                        continue;
                    }
                    Err(error) => panic!("fixture accept: {error}"),
                };
                stream
                    .set_read_timeout(Some(Duration::from_secs(2)))
                    .unwrap();
                stream
                    .set_write_timeout(Some(Duration::from_secs(2)))
                    .unwrap();
                let mut bytes = Vec::new();
                let mut byte = [0];
                while !bytes.ends_with(b"\r\n\r\n") {
                    stream.read_exact(&mut byte).unwrap();
                    bytes.push(byte[0]);
                    assert!(bytes.len() < 65536);
                }
                let header = String::from_utf8(bytes.clone()).unwrap();
                let length = header
                    .lines()
                    .find_map(|line| {
                        line.to_ascii_lowercase()
                            .strip_prefix("content-length: ")
                            .and_then(|value| value.parse::<usize>().ok())
                    })
                    .unwrap_or(0);
                assert!(length < 65536);
                let mut body = vec![0; length];
                stream.read_exact(&mut body).unwrap();
                bytes.extend(body);
                requests.push(String::from_utf8(bytes).unwrap());
                let body = match requests.len() {
                    1 => "/recaptcha/releases/current-version/recaptcha.js",
                    2 => "<input id='recaptcha-token' value='anchor'>",
                    _ => "[\"rresp\",\"fixture-token\"]",
                };
                write!(
                    stream,
                    "HTTP/1.1 200 OK\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
                    body.len()
                )
                .unwrap();
            }
            requests
        });
        let mut builder = Client::builder().memory_cache().offline(true);
        builder.inner.origin = Some(url::Url::parse(&format!("http://{address}")).unwrap());
        let client = builder.build().unwrap();
        let input =
            "https://www.google.com/recaptcha/api2/anchor?k=x&co=aHR0cHM6Ly9vbGQuZXhhbXBsZTo0NDM.";
        assert_eq!(
            client
                .solve_with_options(
                    input,
                    SolveOptions {
                        user_agent: Some("blocking-agent/1"),
                        hostname: Some("new.example"),
                        ..Default::default()
                    }
                )
                .unwrap()
                .token,
            "fixture-token"
        );
        let requests = server.join().unwrap();
        assert!(requests.iter().all(|request| request
            .to_ascii_lowercase()
            .contains("user-agent: blocking-agent/1\r\n")));
        let expected =
            crate::hostname::origin("aHR0cHM6Ly9vbGQuZXhhbXBsZTo0NDM.", "new.example").unwrap();
        assert!(requests[1].contains(&format!("co={expected}")));
        assert!(requests[2].contains(&format!("co={expected}")));
        assert_eq!(
            client
                .solve_with_options(
                    input,
                    SolveOptions {
                        user_agent: Some("bad\r\nagent"),
                        ..Default::default()
                    }
                )
                .unwrap_err()
                .kind,
            ErrorKind::InvalidInput
        );
    }
}
