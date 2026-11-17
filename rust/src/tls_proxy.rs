//! HTTPS-proxy transport with separate, immutable TLS policies for the two hops.
pub(crate) struct RequestHeaders<'a> {
    pub(crate) cookies: Option<&'a mut crate::cookies::OperationCookies>,
    pub(crate) user_agent: Option<&'a str>,
}

use crate::{Error, ErrorKind, Result};
use base64::{engine::general_purpose::STANDARD, Engine};
use http_body_util::{BodyExt, Full};
use hyper::{body::Bytes, Request, Uri};
use hyper_util::{
    client::legacy::{
        connect::{Connected, Connection},
        Client,
    },
    rt::{TokioExecutor, TokioIo, TokioTimer},
};
use rustls::{
    pki_types::{pem::PemObject, CertificateDer, ServerName},
    ClientConfig, RootCertStore,
};
use std::{
    future::Future,
    io,
    pin::Pin,
    sync::Arc,
    task::{Context, Poll},
    time::Duration,
};
use tokio::{
    io::{AsyncRead, AsyncReadExt, AsyncWrite, AsyncWriteExt, ReadBuf},
    net::TcpStream,
    time::Sleep,
};
use tower_service::Service;
use url::Url;

const CONNECT_TIMEOUT: Duration = Duration::from_secs(5);
const READ_IDLE_TIMEOUT: Duration = Duration::from_secs(5);
const MAX_CONNECT_HEADERS: usize = 16384;

type Pool = Client<Connector, Full<Bytes>>;
trait Io: AsyncRead + AsyncWrite + Unpin + Send {}
impl<T: AsyncRead + AsyncWrite + Unpin + Send> Io for T {}

fn failure(kind: ErrorKind) -> Error {
    Error::new(kind, "transport", "HTTPS proxy transport failed")
}
fn network_error(error: &(dyn std::error::Error + 'static)) -> Error {
    let mut cause = Some(error);
    while let Some(current) = cause {
        if let Some(error) = current.downcast_ref::<Error>() {
            return error.clone();
        }
        if current
            .downcast_ref::<io::Error>()
            .is_some_and(|error| error.kind() == io::ErrorKind::TimedOut)
        {
            return failure(ErrorKind::DeadlineExceeded);
        }
        cause = current.source();
    }
    failure(ErrorKind::TransportError)
}
fn tls_config(roots: &[Vec<u8>]) -> Result<Arc<ClientConfig>> {
    let invalid = || Error::new(ErrorKind::InvalidInput, "build", "invalid CA certificate");
    let mut store = RootCertStore::empty();
    store.extend(webpki_roots::TLS_SERVER_ROOTS.iter().cloned());
    for pem in roots {
        let mut found = false;
        for certificate in CertificateDer::pem_slice_iter(pem) {
            store
                .add(certificate.map_err(|_| invalid())?)
                .map_err(|_| invalid())?;
            found = true;
        }
        if !found {
            return Err(invalid());
        }
    }
    Ok(Arc::new(
        ClientConfig::builder_with_provider(Arc::new(rustls::crypto::ring::default_provider()))
            .with_safe_default_protocol_versions()
            .map_err(|_| invalid())?
            .with_root_certificates(store)
            .with_no_client_auth(),
    ))
}

pub(crate) struct HttpsProxy {
    pool: Pool,
}
impl HttpsProxy {
    pub(crate) fn new(
        proxy: &str,
        connections: usize,
        target_roots: &[Vec<u8>],
        proxy_roots: &[Vec<u8>],
    ) -> Result<Self> {
        // The shared parser has already validated the URL and credential encoding.
        crate::proxy::parse(proxy)?;
        let proxy = Url::parse(proxy).map_err(|_| failure(ErrorKind::InvalidProxy))?;
        let authorization = if !proxy.username().is_empty() || proxy.password().is_some() {
            let decode = |value: &str| {
                percent_encoding::percent_decode_str(value)
                    .decode_utf8()
                    .map(|value| value.into_owned())
                    .map_err(|_| failure(ErrorKind::InvalidProxy))
            };
            Some(format!(
                "Basic {}",
                STANDARD.encode(format!(
                    "{}:{}",
                    decode(proxy.username())?,
                    decode(proxy.password().unwrap_or(""))?
                ))
            ))
        } else {
            None
        };
        let connector = Connector {
            host: proxy
                .host_str()
                .ok_or_else(|| failure(ErrorKind::InvalidProxy))?
                .trim_matches(['[', ']'])
                .to_owned(),
            port: proxy
                .port_or_known_default()
                .ok_or_else(|| failure(ErrorKind::InvalidProxy))?,
            authorization,
            proxy_tls: tls_config(proxy_roots)?,
            target_tls: tls_config(target_roots)?,
        };
        let pool = Client::builder(TokioExecutor::new())
            .pool_max_idle_per_host(connections)
            .pool_idle_timeout(Duration::from_secs(30))
            .pool_timer(TokioTimer::new())
            .retry_canceled_requests(false)
            .http1_max_buf_size(65536)
            .build(connector);
        Ok(Self { pool })
    }
    pub(crate) async fn request(
        &self,
        url: Url,
        form: Option<String>,
        limit: usize,
        stage: &'static str,
        timeout: Duration,
        headers: RequestHeaders<'_>,
    ) -> Result<String> {
        let mut cookies = headers.cookies;
        let operation = async {
            let mut builder = Request::builder().uri(url.as_str());
            if let Some(user_agent) = headers.user_agent {
                builder = builder.header(hyper::header::USER_AGENT, user_agent);
            }
            if form.is_some() {
                builder = builder.method("POST").header(
                    hyper::header::CONTENT_TYPE,
                    "application/x-www-form-urlencoded",
                );
            }
            if let Some(cookies) = &cookies {
                if let Some(header) = cookies.header(&url, stage)? {
                    builder = builder.header(hyper::header::COOKIE, header);
                }
            }
            let request = builder
                .body(Full::new(Bytes::from(form.unwrap_or_default())))
                .map_err(|_| failure(ErrorKind::InvalidInput))?;
            let mut response = self
                .pool
                .request(request)
                .await
                .map_err(|error| network_error(&error))?;
            if let Some(cookies) = &mut cookies {
                cookies.receive(&url, response.headers(), stage)?;
            }
            if !response.status().is_success() {
                return Err(failure(
                    if response.status() == hyper::StatusCode::PROXY_AUTHENTICATION_REQUIRED {
                        ErrorKind::ProxyAuthError
                    } else {
                        ErrorKind::HttpStatusError
                    },
                ));
            }
            if response
                .headers()
                .get(hyper::header::CONTENT_LENGTH)
                .and_then(|value| value.to_str().ok())
                .and_then(|value| value.parse::<u64>().ok())
                .is_some_and(|size| size > limit as u64)
            {
                return Err(failure(ErrorKind::ResourceLimit));
            }
            let mut body = Vec::new();
            while let Some(frame) = response.body_mut().frame().await {
                let frame = frame.map_err(|error| network_error(&error))?;
                if let Ok(chunk) = frame.into_data() {
                    if chunk.len() > limit.saturating_sub(body.len()) {
                        return Err(failure(ErrorKind::ResourceLimit));
                    }
                    body.extend_from_slice(&chunk);
                }
            }
            String::from_utf8(body).map_err(|_| failure(ErrorKind::ProtocolError))
        };
        tokio::time::timeout(timeout, operation)
            .await
            .unwrap_or_else(|_| Err(failure(ErrorKind::DeadlineExceeded)))
            .map_err(|mut error| {
                error.stage = stage;
                error
            })
    }
}

#[derive(Clone)]
struct Connector {
    host: String,
    port: u16,
    authorization: Option<String>,
    proxy_tls: Arc<ClientConfig>,
    target_tls: Arc<ClientConfig>,
}
impl Service<Uri> for Connector {
    type Response = ProxyStream;
    type Error = Error;
    type Future = Pin<Box<dyn Future<Output = Result<ProxyStream>> + Send>>;
    fn poll_ready(&mut self, _: &mut Context<'_>) -> Poll<Result<()>> {
        Poll::Ready(Ok(()))
    }
    fn call(&mut self, uri: Uri) -> Self::Future {
        let connector = self.clone();
        Box::pin(async move {
            tokio::time::timeout(CONNECT_TIMEOUT, connector.connect(uri))
                .await
                .unwrap_or_else(|_| Err(failure(ErrorKind::DeadlineExceeded)))
        })
    }
}
impl Connector {
    async fn connect(self, uri: Uri) -> Result<ProxyStream> {
        let host = uri
            .host()
            .ok_or_else(|| failure(ErrorKind::InvalidInput))?
            .trim_matches(['[', ']']);
        let tls = uri.scheme_str() == Some("https");
        let port = uri.port_u16().unwrap_or(if tls { 443 } else { 80 });
        let authority = if host.contains(':') {
            format!("[{host}]:{port}")
        } else {
            format!("{host}:{port}")
        };
        let tcp = TcpStream::connect((self.host.as_str(), self.port))
            .await
            .map_err(|_| failure(ErrorKind::ProxyConnectError))?;
        let name = ServerName::try_from(self.host).map_err(|_| failure(ErrorKind::InvalidProxy))?;
        let mut stream = tokio_rustls::TlsConnector::from(self.proxy_tls)
            .connect(name, tcp)
            .await
            .map_err(|_| failure(ErrorKind::ProxyConnectError))?;
        let authorization = self
            .authorization
            .map(|value| format!("Proxy-Authorization: {value}\r\n"))
            .unwrap_or_default();
        let request =
            format!("CONNECT {authority} HTTP/1.1\r\nHost: {authority}\r\n{authorization}\r\n");
        stream
            .write_all(request.as_bytes())
            .await
            .map_err(|_| failure(ErrorKind::ProxyConnectError))?;
        // Read only the CONNECT head; never consume bytes belonging to the target TLS stream.
        let mut head = Vec::new();
        while !head.ends_with(b"\r\n\r\n") {
            if head.len() >= MAX_CONNECT_HEADERS {
                return Err(failure(ErrorKind::ProxyConnectError));
            }
            head.push(
                stream
                    .read_u8()
                    .await
                    .map_err(|_| failure(ErrorKind::ProxyConnectError))?,
            );
        }
        let status = head
            .split(|byte| *byte == b'\n')
            .next()
            .and_then(|line| std::str::from_utf8(line).ok())
            .and_then(|line| {
                let mut fields = line.split_ascii_whitespace();
                matches!(fields.next(), Some("HTTP/1.0" | "HTTP/1.1"))
                    .then(|| fields.next()?.parse::<u16>().ok())
                    .flatten()
            });
        match status {
            Some(200..=299) => {}
            Some(407) => return Err(failure(ErrorKind::ProxyAuthError)),
            _ => return Err(failure(ErrorKind::ProxyConnectError)),
        }
        let stream: Box<dyn Io> = if tls {
            let name = ServerName::try_from(host.to_owned())
                .map_err(|_| failure(ErrorKind::InvalidInput))?;
            Box::new(
                tokio_rustls::TlsConnector::from(self.target_tls)
                    .connect(name, stream)
                    .await
                    .map_err(|_| failure(ErrorKind::TransportError))?,
            )
        } else {
            Box::new(stream)
        };
        Ok(ProxyStream(TokioIo::new(IdleStream { stream, idle: None })))
    }
}

// An owned read timer also covers slow response headers and does not leave a task behind.
struct IdleStream {
    stream: Box<dyn Io>,
    idle: Option<Pin<Box<Sleep>>>,
}
impl AsyncRead for IdleStream {
    fn poll_read(
        mut self: Pin<&mut Self>,
        cx: &mut Context<'_>,
        buf: &mut ReadBuf<'_>,
    ) -> Poll<io::Result<()>> {
        match Pin::new(&mut self.stream).poll_read(cx, buf) {
            Poll::Ready(result) => {
                self.idle = None;
                Poll::Ready(result)
            }
            Poll::Pending => {
                let idle = self
                    .idle
                    .get_or_insert_with(|| Box::pin(tokio::time::sleep(READ_IDLE_TIMEOUT)));
                if idle.as_mut().poll(cx).is_ready() {
                    Poll::Ready(Err(io::Error::new(
                        io::ErrorKind::TimedOut,
                        "response read idle deadline exceeded",
                    )))
                } else {
                    Poll::Pending
                }
            }
        }
    }
}
impl AsyncWrite for IdleStream {
    fn poll_write(
        mut self: Pin<&mut Self>,
        cx: &mut Context<'_>,
        buf: &[u8],
    ) -> Poll<io::Result<usize>> {
        Pin::new(&mut self.stream).poll_write(cx, buf)
    }
    fn poll_flush(mut self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<io::Result<()>> {
        Pin::new(&mut self.stream).poll_flush(cx)
    }
    fn poll_shutdown(mut self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<io::Result<()>> {
        Pin::new(&mut self.stream).poll_shutdown(cx)
    }
}
struct ProxyStream(TokioIo<IdleStream>);
impl Connection for ProxyStream {
    fn connected(&self) -> Connected {
        Connected::new()
    }
}
impl hyper::rt::Read for ProxyStream {
    fn poll_read(
        mut self: Pin<&mut Self>,
        cx: &mut Context<'_>,
        buf: hyper::rt::ReadBufCursor<'_>,
    ) -> Poll<io::Result<()>> {
        Pin::new(&mut self.0).poll_read(cx, buf)
    }
}
impl hyper::rt::Write for ProxyStream {
    fn poll_write(
        mut self: Pin<&mut Self>,
        cx: &mut Context<'_>,
        buf: &[u8],
    ) -> Poll<io::Result<usize>> {
        Pin::new(&mut self.0).poll_write(cx, buf)
    }
    fn poll_flush(mut self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<io::Result<()>> {
        Pin::new(&mut self.0).poll_flush(cx)
    }
    fn poll_shutdown(mut self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<io::Result<()>> {
        Pin::new(&mut self.0).poll_shutdown(cx)
    }
}
