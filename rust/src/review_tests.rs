use super::*;

#[tokio::test]
async fn authenticated_expired_update_establishes_memory_and_disk_floor() {
    for disk in [false, true] {
        for force in [false, true] {
            let now = bundle::now().unwrap();
            let (initial, keys) = signed("2", now + 600);
            let (expired, _) = signed("4", now.saturating_sub(1));
            let (lower, _) = signed("3", now + 600);
            let (equivocation, _) = signed("4", now + 600);
            let (invalid, _) = signed("9", now + 600);
            let mut invalid: serde_json::Value = serde_json::from_slice(&invalid).unwrap();
            invalid["signature"] = json!(STANDARD.encode([0; 64]));
            let (next, _) = signed("5", now + 600);
            let updates = server(
                vec![
                    initial,
                    expired.clone(),
                    lower,
                    equivocation,
                    serde_json::to_vec(&invalid).unwrap(),
                    next,
                ]
                .into_iter()
                .map(|body| (200, String::from_utf8(body).unwrap()))
                .collect(),
                Duration::ZERO,
            )
            .await;
            let dir = tempfile::tempdir().unwrap();
            let path = dir.path().join("bundle.json");
            let mut b = Client::builder()
                .trusted_keys(keys.clone())
                .bundle_url(updates.url.to_string())
                .refresh_interval(Duration::ZERO);
            if disk {
                b = b.cache_path(&path);
            }
            let mut client = b.clone().build().unwrap();
            assert_eq!(client.refresh().await.unwrap().revision, "2");
            assert_eq!(
                client.snapshot(force, 0).await.err().unwrap().kind,
                ErrorKind::ConfigExpired
            );
            assert_eq!(client.inner.state.lock().await.bundle.payload.revision, "4");
            if disk {
                assert_eq!(tokio::fs::read(&path).await.unwrap(), expired);
                drop(client);
                client = b.build().unwrap();
            }
            assert_eq!(
                client.refresh().await.unwrap_err().kind,
                ErrorKind::ConfigRollback
            );
            assert_eq!(
                client.refresh().await.unwrap_err().kind,
                ErrorKind::ConfigInvalid
            );
            assert_eq!(
                client.refresh().await.unwrap_err().kind,
                ErrorKind::ConfigInvalid
            );
            assert_eq!(client.refresh().await.unwrap().revision, "5");
        }
    }
}

#[test]
fn bundle_loopback_url_supports_both_address_families() {
    for input in [
        "http://127.0.0.1:1234/bundle",
        "http://[::1]:1234/bundle",
        "http://localhost:1234/bundle",
    ] {
        assert!(
            Client::builder().bundle_url(input).build().is_ok(),
            "{input}"
        );
    }
    for input in [
        "http://[2001:db8::1]:1234/bundle",
        "http://192.0.2.1:1234/bundle",
    ] {
        assert_eq!(
            Client::builder()
                .bundle_url(input)
                .build()
                .unwrap_err()
                .kind,
            ErrorKind::InvalidInput
        );
    }
}

#[tokio::test]
async fn supplied_version_is_replaced_by_fetched_version_for_both_variants() {
    for variant in ["api2", "enterprise"] {
        let origin = server(
            vec![
                (
                    200,
                    "/recaptcha/releases/current-version/recaptcha.js".into(),
                ),
                (200, "<input id=recaptcha-token value=challenge>".into()),
                (200, "[\"rresp\",\"fixture\"]".into()),
            ],
            Duration::ZERO,
        )
        .await;
        let result = builder(&origin)
            .build()
            .unwrap()
            .solve(&format!(
                "https://google.com/recaptcha/{variant}/anchor?k=x&co=y&v=stale-client-version"
            ))
            .await
            .unwrap();
        assert_eq!(result.protocol_version, "current-version");
        let requests = origin.requests.lock().unwrap();
        assert_eq!(requests.len(), 3);
        let script = if variant == "api2" {
            "api"
        } else {
            "enterprise"
        };
        assert!(requests[0].starts_with(&format!("GET /recaptcha/{script}.js?render=x ")));
        assert!(requests[1].contains("v=current-version"));
        assert!(requests[2].contains("v=current-version"));
        assert!(requests
            .iter()
            .all(|request| !request.contains("stale-client-version")));
    }
}

#[test]
fn cookie_scope_expiry_deletion_and_budgets() {
    use crate::cookies::OperationCookies;
    use reqwest::header::{HeaderMap, HeaderValue, SET_COOKIE};
    let origin = Url::parse("https://www.google.com/recaptcha/api2/anchor").unwrap();
    let mut cookies = OperationCookies::default();
    let mut headers = HeaderMap::new();
    for value in [
        "host=value; Path=/recaptcha/",
        "parent=value; Domain=.google.com; Path=/",
        "wrong=value; Domain=example.com; Path=/",
        "suffix=value; Domain=com; Path=/",
        "secure=value; Secure; Path=/",
        "expired=value; Expires=Thu, 01 Jan 1970 00:00:01 GMT; Path=/",
        "deleted=value; Path=/",
        "deleted=gone; Path=/; Max-Age=0",
    ] {
        headers.append(SET_COOKIE, HeaderValue::from_static(value));
    }
    cookies.receive(&origin, &headers, "test").unwrap();
    for (address, expected) in [
        (
            "https://www.google.com/recaptcha/api2/reload",
            "host=value; parent=value; secure=value",
        ),
        ("https://google.com/recaptcha/api2/reload", "parent=value"),
        (
            "http://www.google.com/recaptcha/api2/reload",
            "host=value; parent=value",
        ),
        (
            "https://www.google.com/recaptchax/reload",
            "parent=value; secure=value",
        ),
        ("https://example.com/recaptcha/api2/reload", ""),
    ] {
        let actual = cookies
            .header(&Url::parse(address).unwrap(), "test")
            .unwrap()
            .unwrap_or_default();
        let mut parts: Vec<_> = actual.split("; ").collect();
        parts.sort_unstable();
        assert_eq!(parts.join("; "), expected, "{address}");
    }
    let mut cookies = OperationCookies::default();
    let mut headers = HeaderMap::new();
    headers.append(SET_COOKIE, HeaderValue::from_static("name=value"));
    for _ in 0..64 {
        cookies.receive(&origin, &headers, "test").unwrap();
    }
    assert_eq!(
        cookies.receive(&origin, &headers, "test").unwrap_err().kind,
        ErrorKind::ResourceLimit
    );
    let mut cookies = OperationCookies::default();
    let mut headers = HeaderMap::new();
    headers.append(
        SET_COOKIE,
        HeaderValue::from_str(&format!("name={}", "x".repeat(16384))).unwrap(),
    );
    assert_eq!(
        cookies.receive(&origin, &headers, "test").unwrap_err().kind,
        ErrorKind::ResourceLimit
    );
}

#[tokio::test]
async fn operation_cookie_limits_are_cumulative_before_reload() {
    for oversized in [false, true] {
        let headers = if oversized {
            format!("Set-Cookie: name={}\r\n", "x".repeat(16384))
        } else {
            "Set-Cookie: name=value\r\n".repeat(64)
        };
        let origin = server_with_headers(
            vec![
                (
                    200,
                    "/recaptcha/releases/current/recaptcha.js".into(),
                    headers,
                ),
                (
                    200,
                    "<input id=recaptcha-token value=challenge>".into(),
                    "Set-Cookie: extra=value\r\n".into(),
                ),
            ],
            Duration::ZERO,
        )
        .await;
        let result = builder(&origin)
            .build()
            .unwrap()
            .solve("https://google.com/recaptcha/api2/anchor?k=x&co=y")
            .await;
        assert_eq!(result.unwrap_err().kind, ErrorKind::ResourceLimit);
        assert_eq!(
            origin.requests.lock().unwrap().len(),
            if oversized { 1 } else { 2 }
        );
    }
}

#[tokio::test]
async fn cookies_are_isolated_between_solves_with_shared_https_proxy_pool() {
    let origin = tls_origin().await;
    let proxy = tunnel(origin.address, true).await;
    let mut b = Client::builder()
        .offline(true)
        .proxy(&proxy.url)
        .add_root_certificate(origin.pem.as_bytes())
        .unwrap()
        .add_proxy_root_certificate(proxy.pem.as_ref().unwrap().as_bytes())
        .unwrap();
    b.origin = Some(origin.url.clone());
    let client = b.build().unwrap();
    for _ in 0..2 {
        assert_eq!(
            client
                .solve("https://google.com/recaptcha/api2/anchor?k=x&co=y")
                .await
                .unwrap()
                .token,
            "tls-token"
        );
    }
    let requests = origin.requests.lock().unwrap();
    assert_eq!(requests.len(), 6);
    for request in [&requests[0], &requests[3]] {
        assert!(!request.to_ascii_lowercase().contains("cookie:"));
    }
    for request in [&requests[1], &requests[2], &requests[4], &requests[5]] {
        assert!(request
            .to_ascii_lowercase()
            .contains("cookie: operation=fixture"));
    }
    let connects = proxy.seen.lock().unwrap();
    assert_eq!(
        connects.len(),
        1,
        "operation isolation discarded the transport pool"
    );
    assert!(connects
        .iter()
        .all(|request| !request.to_ascii_lowercase().contains("cookie:")));
}

#[tokio::test]
async fn concurrent_operations_preserve_required_cookie_sessions() {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let address = listener.local_addr().unwrap();
    let task = tokio::spawn(async move {
        let mut sequence = 0;
        let mut reloads = 0;
        for _ in 0..9 {
            let (mut stream, _) = listener.accept().await.unwrap();
            let mut head = Vec::new();
            while !head.ends_with(b"\r\n\r\n") {
                head.push(stream.read_u8().await.unwrap());
                assert!(head.len() <= 65536);
            }
            let head = String::from_utf8(head).unwrap();
            let header = |name: &str| {
                head.lines().find_map(|line| {
                    line.split_once(':')
                        .filter(|(key, _)| key.eq_ignore_ascii_case(name))
                        .map(|(_, value)| value.trim())
                })
            };
            let size: usize = header("content-length").unwrap_or("0").parse().unwrap();
            let mut body = vec![0; size];
            stream.read_exact(&mut body).await.unwrap();
            let cookie = header("cookie").and_then(|value| value.strip_prefix("operation="));
            let (status, body, headers) = if head.contains("/api.js?") {
                assert!(cookie.is_none(), "cookie crossed operation boundary");
                sequence += 1;
                (
                    200,
                    "/recaptcha/releases/current/recaptcha.js".to_owned(),
                    format!("Set-Cookie: operation={sequence}; Path=/recaptcha/\r\n"),
                )
            } else if head.starts_with("POST ") {
                let fields: BTreeMap<_, _> =
                    url::form_urlencoded::parse(&body).into_owned().collect();
                if cookie.is_some() && cookie == fields.get("c").map(String::as_str) {
                    reloads += 1;
                    (200, "[\"rresp\",\"fixture\"]".to_owned(), String::new())
                } else {
                    (403, "wrong cookie".to_owned(), String::new())
                }
            } else if let Some(cookie) = cookie {
                (
                    200,
                    format!("<input id=recaptcha-token value={cookie}>"),
                    String::new(),
                )
            } else {
                (403, "missing cookie".to_owned(), String::new())
            };
            let response = format!("HTTP/1.1 {status} Test\r\nContent-Length: {}\r\nConnection: close\r\n{headers}\r\n{body}", body.len());
            stream.write_all(response.as_bytes()).await.unwrap();
        }
        reloads
    });
    let mut b = Client::builder().offline(true);
    b.origin = Some(Url::parse(&format!("http://{address}/")).unwrap());
    let client = b.build().unwrap();
    let url = "https://google.com/recaptcha/api2/anchor?k=x&co=y";
    let (first, second) = tokio::join!(client.solve(url), client.solve(url));
    first.unwrap();
    second.unwrap();
    client.solve(url).await.unwrap();
    assert_eq!(
        tokio::time::timeout(Duration::from_secs(2), task)
            .await
            .unwrap()
            .unwrap(),
        3
    );
}

#[test]
fn outgoing_cookie_header_budget_is_checked_after_serialization() {
    use crate::cookies::OperationCookies;
    use reqwest::header::{HeaderMap, HeaderValue, SET_COOKIE};
    let origin = Url::parse("https://google.com/recaptcha/api.js").unwrap();
    let mut cookies = OperationCookies::default();
    let mut headers = HeaderMap::new();
    for name in ["a", "b"] {
        headers.append(
            SET_COOKIE,
            HeaderValue::from_str(&format!(
                "{name}={}",
                "x".repeat(16384 / 2 - name.len() - 1)
            ))
            .unwrap(),
        );
    }
    cookies.receive(&origin, &headers, "test").unwrap();
    assert_eq!(
        cookies.header(&origin, "test").unwrap_err().kind,
        ErrorKind::ResourceLimit
    );
}
