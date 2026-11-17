use super::*;
use crate::bundle::{self, Bundle};
use crate::client::{Client, ClientBuilder};
use base64::{engine::general_purpose::STANDARD, Engine};
use ed25519_dalek::{Signer, SigningKey};
use serde_json::json;
use std::{
    collections::BTreeMap,
    sync::{Arc, Mutex as StdMutex},
};
use tokio::{
    io::{AsyncReadExt, AsyncWriteExt},
    net::TcpListener,
    task::JoinHandle,
};

struct Server {
    url: Url,
    requests: Arc<StdMutex<Vec<String>>>,
    task: JoinHandle<()>,
}
impl Drop for Server {
    fn drop(&mut self) {
        self.task.abort();
    }
}
async fn server(responses: Vec<(u16, String)>, pause: Duration) -> Server {
    server_with_headers(
        responses
            .into_iter()
            .map(|(status, body)| (status, body, "Set-Cookie: test=private\r\n".to_owned()))
            .collect(),
        pause,
    )
    .await
}
async fn server_with_headers(responses: Vec<(u16, String, String)>, pause: Duration) -> Server {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let address = listener.local_addr().unwrap();
    let requests = Arc::new(StdMutex::new(Vec::new()));
    let captured = requests.clone();
    let task = tokio::spawn(async move {
        for (status, body, headers) in responses {
            let (mut stream, _) = listener.accept().await.unwrap();
            let mut request = Vec::new();
            let mut byte = [0];
            while !request.ends_with(b"\r\n\r\n") {
                stream.read_exact(&mut byte).await.unwrap();
                request.push(byte[0]);
                assert!(request.len() < 65536);
            }
            let header = String::from_utf8(request.clone()).unwrap();
            let length = header
                .lines()
                .find_map(|l| {
                    l.to_ascii_lowercase()
                        .strip_prefix("content-length: ")
                        .and_then(|s| s.trim().parse::<usize>().ok())
                })
                .unwrap_or(0);
            let mut body_bytes = vec![0; length];
            stream.read_exact(&mut body_bytes).await.unwrap();
            request.extend(body_bytes);
            captured
                .lock()
                .unwrap()
                .push(String::from_utf8(request).unwrap());
            tokio::time::sleep(pause).await;
            let response=format!("HTTP/1.1 {status} Test\r\nContent-Length: {}\r\nConnection: close\r\n{headers}\r\n{body}",body.len());
            let _ = stream.write_all(response.as_bytes()).await;
        }
    });
    Server {
        url: Url::parse(&format!("http://{address}/")).unwrap(),
        requests,
        task,
    }
}
fn builder(origin: &Server) -> ClientBuilder {
    let mut b = Client::builder().memory_cache().offline(true);
    b.origin = Some(origin.url.clone());
    b
}
fn signed(revision: &str, expires: u64) -> (Vec<u8>, TrustedKeys) {
    let initial = bundle::Bundle::verify(
        include_bytes!("../data/bundle.json"),
        &ClientBuilder::default().keys,
    )
    .unwrap();
    let payload = json!({"schema_version":1,"protocol_family":"recaptcha-v3/1","revision":revision,"issued_at":bundle::now().unwrap().saturating_sub(1).min(expires.saturating_sub(1)),"expires_at":expires,"fields":initial.payload.fields});
    sign_bytes(serde_json::to_vec(&payload).unwrap())
}
fn sign_bytes(payload: Vec<u8>) -> (Vec<u8>, TrustedKeys) {
    let key = SigningKey::from_bytes(&[7; 32]);
    let mut message = b"anchorpass.bundle.v1\0test\0".to_vec();
    message.extend(&payload);
    let envelope=serde_json::to_vec(&json!({"key_id":"test","payload":STANDARD.encode(payload),"signature":STANDARD.encode(key.sign(&message).to_bytes())})).unwrap();
    (
        envelope,
        BTreeMap::from([(
            "test".to_owned(),
            STANDARD.encode(key.verifying_key().to_bytes()),
        )]),
    )
}
#[test]
fn proxy_vectors() {
    let vectors: Vec<serde_json::Value> =
        serde_json::from_str(include_str!("../tests/data/proxies.json")).unwrap();
    for vector in vectors {
        if let Some(value) = vector["value"].as_str() {
            assert_eq!(
                proxy::parse(value).is_ok(),
                vector["valid"].as_bool().unwrap(),
                "{value}"
            );
        }
    }
}
#[test]
fn strict_anchor_and_parsers() {
    for input in [
        "http://www.google.com/recaptcha/api2/anchor?k=x&co=y",
        "https://evil.example/recaptcha/api2/anchor?k=x&co=y",
        "https://u@www.google.com/recaptcha/api2/anchor?k=x&co=y",
        "https://www.google.com/recaptcha/api2/anchor?k=x&co=y#",
        "https://www.google.com/recaptcha/api2/anchor?k=&co=y",
        "https://www.google.com/recaptcha/api2/anchor?k=x&co=y&k=z",
        "https://www.google.com/recaptcha/api2/anchor?k=x&co=y&sa=a&sa=b",
        "https://www.google.com/recaptcha/api2/anchor?k=x&co=%xx",
    ] {
        assert!(core::parse_anchor(input, None).is_err(), "{input}");
    }
    assert!(core::parse_anchor(
        "https://google.com/recaptcha/enterprise/anchor?k=x&co=y",
        None
    )
    .is_ok());
    assert!(core::token("[\"rresp\",\"\"]").is_err());
    assert!(core::token("[\"wrong\",\"abc\"]").is_err());
    assert_eq!(
        core::challenge("<input value='abc' id='recaptcha-token'>").unwrap(),
        "abc"
    );
}
#[test]
fn signature_validation_and_schema() {
    let (bytes, keys) = signed("2", bundle::now().unwrap() + 600);
    let verified = Bundle::verify(&bytes, &keys).unwrap();
    assert_eq!(verified.payload.revision, "2");
    let mut changed: serde_json::Value = serde_json::from_slice(&bytes).unwrap();
    changed["payload"] = json!(STANDARD.encode(b"{}"));
    assert_eq!(
        Bundle::verify(&serde_json::to_vec(&changed).unwrap(), &keys)
            .err()
            .unwrap()
            .kind,
        ErrorKind::ConfigInvalid
    );
    let (duplicate, keys) = sign_bytes(br#"{"schema_version":1,"schema_version":1}"#.to_vec());
    assert!(Bundle::verify(&duplicate, &keys).is_err());
    for rev in ["01", "-1", "1e2", ""] {
        let (bytes, keys) = signed(rev, bundle::now().unwrap() + 600);
        assert!(Bundle::verify(&bytes, &keys).is_err());
    }
    let (expired, keys) = signed("3", bundle::now().unwrap());
    assert_eq!(
        Bundle::verify(&expired, &keys)
            .unwrap()
            .valid_for(0)
            .unwrap_err()
            .kind,
        ErrorKind::ConfigExpired
    );
    let (old, keys) = signed("1", bundle::now().unwrap() + 600);
    assert_eq!(
        verified
            .advance(&Bundle::verify(&old, &keys).unwrap())
            .unwrap_err()
            .kind,
        ErrorKind::ConfigRollback
    );
    assert!(bundle::revision_cmp("99999999999999999999", "100000000000000000000").is_lt());
}
#[tokio::test]
async fn both_protocol_variants_and_form_encoding() {
    for variant in ["api2", "enterprise"] {
        let origin = server(
            vec![
                (200, "/recaptcha/releases/version-123/recaptcha.js".into()),
                (
                    200,
                    "<input id='recaptcha-token' value='challenge+&='>".replace("+&=", "+="),
                ),
                (200, ")]}'\n[\"rresp\",\"result-token\"]".into()),
            ],
            Duration::ZERO,
        )
        .await;
        let client = builder(&origin).build().unwrap();
        let url=format!("https://www.google.com/recaptcha/{variant}/anchor?k=site%2B%26%3D&co=origin%2B&sa=url-action&extra=keep");
        let result = client
            .solve_with_action(&url, "explicit +&=")
            .await
            .unwrap();
        assert_eq!(result.token, "result-token");
        assert_eq!(result.protocol_version, "version-123");
        let requests = origin.requests.lock().unwrap();
        assert_eq!(requests.len(), 3);
        assert!(requests[1].contains("extra=keep"));
        assert!(requests[1].contains("sa=explicit+%2B%26%3D"));
        let body = requests[2].split("\r\n\r\n").nth(1).unwrap();
        let fields: BTreeMap<_, _> = url::form_urlencoded::parse(body.as_bytes())
            .into_owned()
            .collect();
        assert_eq!(fields["k"], "site+&=");
        assert_eq!(fields["c"], "challenge+=");
        assert_eq!(fields["co"], "origin+");
        assert_eq!(fields["sa"], "explicit +&=");
        assert!(fields["bg"].starts_with('!'));
        assert!(!requests[0].to_ascii_lowercase().contains("cookie:"));
        assert!(requests[1..]
            .iter()
            .all(|r| r.to_ascii_lowercase().contains("cookie: test=private")));
        assert!(requests
            .iter()
            .all(|r| !r.to_ascii_lowercase().contains("proxy-authorization:")));
    }
}
#[tokio::test]
async fn proxy_route_auth_and_no_direct_fallback() {
    let proxy_server = server(
        vec![
            (
                200,
                "/recaptcha/releases/current-version/recaptcha.js".into(),
            ),
            (200, "<input id='recaptcha-token' value='challenge'>".into()),
            (200, "[\"rresp\",\"token\"]".into()),
        ],
        Duration::ZERO,
    )
    .await;
    let direct = server(vec![(200, "unused".into())], Duration::ZERO).await;
    let mut b = builder(&direct).proxy(format!(
        "http://user:p%40ss@{}",
        proxy_server.url.socket_addrs(|| None).unwrap()[0]
    ));
    b.origin = Some(direct.url.clone());
    b.build()
        .unwrap()
        .solve("https://www.google.com/recaptcha/api2/anchor?k=x&co=y&v=v")
        .await
        .unwrap();
    assert_eq!(direct.requests.lock().unwrap().len(), 0);
    {
        let requests = proxy_server.requests.lock().unwrap();
        assert!(requests
            .iter()
            .all(|r| r.starts_with("GET http://") || r.starts_with("POST http://")));
        assert!(requests.iter().all(|r| r
            .to_ascii_lowercase()
            .contains("proxy-authorization: basic dxnlcjpwqhnz")));
    }
    // Reserve a refused port until the request finishes; another test must not reuse it.
    let unavailable = tokio::net::TcpSocket::new_v4().unwrap();
    unavailable.bind("127.0.0.1:0".parse().unwrap()).unwrap();
    let addr = unavailable.local_addr().unwrap();
    assert_eq!(
        TcpListener::bind(addr).await.unwrap_err().kind(),
        std::io::ErrorKind::AddrInUse
    );
    let error = builder(&direct)
        .proxy(format!("http://{addr}"))
        .build()
        .unwrap()
        .solve("https://www.google.com/recaptcha/api2/anchor?k=x&co=y&v=v")
        .await
        .unwrap_err();
    drop(unavailable);
    assert_eq!(error.kind, ErrorKind::ProxyConnectError);
    assert!(direct.requests.lock().unwrap().is_empty());
    assert!(!error.to_string().contains("127.0.0.1"));
}
#[tokio::test]
async fn deadline_cancellation_limits_and_redirects() {
    let origin = server(vec![(200, "slow".into())], Duration::from_secs(2)).await;
    let client = builder(&origin)
        .timeout(Duration::from_millis(50))
        .max_in_flight(1)
        .build()
        .unwrap();
    let url = "https://www.google.com/recaptcha/api2/anchor?k=x&co=y&v=v";
    let first = client.solve(url);
    tokio::pin!(first);
    tokio::select! {r=&mut first=>panic!("unexpected result {r:?}"),_=tokio::time::sleep(Duration::from_millis(10))=>{}}
    assert_eq!(
        client.solve(url).await.unwrap_err().kind,
        ErrorKind::ResourceLimit
    );
    assert_eq!(first.await.unwrap_err().kind, ErrorKind::DeadlineExceeded);
    let origin = server(vec![(200, "x".repeat(1025))], Duration::ZERO).await;
    let client = builder(&origin).max_response_bytes(1024).build().unwrap();
    assert_eq!(
        client.solve(url).await.unwrap_err().kind,
        ErrorKind::ResourceLimit
    );
    let origin = server(vec![(302, "redirect".into())], Duration::ZERO).await;
    assert_eq!(
        builder(&origin)
            .build()
            .unwrap()
            .solve(url)
            .await
            .unwrap_err()
            .kind,
        ErrorKind::HttpStatusError
    );
    let origin = server(
        vec![(200, "slow".into()), (200, "[\"rresp\",\"token\"]".into())],
        Duration::from_secs(2),
    )
    .await;
    let client = builder(&origin).max_in_flight(1).build().unwrap();
    let mut first = Box::pin(client.solve(url));
    tokio::select! {_=&mut first=>panic!("unexpected completion"),_=tokio::time::sleep(Duration::from_millis(10))=>{}}
    drop(first);
    // Cancellation drops the owned permit immediately, allowing another operation.
    assert_eq!(client.inner.permits.available_permits(), 1);
}
#[tokio::test]
async fn update_signature_cache_rollback_pin_and_offline() {
    let (new, keys) = signed("2", bundle::now().unwrap() + 600);
    let update = server(
        vec![(200, String::from_utf8(new.clone()).unwrap())],
        Duration::ZERO,
    )
    .await;
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("bundle.json");
    let client = Client::builder()
        .trusted_keys(keys.clone())
        .bundle_url(update.url.to_string())
        .cache_path(&path)
        .build()
        .unwrap();
    assert_eq!(client.refresh().await.unwrap().revision, "2");
    assert_eq!(tokio::fs::read(&path).await.unwrap(), new);
    let offline = Client::builder()
        .trusted_keys(keys.clone())
        .cache_path(&path)
        .offline(true)
        .pin("2")
        .build()
        .unwrap();
    assert_eq!(offline.prepare().await.unwrap().revision, "2");
    assert_eq!(
        Client::builder()
            .trusted_keys(keys.clone())
            .cache_path(&path)
            .offline(true)
            .pin("1")
            .build()
            .unwrap()
            .prepare()
            .await
            .unwrap_err()
            .kind,
        ErrorKind::ConfigRollback
    );
    let (old, _) = signed("1", bundle::now().unwrap() + 600);
    let update = server(vec![(200, String::from_utf8(old).unwrap())], Duration::ZERO).await;
    assert_eq!(
        Client::builder()
            .trusted_keys(keys.clone())
            .bundle_url(update.url.to_string())
            .cache_path(&path)
            .build()
            .unwrap()
            .refresh()
            .await
            .unwrap_err()
            .kind,
        ErrorKind::ConfigRollback
    );
    let mut tamper: serde_json::Value = serde_json::from_slice(&new).unwrap();
    tamper["key_id"] = json!("unknown");
    let update = server(vec![(200, tamper.to_string())], Duration::ZERO).await;
    assert_eq!(
        Client::builder()
            .trusted_keys(keys)
            .bundle_url(update.url.to_string())
            .cache_path(&path)
            .build()
            .unwrap()
            .refresh()
            .await
            .unwrap_err()
            .kind,
        ErrorKind::ConfigInvalid
    );
    assert!(update
        .requests
        .lock()
        .unwrap()
        .iter()
        .all(|r| !r.contains("Cookie:") && !r.contains("site")));
}
#[tokio::test]
async fn update_proxy_modes() {
    for mode in [
        UpdatesProxy::Inherit,
        UpdatesProxy::Direct,
        UpdatesProxy::Proxy(String::new()),
    ] {
        let (new, keys) = signed("2", bundle::now().unwrap() + 600);
        let direct = server(
            vec![(200, String::from_utf8(new.clone()).unwrap())],
            Duration::ZERO,
        )
        .await;
        let proxy = server(vec![(200, String::from_utf8(new).unwrap())], Duration::ZERO).await;
        let proxy_url = proxy.url.to_string();
        let mode = match mode {
            UpdatesProxy::Proxy(_) => UpdatesProxy::Proxy(proxy_url.clone()),
            v => v,
        };
        let is_direct = matches!(mode, UpdatesProxy::Direct);
        Client::builder()
            .proxy(proxy_url)
            .updates_proxy(mode)
            .memory_cache()
            .trusted_keys(keys)
            .bundle_url(direct.url.to_string())
            .build()
            .unwrap()
            .refresh()
            .await
            .unwrap();
        assert_eq!(
            direct.requests.lock().unwrap().len(),
            usize::from(is_direct)
        );
        assert_eq!(
            proxy.requests.lock().unwrap().len(),
            usize::from(!is_direct)
        );
    }
}
#[cfg(feature = "blocking")]
#[test]
fn blocking_prepare_and_input_validation() {
    let client = crate::blocking::Client::builder()
        .memory_cache()
        .offline(true)
        .build()
        .unwrap();
    assert_eq!(client.prepare().unwrap().revision, "1");
    assert_eq!(
        client.solve("invalid").unwrap_err().kind,
        ErrorKind::InvalidInput
    );
}
#[cfg(feature = "blocking")]
#[tokio::test]
async fn blocking_rejects_async_context() {
    assert_eq!(
        crate::blocking::Client::builder()
            .build()
            .err()
            .unwrap()
            .kind,
        ErrorKind::InvalidInput
    );
}

struct TlsOrigin {
    url: Url,
    // Proxy upstreams use the bound socket; localhost may resolve to IPv6 first.
    address: std::net::SocketAddr,
    pem: String,
    requests: Arc<StdMutex<Vec<String>>>,
    task: JoinHandle<()>,
}
impl Drop for TlsOrigin {
    fn drop(&mut self) {
        self.task.abort();
    }
}
fn tls_config() -> (String, Arc<rustls::ServerConfig>) {
    let cert = rcgen::generate_simple_self_signed(vec![
        "localhost".into(),
        "127.0.0.1".into(),
        "only-proxy.invalid".into(),
    ])
    .unwrap();
    let key = rustls::pki_types::PrivateKeyDer::Pkcs8(rustls::pki_types::PrivatePkcs8KeyDer::from(
        cert.key_pair.serialize_der(),
    ));
    let pem = cert.cert.pem();
    let config = rustls::ServerConfig::builder_with_provider(Arc::new(
        rustls::crypto::ring::default_provider(),
    ))
    .with_safe_default_protocol_versions()
    .unwrap()
    .with_no_client_auth()
    .with_single_cert(vec![cert.cert.der().clone()], key)
    .unwrap();
    (pem, Arc::new(config))
}
async fn tls_origin() -> TlsOrigin {
    let (pem, config) = tls_config();
    tls_origin_with_config(pem, config).await
}
async fn tls_origin_with_config(pem: String, config: Arc<rustls::ServerConfig>) -> TlsOrigin {
    tls_origin_with_reply(pem, config, None).await
}
async fn tls_origin_with_reply(
    pem: String,
    config: Arc<rustls::ServerConfig>,
    reply: Option<String>,
) -> TlsOrigin {
    let acceptor = tokio_rustls::TlsAcceptor::from(config);
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = listener.local_addr().unwrap();
    let requests = Arc::new(StdMutex::new(Vec::new()));
    let captured = requests.clone();
    let task = tokio::spawn(async move {
        let mut handlers = tokio::task::JoinSet::new();
        loop {
            let (stream, _) = listener.accept().await.unwrap();
            let acceptor = acceptor.clone();
            let captured = captured.clone();
            let reply = reply.clone();
            handlers.spawn(async move {
                let Ok(mut stream) = acceptor.accept(stream).await else {
                    return;
                };
                loop {
                    let mut request = Vec::new();
                    let mut byte = [0];
                    while !request.ends_with(b"\r\n\r\n") {
                        if stream.read_exact(&mut byte).await.is_err() {
                            return;
                        }
                        request.push(byte[0]);
                        assert!(request.len() < 65536);
                    }
                    let header = String::from_utf8(request).unwrap();
                    let length = header
                        .lines()
                        .find_map(|l| {
                            l.to_ascii_lowercase()
                                .strip_prefix("content-length: ")
                                .and_then(|s| s.trim().parse::<usize>().ok())
                        })
                        .unwrap_or(0);
                    let mut body = vec![0; length];
                    if stream.read_exact(&mut body).await.is_err() {
                        return;
                    }
                    let response = if header.contains("api.js?") {
                        "/recaptcha/releases/test-version/recaptcha.js"
                    } else if header.starts_with("POST ") {
                        "[\"rresp\",\"tls-token\"]"
                    } else {
                        "<input id='recaptcha-token' value='challenge'>"
                    };
                    captured.lock().unwrap().push(header);
                    let response = reply.clone().unwrap_or_else(|| {
                        format!(
                            "HTTP/1.1 200 OK\r\nContent-Length: {}\r\nSet-Cookie: operation=fixture; Path=/recaptcha/; Secure\r\n\r\n{response}",
                            response.len()
                        )
                    });
                    if stream.write_all(response.as_bytes()).await.is_err() {
                        return;
                    }
                    if response.contains("\r\nConnection: close\r\n") {
                        let _ = stream.shutdown().await;
                        return;
                    }
                }
            });
            while handlers.try_join_next().is_some() {}
        }
    });
    TlsOrigin {
        url: Url::parse(&format!("https://localhost:{}/", addr.port())).unwrap(),
        address: addr,
        pem,
        requests,
        task,
    }
}
struct Tunnel {
    url: String,
    pem: Option<String>,
    seen: Arc<StdMutex<Vec<String>>>,
    task: JoinHandle<()>,
}
impl Drop for Tunnel {
    fn drop(&mut self) {
        self.task.abort();
    }
}
async fn tunnel(target: std::net::SocketAddr, tls: bool) -> Tunnel {
    tunnel_with_config(target, tls.then(tls_config)).await
}
async fn tunnel_with_config(
    target: std::net::SocketAddr,
    config: Option<(String, Arc<rustls::ServerConfig>)>,
) -> Tunnel {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = listener.local_addr().unwrap();
    let seen = Arc::new(StdMutex::new(Vec::new()));
    let captured = seen.clone();
    let tls = config.is_some();
    let (pem, acceptor) = if let Some((pem, config)) = config {
        (Some(pem), Some(tokio_rustls::TlsAcceptor::from(config)))
    } else {
        (None, None)
    };
    let task = tokio::spawn(async move {
        let mut handlers = tokio::task::JoinSet::new();
        loop {
            let (stream, _) = listener.accept().await.unwrap();
            let acceptor = acceptor.clone();
            let captured = captured.clone();
            handlers.spawn(async move {
                use tokio::io::{AsyncRead, AsyncWrite};
                trait Io: AsyncRead + AsyncWrite + Unpin + Send {}
                impl<T: AsyncRead + AsyncWrite + Unpin + Send> Io for T {}
                let mut stream: Box<dyn Io> = if let Some(acceptor) = acceptor {
                    match acceptor.accept(stream).await {
                        Ok(s) => Box::new(s),
                        Err(_) => return,
                    }
                } else {
                    Box::new(stream)
                };
                let mut head = Vec::new();
                let mut byte = [0];
                while !head.ends_with(b"\r\n\r\n") {
                    if stream.read_exact(&mut byte).await.is_err() {
                        return;
                    }
                    head.push(byte[0]);
                    assert!(head.len() < 16384);
                }
                let head = String::from_utf8(head).unwrap();
                assert!(head.starts_with("CONNECT "));
                captured.lock().unwrap().push(head);
                let mut upstream = tokio::net::TcpStream::connect(target).await.unwrap();
                stream
                    .write_all(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                    .await
                    .unwrap();
                let _ = tokio::io::copy_bidirectional(&mut stream, &mut upstream).await;
            });
            while handlers.try_join_next().is_some() {}
        }
    });
    Tunnel {
        url: format!(
            "{}://user:p%40ss@localhost:{}",
            if tls { "https" } else { "http" },
            addr.port()
        ),
        pem,
        seen,
        task,
    }
}
#[tokio::test]
async fn https_proxy_trust_cannot_cross_same_host_boundaries() {
    for target_uses_proxy_certificate in [false, true] {
        let (target_pem, target_config) = tls_config();
        let (proxy_pem, proxy_config) = tls_config();
        // Both hops use localhost, so a hostname-dispatching verifier would also fail
        // these controls. Each untrusted hop presents the other policy's valid cert.
        let origin = tls_origin_with_config(
            if target_uses_proxy_certificate {
                proxy_pem.clone()
            } else {
                target_pem.clone()
            },
            if target_uses_proxy_certificate {
                proxy_config.clone()
            } else {
                target_config.clone()
            },
        )
        .await;
        let proxy = tunnel_with_config(
            origin.address,
            Some((
                if target_uses_proxy_certificate {
                    proxy_pem.clone()
                } else {
                    target_pem.clone()
                },
                if target_uses_proxy_certificate {
                    proxy_config
                } else {
                    target_config
                },
            )),
        )
        .await;
        let mut builder = Client::builder()
            .offline(true)
            .proxy(&proxy.url)
            .add_root_certificate(target_pem.as_bytes())
            .unwrap()
            .add_proxy_root_certificate(proxy_pem.as_bytes())
            .unwrap();
        builder.origin = Some(origin.url.clone());
        let error = builder
            .build()
            .unwrap()
            .solve("https://www.google.com/recaptcha/api2/anchor?k=x&co=y&v=v")
            .await
            .unwrap_err();
        assert_eq!(
            error.kind,
            if target_uses_proxy_certificate {
                ErrorKind::TransportError
            } else {
                ErrorKind::ProxyConnectError
            }
        );
        assert!(origin.requests.lock().unwrap().is_empty());
        assert_eq!(
            proxy.seen.lock().unwrap().len(),
            usize::from(target_uses_proxy_certificate)
        );
    }
}
#[tokio::test]
async fn https_proxy_body_status_deadlines_and_credential_isolation() {
    for (reply, limit, expected) in [
        (
            "HTTP/1.1 200 OK\r\nContent-Length: 1024\r\n\r\n".to_owned(),
            128,
            Some(ErrorKind::ResourceLimit),
        ),
        (
            format!(
                "HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n100\r\n{}\r\n0\r\n\r\n",
                "x".repeat(256)
            ),
            128,
            Some(ErrorKind::ResourceLimit),
        ),
        (
            "HTTP/1.1 302 Found\r\nContent-Length: 0\r\nLocation: https://localhost/\r\n\r\n"
                .to_owned(),
            128,
            Some(ErrorKind::HttpStatusError),
        ),
        (
            "HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nx".to_owned(),
            128,
            Some(ErrorKind::TransportError),
        ),
        (
            "HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nx".to_owned(),
            128,
            Some(ErrorKind::DeadlineExceeded),
        ),
        (String::new(), 128, Some(ErrorKind::DeadlineExceeded)),
        (
            "HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok".to_owned(),
            128,
            None,
        ),
    ] {
        let (pem, config) = tls_config();
        let origin = tls_origin_with_reply(pem, config, Some(reply)).await;
        let proxy = tunnel(origin.address, true).await;
        // Case-insensitive schemes must still select the isolated adapter. Literal %
        // and + verify one decoding step, rather than form decoding or double decoding.
        let proxy_url = proxy
            .url
            .replace("https://user:p%40ss", "HTTPS://u%2540ser:p%2Bss");
        let client = Client::builder()
            .proxy(proxy_url)
            .add_root_certificate(origin.pem.as_bytes())
            .unwrap()
            .add_proxy_root_certificate(proxy.pem.as_ref().unwrap().as_bytes())
            .unwrap()
            .build()
            .unwrap();
        let result = request(
            &client.inner.http,
            origin.url.clone(),
            Some("field=value".into()),
            limit,
            "test",
            true,
            Duration::from_millis(100),
        )
        .await;
        match expected {
            Some(kind) => assert_eq!(result.unwrap_err().kind, kind),
            None => assert_eq!(result.unwrap(), "ok"),
        }
        assert_eq!(origin.requests.lock().unwrap().len(), 1);
        let captured = origin.requests.lock().unwrap();
        assert!(captured[0].starts_with("POST / HTTP/1.1"));
        assert!(!captured[0]
            .to_ascii_lowercase()
            .contains("proxy-authorization"));
        let expected = format!(
            "proxy-authorization: basic {}",
            STANDARD.encode("u%40ser:p+ss")
        )
        .to_ascii_lowercase();
        assert!(proxy.seen.lock().unwrap()[0]
            .to_ascii_lowercase()
            .contains(&expected));
    }
}
#[tokio::test]
async fn https_proxy_rejects_auth_and_invalid_connect_heads_without_fallback() {
    for (reply, kind) in [
        (
            "HTTP/1.1 407 Proxy Authentication Required\r\nContent-Length: 0\r\n\r\n".to_owned(),
            ErrorKind::ProxyAuthError,
        ),
        (
            "malformed 200 status\r\n\r\n".to_owned(),
            ErrorKind::ProxyConnectError,
        ),
        (
            format!(
                "HTTP/1.1 200 OK\r\nX-Oversized: {}\r\n\r\n",
                "x".repeat(16384)
            ),
            ErrorKind::ProxyConnectError,
        ),
    ] {
        let (pem, config) = tls_config();
        let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let address = listener.local_addr().unwrap();
        let task = tokio::spawn(async move {
            let (tcp, _) = listener.accept().await.unwrap();
            let mut stream = tokio_rustls::TlsAcceptor::from(config)
                .accept(tcp)
                .await
                .unwrap();
            let mut head = Vec::new();
            while !head.ends_with(b"\r\n\r\n") {
                head.push(stream.read_u8().await.unwrap());
            }
            let _ = stream.write_all(reply.as_bytes()).await;
            head
        });
        let origin = tls_origin().await;
        let mut builder = Client::builder()
            .offline(true)
            .proxy(format!(
                "https://user:p%2540ss@localhost:{}",
                address.port()
            ))
            .add_proxy_root_certificate(pem.as_bytes())
            .unwrap();
        builder.origin = Some(origin.url.clone());
        let client = builder.build().unwrap();
        let error = client
            .solve("https://www.google.com/recaptcha/api2/anchor?k=x&co=y&v=v")
            .await
            .unwrap_err();
        assert_eq!(error.kind, kind);
        assert!(!format!("{error:?} {client:?}").contains("p%40ss"));
        let head = String::from_utf8(task.await.unwrap())
            .unwrap()
            .to_ascii_lowercase();
        assert!(head.contains(
            &format!(
                "proxy-authorization: basic {}",
                STANDARD.encode("user:p%40ss")
            )
            .to_ascii_lowercase()
        ));
        assert!(origin.requests.lock().unwrap().is_empty());
    }
}

#[tokio::test]
async fn https_proxy_cancellation_closes_connect_and_target_handshake() {
    for after_connect in [false, true] {
        let (pem, config) = tls_config();
        let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let address = listener.local_addr().unwrap();
        let (ready, started) = tokio::sync::oneshot::channel();
        let (closed, disconnected) = tokio::sync::oneshot::channel();
        let proxy_task = tokio::spawn(async move {
            let (tcp, _) = listener.accept().await.unwrap();
            let mut stream = tokio_rustls::TlsAcceptor::from(config)
                .accept(tcp)
                .await
                .unwrap();
            let mut head = Vec::new();
            while !head.ends_with(b"\r\n\r\n") {
                head.push(stream.read_u8().await.unwrap());
            }
            if after_connect {
                stream.write_all(b"HTTP/1.1 200 OK\r\n\r\n").await.unwrap();
            }
            ready.send(()).unwrap();
            let mut byte = [0; 1024];
            loop {
                match stream.read(&mut byte).await {
                    Ok(0) | Err(_) => break,
                    Ok(_) => {}
                }
            }
            closed.send(()).unwrap();
        });
        let direct = tls_origin().await;
        let mut builder = Client::builder()
            .offline(true)
            .max_in_flight(1)
            .proxy(format!("https://localhost:{}", address.port()))
            .add_proxy_root_certificate(pem.as_bytes())
            .unwrap();
        builder.origin = Some(direct.url.clone());
        let client = builder.build().unwrap();
        let owned = client.clone();
        let task = tokio::spawn(async move {
            owned
                .solve("https://www.google.com/recaptcha/api2/anchor?k=x&co=y&v=v")
                .await
        });
        tokio::time::timeout(Duration::from_secs(1), started)
            .await
            .unwrap()
            .unwrap();
        task.abort();
        assert!(task.await.unwrap_err().is_cancelled());
        tokio::time::timeout(Duration::from_secs(1), disconnected)
            .await
            .unwrap()
            .unwrap();
        proxy_task.await.unwrap();
        assert_eq!(client.inner.permits.available_permits(), 1);
        assert!(direct.requests.lock().unwrap().is_empty());
    }
}
#[tokio::test]
async fn https_proxy_update_transport_uses_both_policies() {
    let (bytes, keys) = signed("2", bundle::now().unwrap() + 600);
    let body = String::from_utf8(bytes).unwrap();
    let (pem, config) = tls_config();
    let origin = tls_origin_with_reply(
        pem,
        config,
        Some(format!(
            "HTTP/1.1 200 OK\r\nContent-Length: {}\r\n\r\n{body}",
            body.len()
        )),
    )
    .await;
    let proxy = tunnel(origin.address, true).await;
    let client = Client::builder()
        .bundle_url(origin.url.as_str())
        .proxy("http://127.0.0.1:1")
        .updates_proxy(UpdatesProxy::Proxy(proxy.url.clone()))
        .trusted_keys(keys.clone())
        .add_root_certificate(origin.pem.as_bytes())
        .unwrap()
        .add_proxy_root_certificate(proxy.pem.as_ref().unwrap().as_bytes())
        .unwrap()
        .build()
        .unwrap();
    assert_eq!(client.refresh().await.unwrap().revision, "2");
    assert_eq!(origin.requests.lock().unwrap().len(), 1);
    assert_eq!(proxy.seen.lock().unwrap().len(), 1);
    #[cfg(feature = "blocking")]
    {
        let origin_url = origin.url.to_string();
        let target_root = origin.pem.clone();
        let proxy_root = proxy.pem.clone().unwrap();
        let proxy_url = proxy.url.clone();
        let thread = std::thread::spawn(move || {
            let client = crate::blocking::Client::builder()
                .bundle_url(origin_url)
                .proxy(proxy_url)
                .trusted_keys(keys)
                .add_root_certificate(target_root.as_bytes())
                .unwrap()
                .add_proxy_root_certificate(proxy_root.as_bytes())
                .unwrap()
                .build()
                .unwrap();
            client.refresh().unwrap().revision
        });
        until(|| thread.is_finished()).await;
        assert_eq!(thread.join().unwrap(), "2");
        assert_eq!(origin.requests.lock().unwrap().len(), 2);
    }
    assert!(origin
        .requests
        .lock()
        .unwrap()
        .iter()
        .all(|request| !request.to_ascii_lowercase().contains("proxy-authorization")));
}
#[tokio::test]
async fn connect_and_tls_proxy_preserve_target_tls() {
    for tls in [false, true] {
        let origin = tls_origin().await;
        let target = origin.address;
        let proxy = tunnel(target, tls).await;
        let (unrelated, _) = tls_config();
        // Preserve the existing PEM-bundle API: the matching root need not be first.
        let target_roots = format!("{unrelated}{}", origin.pem);
        let mut builder = Client::builder()
            .memory_cache()
            .offline(true)
            .proxy(&proxy.url)
            .add_root_certificate(target_roots.as_bytes())
            .unwrap();
        if let Some(pem) = &proxy.pem {
            let proxy_roots = format!("{unrelated}{pem}");
            builder = builder
                .add_proxy_root_certificate(proxy_roots.as_bytes())
                .unwrap();
        }
        builder.origin = Some(origin.url.clone());
        let client = builder.build().unwrap();
        let result = client
            .solve("https://www.google.com/recaptcha/api2/anchor?k=x&co=y")
            .await
            .unwrap();
        assert_eq!(result.token, "tls-token");
        assert_eq!(origin.requests.lock().unwrap().len(), 3);
        {
            let requests = proxy.seen.lock().unwrap();
            assert_eq!(requests.len(), 1);
            assert!(requests[0]
                .to_ascii_lowercase()
                .contains("proxy-authorization: basic dxnlcjpwqhnz"));
            assert!(origin
                .requests
                .lock()
                .unwrap()
                .iter()
                .all(|r| !r.to_ascii_lowercase().contains("proxy-authorization:")));
        }
        // Independent target verification remains enabled, even after proxy TLS succeeds.
        let mut builder = Client::builder()
            .memory_cache()
            .offline(true)
            .proxy(&proxy.url);
        if let Some(pem) = &proxy.pem {
            builder = builder.add_proxy_root_certificate(pem.as_bytes()).unwrap();
        }
        builder.origin = Some(origin.url.clone());
        assert!(builder
            .build()
            .unwrap()
            .solve("https://www.google.com/recaptcha/api2/anchor?k=x&co=y&v=v")
            .await
            .is_err());
    }
}
async fn socks(target: std::net::SocketAddr, allow_auth: bool) -> Tunnel {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = listener.local_addr().unwrap();
    let seen = Arc::new(StdMutex::new(Vec::new()));
    let captured = seen.clone();
    let task = tokio::spawn(async move {
        let mut handlers = tokio::task::JoinSet::new();
        loop {
            let (mut stream, _) = listener.accept().await.unwrap();
            let captured = captured.clone();
            handlers.spawn(async move {
                let mut head = [0; 2];
                if stream.read_exact(&mut head).await.is_err() {
                    return;
                }
                assert_eq!(head[0], 5);
                let mut methods = vec![0; head[1] as usize];
                stream.read_exact(&mut methods).await.unwrap();
                assert!(methods.contains(&2));
                stream.write_all(&[5, 2]).await.unwrap();
                stream.read_exact(&mut head).await.unwrap();
                assert_eq!(head[0], 1);
                let mut user = vec![0; head[1] as usize];
                stream.read_exact(&mut user).await.unwrap();
                let n = stream.read_u8().await.unwrap();
                let mut pass = vec![0; n as usize];
                stream.read_exact(&mut pass).await.unwrap();
                assert_eq!(&user, b"user");
                assert_eq!(&pass, b"p@ss");
                stream.write_all(&[1, u8::from(!allow_auth)]).await.unwrap();
                if !allow_auth {
                    return;
                }
                let mut command = [0; 4];
                stream.read_exact(&mut command).await.unwrap();
                assert_eq!(&command[..3], &[5, 1, 0]);
                let destination = match command[3] {
                    1 => {
                        let mut ip = [0; 4];
                        stream.read_exact(&mut ip).await.unwrap();
                        std::net::Ipv4Addr::from(ip).to_string()
                    }
                    4 => {
                        let mut ip = [0; 16];
                        stream.read_exact(&mut ip).await.unwrap();
                        std::net::Ipv6Addr::from(ip).to_string()
                    }
                    3 => {
                        let n = stream.read_u8().await.unwrap();
                        let mut host = vec![0; n as usize];
                        stream.read_exact(&mut host).await.unwrap();
                        String::from_utf8(host).unwrap()
                    }
                    _ => panic!("invalid SOCKS address"),
                };
                let _port = stream.read_u16().await.unwrap();
                captured.lock().unwrap().push(destination);
                let mut upstream = tokio::net::TcpStream::connect(target).await.unwrap();
                stream
                    .write_all(&[5, 0, 0, 1, 127, 0, 0, 1, 0, 0])
                    .await
                    .unwrap();
                let _ = tokio::io::copy_bidirectional(&mut stream, &mut upstream).await;
            });
            while handlers.try_join_next().is_some() {}
        }
    });
    Tunnel {
        url: format!("socks5h://user:p%40ss@127.0.0.1:{}", addr.port()),
        pem: None,
        seen,
        task,
    }
}
#[tokio::test]
async fn socks_dns_auth_and_no_fallback() {
    for local in [false, true] {
        let origin = tls_origin().await;
        let target = origin.address;
        let proxy = socks(target, true).await;
        let proxy_url = if local {
            proxy.url.replace("socks5h:", "socks5:")
        } else {
            proxy.url.clone()
        };
        let mut builder = Client::builder()
            .memory_cache()
            .offline(true)
            .proxy(proxy_url)
            .add_root_certificate(origin.pem.as_bytes())
            .unwrap();
        builder.origin = Some(origin.url.clone());
        builder
            .build()
            .unwrap()
            .solve("https://www.google.com/recaptcha/api2/anchor?k=x&co=y&v=v")
            .await
            .unwrap();
        let seen = proxy.seen.lock().unwrap();
        assert_eq!(seen.len(), 1);
        if local {
            assert!(seen[0].parse::<std::net::IpAddr>().is_ok());
        } else {
            assert_eq!(seen[0], "localhost");
        }
        drop(seen);
    }
    let origin = tls_origin().await;
    let proxy = socks(origin.address, true).await;
    let mut remote = origin.url.clone();
    remote.set_host(Some("only-proxy.invalid")).unwrap();
    let mut builder = Client::builder()
        .memory_cache()
        .offline(true)
        .proxy(&proxy.url)
        .add_root_certificate(origin.pem.as_bytes())
        .unwrap();
    builder.origin = Some(remote.clone());
    builder
        .clone()
        .build()
        .unwrap()
        .solve("https://www.google.com/recaptcha/api2/anchor?k=x&co=y&v=v")
        .await
        .unwrap();
    assert_eq!(proxy.seen.lock().unwrap()[0], "only-proxy.invalid");
    builder.proxy = Some(proxy.url.replace("socks5h:", "socks5:"));
    let count = origin.requests.lock().unwrap().len();
    assert!(builder
        .build()
        .unwrap()
        .solve("https://www.google.com/recaptcha/api2/anchor?k=x&co=y&v=v")
        .await
        .is_err());
    assert_eq!(origin.requests.lock().unwrap().len(), count);
    let bad = socks(origin.address, false).await;
    let mut builder = Client::builder()
        .memory_cache()
        .offline(true)
        .proxy(&bad.url)
        .add_root_certificate(origin.pem.as_bytes())
        .unwrap();
    builder.origin = Some(origin.url.clone());
    assert!(builder
        .build()
        .unwrap()
        .solve("https://www.google.com/recaptcha/api2/anchor?k=x&co=y&v=v")
        .await
        .is_err());
    assert!(bad.seen.lock().unwrap().is_empty());
}

#[test]
fn extract_action_is_pure_and_validated() {
    assert_eq!(
        crate::extract_action(
            "https://www.google.com/recaptcha/api2/anchor?k=x&co=y&sa=hello%2Bworld"
        )
        .unwrap(),
        Some("hello+world".to_owned())
    );
    assert_eq!(
        crate::extract_action("https://www.google.com/recaptcha/api2/anchor?k=x&co=y").unwrap(),
        None
    );
    assert!(crate::extract_action(
        "https://www.google.com/recaptcha/api2/anchor?k=x&co=y&sa=a&sa=b"
    )
    .is_err());
}
#[tokio::test]
async fn bg_override_is_bounded_and_operation_local() {
    let origin = server(
        vec![
            (
                200,
                "/recaptcha/releases/current-version/recaptcha.js".into(),
            ),
            (200, "<input id='recaptcha-token' value='challenge'>".into()),
            (200, "[\"rresp\",\"first\"]".into()),
            (
                200,
                "/recaptcha/releases/current-version/recaptcha.js".into(),
            ),
            (200, "<input id='recaptcha-token' value='challenge'>".into()),
            (200, "[\"rresp\",\"second\"]".into()),
        ],
        Duration::ZERO,
    )
    .await;
    let client = builder(&origin).build().unwrap();
    let original = client.load_bg().await.unwrap();
    assert!(!original.is_empty());
    let url = "https://www.google.com/recaptcha/api2/anchor?k=x&co=y&v=v&sa=url";
    for invalid in ["".to_owned(), "x".repeat(16385)] {
        assert_eq!(
            client
                .solve_with_options(
                    url,
                    SolveOptions {
                        bg: Some(&invalid),
                        action: None,
                        user_agent: None,
                        hostname: None,
                    }
                )
                .await
                .unwrap_err()
                .kind,
            ErrorKind::InvalidInput
        );
    }
    assert!(origin.requests.lock().unwrap().is_empty());
    client
        .solve_with_options(
            url,
            SolveOptions {
                bg: Some("custom +&=💫"),
                action: Some("override"),
                user_agent: None,
                hostname: None,
            },
        )
        .await
        .unwrap();
    client.solve(url).await.unwrap();
    assert_eq!(client.load_bg().await.unwrap(), original);
    let requests = origin.requests.lock().unwrap();
    let fields = |i: usize| -> BTreeMap<String, String> {
        url::form_urlencoded::parse(requests[i].split("\r\n\r\n").nth(1).unwrap().as_bytes())
            .into_owned()
            .collect()
    };
    assert_eq!(fields(2)["bg"], "custom +&=💫");
    assert_eq!(fields(2)["sa"], "override");
    assert_eq!(fields(5)["bg"], original);
    assert_eq!(fields(5)["sa"], "url");
}

#[tokio::test]
async fn persistent_cache_equivocation_expiry_and_lock_deadline() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("bundle.json");
    let now = bundle::now().unwrap();
    let (bytes, keys) = signed("2", now + 600);
    let first = Bundle::verify(&bytes, &keys).unwrap();
    cache::accept(&path, &first, &keys).await.unwrap();
    let (bytes, _) = signed("2", now + 601);
    let equivocation = Bundle::verify(&bytes, &keys).unwrap();
    assert_eq!(
        cache::accept(&path, &equivocation, &keys)
            .await
            .err()
            .unwrap()
            .kind,
        ErrorKind::ConfigInvalid
    );
    assert_eq!(tokio::fs::read(&path).await.unwrap(), first.envelope);
    let (expired, keys) = signed("3", now);
    tokio::fs::write(&path, expired).await.unwrap();
    assert_eq!(
        Client::builder()
            .trusted_keys(keys)
            .cache_path(&path)
            .offline(true)
            .pin("3")
            .build()
            .unwrap()
            .prepare()
            .await
            .unwrap_err()
            .kind,
        ErrorKind::ConfigExpired
    );
    let lock_path = dir.path().join("blocked.json.lock");
    let lock = std::fs::File::create(lock_path).unwrap();
    fs2::FileExt::try_lock_exclusive(&lock).unwrap();
    let client = Client::builder()
        .cache_path(dir.path().join("blocked.json"))
        // Recovery also performs real file and directory syncs on shared CI runners.
        .timeout(Duration::from_secs(2))
        .offline(true)
        .build()
        .unwrap();
    assert_eq!(
        client.prepare().await.unwrap_err().kind,
        ErrorKind::DeadlineExceeded
    );
    assert_eq!(client.inner.permits.available_permits(), 16);
    drop(lock);
    assert_eq!(client.prepare().await.unwrap().revision, "1");
}
#[tokio::test]
async fn concurrent_cache_writers_keep_highest_revision() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("bundle.json");
    let (a, keys) = signed("2", bundle::now().unwrap() + 600);
    let (b, _) = signed("3", bundle::now().unwrap() + 600);
    let a = Bundle::verify(&a, &keys).unwrap();
    let b = Bundle::verify(&b, &keys).unwrap();
    let (ar, br) = tokio::join!(
        cache::accept(&path, &a, &keys),
        cache::accept(&path, &b, &keys)
    );
    if let Err(error) = ar {
        assert_eq!(error.kind, ErrorKind::ConfigRollback);
    }
    assert!(br.is_ok());
    assert_eq!(
        cache::load(&path, &keys)
            .await
            .unwrap()
            .unwrap()
            .payload
            .revision,
        "3"
    );
}
#[tokio::test]
async fn streamed_body_limit_and_slow_drip_total_deadline() {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = listener.local_addr().unwrap();
    let task = tokio::spawn(async move {
        let (mut stream, _) = listener.accept().await.unwrap();
        let mut head = Vec::new();
        let mut byte = [0];
        while !head.ends_with(b"\r\n\r\n") {
            stream.read_exact(&mut byte).await.unwrap();
            head.push(byte[0]);
        }
        stream
            .write_all(b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n500\r\n")
            .await
            .unwrap();
        let _ = stream.write_all(&[b'x'; 1280]).await;
    });
    let mut builder = Client::builder()
        .memory_cache()
        .offline(true)
        .max_response_bytes(1024);
    builder.origin = Some(Url::parse(&format!("http://{addr}/")).unwrap());
    assert_eq!(
        builder
            .build()
            .unwrap()
            .solve("https://www.google.com/recaptcha/api2/anchor?k=x&co=y&v=v")
            .await
            .unwrap_err()
            .kind,
        ErrorKind::ResourceLimit
    );
    task.await.unwrap();
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = listener.local_addr().unwrap();
    let task = tokio::spawn(async move {
        let (mut stream, _) = listener.accept().await.unwrap();
        let mut head = Vec::new();
        let mut byte = [0];
        while !head.ends_with(b"\r\n\r\n") {
            stream.read_exact(&mut byte).await.unwrap();
            head.push(byte[0]);
        }
        stream
            .write_all(b"HTTP/1.1 200 OK\r\nContent-Length: 100\r\n\r\n")
            .await
            .unwrap();
        for _ in 0..100 {
            if stream.write_all(b"x").await.is_err() {
                return;
            }
            tokio::time::sleep(Duration::from_millis(10)).await;
        }
    });
    let mut builder = Client::builder()
        .memory_cache()
        .offline(true)
        .timeout(Duration::from_millis(50));
    builder.origin = Some(Url::parse(&format!("http://{addr}/")).unwrap());
    assert_eq!(
        builder
            .build()
            .unwrap()
            .solve("https://www.google.com/recaptcha/api2/anchor?k=x&co=y&v=v")
            .await
            .unwrap_err()
            .kind,
        ErrorKind::DeadlineExceeded
    );
    task.abort();
    let result = task.await;
    assert!(result.is_err_and(|error| error.is_cancelled()));
}

#[test]
fn proxy_raw_paths_are_rejected() {
    for path in ["/forbidden/..", "/./", "/%2e%2e", "/a/../", "\\\\."] {
        assert_eq!(
            proxy::parse(&format!("http://localhost:80{path}"))
                .err()
                .unwrap()
                .kind,
            ErrorKind::InvalidProxy
        );
    }
}
#[tokio::test]
async fn proxy_credentials_are_decoded_once() {
    let proxy = server(
        vec![
            (
                200,
                "/recaptcha/releases/current-version/recaptcha.js".into(),
            ),
            (200, "<input id='recaptcha-token' value='challenge'>".into()),
            (200, "[\"rresp\",\"token\"]".into()),
        ],
        Duration::ZERO,
    )
    .await;
    let direct = server(vec![], Duration::ZERO).await;
    let proxy_url = format!(
        "http://u%2540ser:p%2540ss@{}",
        proxy.url.socket_addrs(|| None).unwrap()[0]
    );
    builder(&direct)
        .proxy(proxy_url)
        .build()
        .unwrap()
        .solve("https://www.google.com/recaptcha/api2/anchor?k=x&co=y&v=v")
        .await
        .unwrap();
    let expected = format!(
        "proxy-authorization: basic {}",
        STANDARD.encode("u%40ser:p%40ss")
    )
    .to_ascii_lowercase();
    assert!(proxy
        .requests
        .lock()
        .unwrap()
        .iter()
        .all(|request| request.to_ascii_lowercase().contains(&expected)));
}
#[tokio::test]
async fn initialized_clients_observe_persisted_highwater() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("bundle.json");
    let (new, keys) = signed("2", bundle::now().unwrap() + 600);
    let pinned = Client::builder()
        .cache_path(&path)
        .trusted_keys(keys.clone())
        .offline(true)
        .pin("1")
        .build()
        .unwrap();
    let automatic = Client::builder()
        .cache_path(&path)
        .trusted_keys(keys.clone())
        .offline(true)
        .build()
        .unwrap();
    assert_eq!(pinned.prepare().await.unwrap().revision, "1");
    assert_eq!(automatic.prepare().await.unwrap().revision, "1");
    let updates = server(vec![(200, String::from_utf8(new).unwrap())], Duration::ZERO).await;
    Client::builder()
        .cache_path(&path)
        .trusted_keys(keys)
        .bundle_url(updates.url.to_string())
        .build()
        .unwrap()
        .refresh()
        .await
        .unwrap();
    assert_eq!(
        pinned.prepare().await.unwrap_err().kind,
        ErrorKind::ConfigRollback
    );
    assert_eq!(automatic.prepare().await.unwrap().revision, "2");
}
#[cfg(all(unix, feature = "blocking"))]
#[test]
fn nonregular_cache_files_do_not_leave_blocking_io() {
    let dir = tempfile::tempdir().unwrap();
    for suffix in ["cache.json", "lock.json.lock"] {
        let fifo = dir.path().join(suffix);
        let path = std::ffi::CString::new(fifo.as_os_str().as_encoded_bytes()).unwrap();
        // SAFETY: the owned CString remains valid throughout the syscall.
        assert_eq!(unsafe { libc::mkfifo(path.as_ptr(), 0o600) }, 0);
        let cache = if suffix.ends_with(".lock") {
            dir.path().join("lock.json")
        } else {
            fifo
        };
        let client = crate::blocking::Client::builder()
            .cache_path(cache)
            .offline(true)
            .timeout(Duration::from_millis(50))
            .build()
            .unwrap();
        let start = Instant::now();
        assert_eq!(
            client.prepare().unwrap_err().kind,
            ErrorKind::ConfigUnavailable
        );
        drop(client);
        assert!(start.elapsed() < Duration::from_secs(1));
    }
    let original = dir.path().join("original.json");
    std::fs::write(&original, include_bytes!("../data/bundle.json")).unwrap();
    let link = dir.path().join("symlink.json");
    std::os::unix::fs::symlink(original, &link).unwrap();
    let client = crate::blocking::Client::builder()
        .cache_path(link)
        .offline(true)
        .build()
        .unwrap();
    assert_eq!(
        client.prepare().unwrap_err().kind,
        ErrorKind::ConfigUnavailable
    );
    drop(client);
}

#[tokio::test]
async fn default_cache_mode_is_memory_without_implicit_paths() {
    let client = Client::builder().offline(true).build().unwrap();
    assert!(!client.prepare().await.unwrap().persisted);
    assert!(client.inner.cache_path.is_none());
}
#[test]
fn anchor_raw_paths_and_empty_credentials_are_rejected() {
    for url in [
        "https://www.google.com/recaptcha/api2/anything/../anchor?k=x&co=y",
        "https://www.google.com/recaptcha/api2/./anchor?k=x&co=y",
        "https://@www.google.com/recaptcha/api2/anchor?k=x&co=y",
        "https://www.google.com/recaptcha/api2/anchor?k=x&co=%FF",
    ] {
        assert!(core::parse_anchor(url, None).is_err(), "{url}");
    }
}

#[tokio::test]
async fn separate_clients_share_one_update_download() {
    let (bytes, keys) = signed("2", bundle::now().unwrap() + 600);
    let body = String::from_utf8(bytes).unwrap();
    let updates = server(
        vec![(200, body.clone()), (200, body)],
        Duration::from_millis(25),
    )
    .await;
    let make = || {
        Client::builder()
            .bundle_url(updates.url.to_string())
            .trusted_keys(keys.clone())
            .build()
            .unwrap()
    };
    let first = make();
    let second = make();
    let (a, b) = tokio::join!(first.refresh(), second.refresh());
    assert_eq!(a.unwrap().revision, "2");
    assert_eq!(b.unwrap().revision, "2");
    assert_eq!(
        updates.requests.lock().unwrap().len(),
        1,
        "a channel refresh must be shared across separately built clients"
    );
}

async fn until(mut condition: impl FnMut() -> bool) {
    tokio::time::timeout(Duration::from_secs(3), async {
        while !condition() {
            tokio::time::sleep(Duration::from_millis(1)).await;
        }
    })
    .await
    .expect("condition did not become ready before deadline");
}
#[tokio::test]
async fn shared_updates_isolate_trust_and_pin_acceptance() {
    let (bytes, keys) = signed("2", bundle::now().unwrap() + 600);
    let updates = server(
        vec![(200, String::from_utf8(bytes).unwrap())],
        Duration::from_millis(50),
    )
    .await;
    let base = Client::builder().bundle_url(updates.url.to_string());
    let trusted = base.clone().trusted_keys(keys.clone()).build().unwrap();
    let untrusted = base.clone().build().unwrap();
    let pinned = base.trusted_keys(keys).pin("3").build().unwrap();
    let (a, b, c) = tokio::join!(trusted.refresh(), untrusted.refresh(), pinned.refresh());
    assert_eq!(a.unwrap().revision, "2");
    assert_eq!(b.unwrap_err().kind, ErrorKind::ConfigInvalid);
    assert_eq!(c.unwrap_err().kind, ErrorKind::ConfigUnavailable);
    assert_eq!(updates.requests.lock().unwrap().len(), 1);
}
#[tokio::test]
async fn shared_update_waiters_keep_independent_deadlines() {
    let (bytes, keys) = signed("2", bundle::now().unwrap() + 600);
    let updates = server(
        vec![(200, String::from_utf8(bytes).unwrap())],
        Duration::from_millis(150),
    )
    .await;
    let base = Client::builder()
        .bundle_url(updates.url.to_string())
        .trusted_keys(keys);
    let short = base
        .clone()
        .timeout(Duration::from_millis(50))
        .build()
        .unwrap();
    let long = base.timeout(Duration::from_secs(2)).build().unwrap();
    let (a, b) = tokio::join!(short.refresh(), long.refresh());
    assert_eq!(a.unwrap_err().kind, ErrorKind::DeadlineExceeded);
    assert_eq!(b.unwrap().revision, "2");
    assert_eq!(updates.requests.lock().unwrap().len(), 1);
}
#[tokio::test]
async fn cancelling_last_shared_waiter_drains_worker() {
    let (bytes, keys) = signed("2", bundle::now().unwrap() + 600);
    let updates = server(
        vec![(200, String::from_utf8(bytes).unwrap())],
        Duration::from_secs(2),
    )
    .await;
    let client = Client::builder()
        .bundle_url(updates.url.to_string())
        .trusted_keys(keys)
        .build()
        .unwrap();
    let config = client.inner.update_config.clone().unwrap();
    let mut future = Box::pin(client.refresh());
    tokio::select! { result = &mut future => panic!("unexpected early result {result:?}"), _ = until(|| updates.requests.lock().unwrap().len() == 1) => {} }
    drop(future);
    until(|| !shared::present(&config)).await;
    assert_eq!(client.inner.permits.available_permits(), 16);
}
#[cfg(feature = "blocking")]
#[tokio::test]
async fn shared_update_survives_subscriber_runtime_shutdown() {
    let (bytes, keys) = signed("2", bundle::now().unwrap() + 600);
    let updates = server(
        vec![(200, String::from_utf8(bytes).unwrap())],
        Duration::from_millis(200),
    )
    .await;
    let base = Client::builder()
        .bundle_url(updates.url.to_string())
        .trusted_keys(keys.clone());
    let config = base
        .clone()
        .build()
        .unwrap()
        .inner
        .update_config
        .clone()
        .unwrap();
    let (cancel, mut cancellation) = tokio::sync::watch::channel(false);
    let first = std::thread::spawn(move || {
        let rt = tokio::runtime::Builder::new_current_thread()
            .enable_all()
            .build()
            .unwrap();
        let client = base.build().unwrap();
        rt.block_on(async {tokio::select!{r=client.refresh()=>panic!("unexpected result before cancellation {r:?}"),_=cancellation.changed()=>{}}});
        drop(client);
        drop(rt);
    });
    until(|| shared::subscribers(&config) == 1).await;
    let url = updates.url.to_string();
    let second = std::thread::spawn(move || {
        let client = crate::blocking::Client::builder()
            .bundle_url(url)
            .trusted_keys(keys)
            .build()
            .unwrap();
        client.refresh().unwrap().revision
    });
    until(|| shared::subscribers(&config) == 2).await;
    cancel.send_replace(true);
    until(|| first.is_finished()).await;
    first.join().unwrap();
    until(|| second.is_finished()).await;
    assert_eq!(second.join().unwrap(), "2");
    assert_eq!(updates.requests.lock().unwrap().len(), 1);
    until(|| !shared::present(&config)).await;
}
#[tokio::test]
async fn different_update_tls_roots_and_timeouts_do_not_share() {
    for policy in ["target", "proxy", "timeout"] {
        let (bytes, keys) = signed("2", bundle::now().unwrap() + 600);
        let body = String::from_utf8(bytes).unwrap();
        let updates = server(
            vec![(200, body.clone()), (200, body)],
            Duration::from_millis(25),
        )
        .await;
        let base = Client::builder()
            .bundle_url(updates.url.to_string())
            .trusted_keys(keys);
        let first = base.clone().build().unwrap();
        let second = if policy == "timeout" {
            base.update_timeout(Duration::from_secs(2)).build().unwrap()
        } else {
            let (pem, _) = tls_config();
            (if policy == "proxy" {
                base.add_proxy_root_certificate(pem.as_bytes())
            } else {
                base.add_root_certificate(pem.as_bytes())
            })
            .unwrap()
            .build()
            .unwrap()
        };
        let (a, b) = tokio::join!(first.refresh(), second.refresh());
        a.unwrap();
        b.unwrap();
        assert_eq!(updates.requests.lock().unwrap().len(), 2);
    }
}
#[tokio::test]
#[ignore = "run alone to exhaust the process-wide coordinator without interfering with other tests"]
async fn process_refresh_registry_limit_and_cleanup() {
    assert_eq!(shared::active_workers(), 0);
    let (bytes, keys) = signed("2", bundle::now().unwrap() + 600);
    let body = String::from_utf8(bytes).unwrap();
    let mut servers = Vec::new();
    let mut pending = tokio::task::JoinSet::new();
    for _ in 0..16 {
        let updates = server(vec![(200, body.clone())], Duration::from_secs(10)).await;
        let client = Client::builder()
            .bundle_url(updates.url.to_string())
            .trusted_keys(keys.clone())
            .build()
            .unwrap();
        pending.spawn(async move { client.refresh().await });
        until(|| updates.requests.lock().unwrap().len() == 1).await;
        servers.push(updates);
    }
    assert_eq!(shared::active_workers(), 16);
    let overflow = server(vec![(200, body)], Duration::ZERO).await;
    let client = Client::builder()
        .bundle_url(overflow.url.to_string())
        .trusted_keys(keys)
        .build()
        .unwrap();
    assert_eq!(
        client.refresh().await.unwrap_err().kind,
        ErrorKind::ResourceLimit
    );
    assert!(overflow.requests.lock().unwrap().is_empty());
    pending.abort_all();
    while let Some(result) = pending.join_next().await {
        assert!(result.is_err_and(|error| error.is_cancelled()));
    }
    until(|| shared::active_workers() == 0).await;
    drop(servers);
}
#[tokio::test]
async fn selected_actions_are_nonempty_and_bounded_before_network() {
    let origin = server(
        vec![
            (
                200,
                "/recaptcha/releases/current-version/recaptcha.js".into(),
            ),
            (
                200,
                "<input id='recaptcha-token' value='challenge'>".to_owned(),
            ),
            (200, "[\"rresp\",\"token\"]".to_owned()),
        ],
        Duration::ZERO,
    )
    .await;
    let client = builder(&origin).build().unwrap();
    let url = "https://www.google.com/recaptcha/api2/anchor?k=x&co=y&v=v";
    for action in ["".to_owned(), "x".repeat(257), "💫".repeat(65)] {
        assert_eq!(
            client
                .solve_with_action(url, &action)
                .await
                .unwrap_err()
                .kind,
            ErrorKind::InvalidInput
        );
    }
    assert_eq!(
        client.solve(&format!("{url}&sa=")).await.unwrap_err().kind,
        ErrorKind::InvalidInput
    );
    assert!(origin.requests.lock().unwrap().is_empty());
    client
        .solve_with_action(&format!("{url}&sa="), "override")
        .await
        .unwrap();
    assert_eq!(origin.requests.lock().unwrap().len(), 3);
}

#[test]
fn canonical_bundle_revision_timestamp_and_field_boundaries() {
    let (bytes, keys) = signed("2", bundle::now().unwrap() + 600);
    let envelope: serde_json::Value = serde_json::from_slice(&bytes).unwrap();
    let payload_bytes = STANDARD
        .decode(envelope["payload"].as_str().unwrap())
        .unwrap();
    let original: serde_json::Value = serde_json::from_slice(&payload_bytes).unwrap();
    let verify = |value: &serde_json::Value| {
        let (bytes, keys) = sign_bytes(serde_json::to_vec(value).unwrap());
        Bundle::verify(&bytes, &keys)
    };
    for revision in ["0", "01", "100000000000000000000"] {
        let mut value = original.clone();
        value["revision"] = json!(revision);
        assert!(verify(&value).is_err());
    }
    let mut value = original.clone();
    value["revision"] = json!("99999999999999999999");
    value["issued_at"] = json!(0);
    value["expires_at"] = json!(9_007_199_254_740_991u64);
    assert!(verify(&value).is_ok());
    value["expires_at"] = json!(9_007_199_254_740_992u64);
    assert!(verify(&value).is_err());
    for field in ["reason", "hl", "size", "chr", "vh", "bg"] {
        let mut value = original.clone();
        value["fields"][field] = json!("x".repeat(16384));
        assert!(verify(&value).is_ok(), "{field}");
        value["fields"][field] = json!("x".repeat(16385));
        assert!(verify(&value).is_err(), "{field}");
        value["fields"][field] = json!("\0\n\t");
        assert!(verify(&value).is_ok(), "{field}");
    }
    let mut value = original;
    let unicode = "💫".repeat(16384 / "💫".len());
    value["fields"]["bg"] = json!(unicode);
    assert!(verify(&value).is_ok());
    assert!(Bundle::verify(&bytes, &keys).is_ok());
}
#[test]
fn challenge_decodes_html_attributes_and_entities() {
    assert_eq!(
        core::challenge("<input value='anchor&amp;token' id='recaptcha-token'>").unwrap(),
        "anchor&token"
    );
    assert_eq!(
        core::challenge("<input ID=recaptcha-token value=anchor&#38;token>").unwrap(),
        "anchor&token"
    );
    assert_eq!(
        core::challenge("<input value=\"a'b&gt;c&#x26;d&copy;\" id='recaptcha&#45;token'>")
            .unwrap(),
        "a'b>c&d©"
    );
    assert_eq!(
        core::challenge("<input id='recaptcha-token' value='a>b'>").unwrap(),
        "a>b"
    );
}
#[tokio::test]
async fn html_challenge_and_control_text_are_form_encoded() {
    let origin = server(
        vec![
            (
                200,
                "/recaptcha/releases/current-version/recaptcha.js".into(),
            ),
            (
                200,
                "<input value='anchor&amp;token' id='recaptcha-token'>".to_owned(),
            ),
            (200, "[\"rresp\",\"token\"]".to_owned()),
        ],
        Duration::ZERO,
    )
    .await;
    let client = builder(&origin).build().unwrap();
    client
        .solve_with_options(
            "https://www.google.com/recaptcha/api2/anchor?k=x&co=y&v=v",
            SolveOptions {
                bg: Some("bg\0\n"),
                action: Some("action\0\n"),
                user_agent: None,
                hostname: None,
            },
        )
        .await
        .unwrap();
    let requests = origin.requests.lock().unwrap();
    let fields: BTreeMap<_, _> =
        url::form_urlencoded::parse(requests[2].split("\r\n\r\n").nth(1).unwrap().as_bytes())
            .into_owned()
            .collect();
    assert_eq!(fields["c"], "anchor&token");
    assert_eq!(fields["bg"], "bg\0\n");
    assert_eq!(fields["sa"], "action\0\n");
    assert!(requests[1].contains("sa=action%00%0A"));
}

#[tokio::test]
async fn different_update_proxy_routes_do_not_share_raw_bytes() {
    let (direct_bytes, keys) = signed("2", bundle::now().unwrap() + 600);
    let (proxy_bytes, _) = signed("3", bundle::now().unwrap() + 600);
    let direct = server(
        vec![(200, String::from_utf8(direct_bytes).unwrap())],
        Duration::from_millis(25),
    )
    .await;
    let proxy = server(
        vec![(200, String::from_utf8(proxy_bytes).unwrap())],
        Duration::from_millis(25),
    )
    .await;
    let base = Client::builder()
        .bundle_url(direct.url.to_string())
        .trusted_keys(keys);
    let first = base.clone().build().unwrap();
    let second = base
        .updates_proxy(UpdatesProxy::Proxy(proxy.url.to_string()))
        .build()
        .unwrap();
    let (a, b) = tokio::join!(first.refresh(), second.refresh());
    assert_eq!(a.unwrap().revision, "2");
    assert_eq!(b.unwrap().revision, "3");
    assert_eq!(direct.requests.lock().unwrap().len(), 1);
    assert_eq!(proxy.requests.lock().unwrap().len(), 1);
}
#[tokio::test]
async fn invalid_selected_text_fails_before_update_download() {
    let (bytes, keys) = signed("2", bundle::now().unwrap() + 600);
    let updates = server(
        vec![(200, String::from_utf8(bytes).unwrap())],
        Duration::ZERO,
    )
    .await;
    let origin = server(vec![], Duration::ZERO).await;
    let client = builder(&origin)
        .offline(false)
        .bundle_url(updates.url.to_string())
        .trusted_keys(keys)
        .build()
        .unwrap();
    let url = "https://www.google.com/recaptcha/api2/anchor?k=x&co=y&v=v";
    let overlong_bg = "x".repeat(16385);
    let overlong_action = "x".repeat(257);
    for options in [
        SolveOptions {
            bg: Some(""),
            action: None,
            user_agent: None,
            hostname: None,
        },
        SolveOptions {
            bg: Some(&overlong_bg),
            action: None,
            user_agent: None,
            hostname: None,
        },
        SolveOptions {
            bg: None,
            action: Some(""),
            user_agent: None,
            hostname: None,
        },
        SolveOptions {
            bg: None,
            action: Some(&overlong_action),
            user_agent: None,
            hostname: None,
        },
    ] {
        assert_eq!(
            client
                .solve_with_options(url, options)
                .await
                .unwrap_err()
                .kind,
            ErrorKind::InvalidInput
        );
    }
    assert_eq!(
        client.solve(&format!("{url}&sa=")).await.unwrap_err().kind,
        ErrorKind::InvalidInput
    );
    assert!(updates.requests.lock().unwrap().is_empty());
    assert!(origin.requests.lock().unwrap().is_empty());
    assert!(
        core::parse_anchor(&format!("{url}&sa="), Some(&"💫".repeat(256 / "💫".len()))).is_ok()
    );
}

#[test]
fn html5_attribute_numeric_legacy_and_context_semantics() {
    for (input, expected) in [
        ("a&#x80;b", "a€b"),
        ("a&#0;b", "a�b"),
        ("a&copy b", "a© b"),
        ("a&copy=tail", "a&copy=tail"),
        ("a&copytail", "a&copytail"),
        ("a&#xD800;b", "a�b"),
        ("a&#x110000;b", "a�b"),
        ("a&notit;b", "a&notit;b"),
    ] {
        assert_eq!(
            core::challenge(&format!("<input id=recaptcha-token value='{input}'>")).unwrap(),
            expected
        );
    }
    let html="<!-- <input id=recaptcha-token value=fake-comment> --><script>const x=\"<input id=recaptcha-token value=fake-script>\";</script><textarea><input id=recaptcha-token value=fake-text></textarea><input id=recaptcha-token value=actual>";
    assert_eq!(core::challenge(html).unwrap(), "actual");
}
#[test]
fn pure_extraction_preserves_empty_sa() {
    assert_eq!(
        crate::extract_action("https://www.google.com/recaptcha/api2/anchor?k=x&co=y&sa=").unwrap(),
        Some("".to_owned())
    );
    assert!(core::parse_anchor(
        "https://www.google.com/recaptcha/api2/anchor?k=x&co=y&sa=",
        None
    )
    .is_err());
}

#[path = "review_tests.rs"]
mod review_tests;

#[tokio::test]
async fn user_agent_per_operation_and_no_default_leakage() {
    let responses = || {
        vec![
            (
                200,
                "/recaptcha/releases/current-version/recaptcha.js".into(),
            ),
            (200, "<input id='recaptcha-token' value='anchor'>".into()),
            (200, "[\"rresp\",\"fixture-token\"]".into()),
        ]
    };
    let origin = server(
        [responses(), responses(), responses()].concat(),
        Duration::ZERO,
    )
    .await;
    let client = builder(&origin).build().unwrap();
    let url = "https://www.google.com/recaptcha/api2/anchor?k=x&co=y";
    for user_agent in [Some("first/1"), Some("second/2"), None] {
        client
            .solve_with_options(
                url,
                SolveOptions {
                    user_agent,
                    ..Default::default()
                },
            )
            .await
            .unwrap();
    }
    let requests = origin.requests.lock().unwrap();
    for (index, request) in requests.iter().enumerate() {
        let header = request
            .lines()
            .find(|line| line.to_ascii_lowercase().starts_with("user-agent:"));
        let expected = if index < 3 {
            Some("user-agent: first/1")
        } else if index < 6 {
            Some("user-agent: second/2")
        } else {
            None
        };
        assert_eq!(header.map(str::to_ascii_lowercase).as_deref(), expected);
    }
}

#[tokio::test]
async fn invalid_user_agent_fails_before_update_download() {
    let updates = server(vec![], Duration::ZERO).await;
    let origin = server(vec![], Duration::ZERO).await;
    let client = builder(&origin)
        .offline(false)
        .bundle_url(updates.url.to_string())
        .build()
        .unwrap();
    let overlong = "a".repeat(1025);
    for user_agent in [
        "",
        " ",
        "\t",
        "a\rb",
        "a\nb",
        "a\0b",
        "a\u{7f}b",
        "привет",
        &overlong,
    ] {
        let error = client
            .solve_with_options(
                "https://www.google.com/recaptcha/api2/anchor?k=x&co=y",
                SolveOptions {
                    user_agent: Some(user_agent),
                    ..Default::default()
                },
            )
            .await
            .unwrap_err();
        assert_eq!(error.kind, ErrorKind::InvalidInput);
        assert_eq!(error.stage, "overrides");
    }
    assert!(origin.requests.lock().unwrap().is_empty());
    assert!(updates.requests.lock().unwrap().is_empty());
}

#[tokio::test]
async fn hostname_per_operation_and_legacy_co_preservation() {
    let response = || {
        vec![
            (
                200,
                "/recaptcha/releases/current-version/recaptcha.js".into(),
            ),
            (200, "<input id='recaptcha-token' value='anchor'>".into()),
            (200, "[\"rresp\",\"fixture-token\"]".into()),
        ]
    };
    let origin = server(
        [response(), response(), response()].concat(),
        Duration::ZERO,
    )
    .await;
    let client = builder(&origin).build().unwrap();
    let encoded = "aHR0cHM6Ly9vbGQuZXhhbXBsZTo4NDQz";
    let url = format!("https://www.google.com/recaptcha/api2/anchor?k=x&co={encoded}");
    client
        .solve_with_options(
            &url,
            SolveOptions {
                hostname: Some("NEW.EXAMPLE"),
                ..Default::default()
            },
        )
        .await
        .unwrap();
    client.solve(&url).await.unwrap();
    client
        .solve("https://www.google.com/recaptcha/api2/anchor?k=x&co=legacy")
        .await
        .unwrap();
    let expected = crate::hostname::origin(encoded, "new.example").unwrap();
    let requests = origin.requests.lock().unwrap();
    for (offset, expected) in [(0, expected.as_str()), (3, encoded), (6, "legacy")] {
        let target = requests[offset + 1]
            .lines()
            .next()
            .unwrap()
            .split(' ')
            .nth(1)
            .unwrap();
        let parsed = Url::parse(&format!("http://localhost{target}")).unwrap();
        assert_eq!(
            parsed.query_pairs().find(|(key, _)| key == "co").unwrap().1,
            expected
        );
        let fields: BTreeMap<_, _> = url::form_urlencoded::parse(
            requests[offset + 2]
                .split("\r\n\r\n")
                .nth(1)
                .unwrap()
                .as_bytes(),
        )
        .into_owned()
        .collect();
        assert_eq!(fields["co"], expected);
    }
}

#[tokio::test]
async fn invalid_hostname_fails_before_update_download() {
    let updates = server(vec![], Duration::ZERO).await;
    let origin = server(vec![], Duration::ZERO).await;
    let client = builder(&origin)
        .offline(false)
        .bundle_url(updates.url.to_string())
        .build()
        .unwrap();
    let vectors: serde_json::Value =
        serde_json::from_str(include_str!("../tests/data/solve-options.json")).unwrap();
    for vector in vectors["hostname"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|vector| vector["valid"] == false)
    {
        let co: String =
            url::form_urlencoded::byte_serialize(vector["co"].as_str().unwrap().as_bytes())
                .collect();
        let input = format!("https://www.google.com/recaptcha/api2/anchor?k=x&co={co}");
        let error = client
            .solve_with_options(
                &input,
                SolveOptions {
                    hostname: vector["hostname"].as_str(),
                    ..Default::default()
                },
            )
            .await
            .unwrap_err();
        assert_eq!(error.kind, ErrorKind::InvalidInput);
    }
    for vector in vectors["user_agent"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|vector| vector["valid"] == false)
    {
        let error = client
            .solve_with_options(
                "https://www.google.com/recaptcha/api2/anchor?k=x&co=legacy",
                SolveOptions {
                    user_agent: vector["value"].as_str(),
                    ..Default::default()
                },
            )
            .await
            .unwrap_err();
        assert_eq!(error.kind, ErrorKind::InvalidInput);
    }
    assert!(origin.requests.lock().unwrap().is_empty());
    assert!(updates.requests.lock().unwrap().is_empty());
}
