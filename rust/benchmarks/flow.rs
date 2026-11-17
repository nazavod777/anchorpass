//! Synthetic three-request flow shared by all raw transports, independent of the public API.
use super::*;

fn invalid(message: &str) -> Failure {
    Failure("validation", Some(message.into()))
}
fn url(job: &Job, path: &str) -> String {
    let mut url = reqwest::Url::parse(&format!(
        "{}{}",
        job.fixture.https_url.trim_end_matches('/'),
        path
    ))
    .expect("validated fixture URL and fixed flow path");
    if job.profile.flow_delay_ms != 0 {
        url.query_pairs_mut()
            .append_pair("delay_ms", &job.profile.flow_delay_ms.to_string());
    }
    url.to_string()
}
fn version(body: &[u8]) -> Result<String, Failure> {
    let text = std::str::from_utf8(body).map_err(|_| invalid("invalid version encoding"))?;
    text.split_once("releases/")
        .and_then(|(_, tail)| tail.split_once('/'))
        .map(|(version, _)| version.to_owned())
        .filter(|value| !value.is_empty())
        .ok_or_else(|| invalid("missing flow version"))
}
fn cookie(header: Option<String>) -> Result<String, Failure> {
    let value = header.ok_or_else(|| invalid("missing operation cookie"))?;
    let cookie = value.split(';').next().unwrap_or("");
    if !cookie
        .strip_prefix("operation=")
        .is_some_and(|value| !value.is_empty() && value.bytes().all(|byte| byte.is_ascii_digit()))
    {
        return Err(invalid("invalid operation cookie"));
    }
    Ok(cookie.into())
}
fn anchor_path(version: &str) -> String {
    let mut url = reqwest::Url::parse("https://localhost/recaptcha/api2/anchor").unwrap();
    url.query_pairs_mut().extend_pairs([
        ("k", "test-key"),
        ("co", "dGVzdA"),
        ("sa", "matrix-action"),
        ("v", version),
    ]);
    format!("{}?{}", url.path(), url.query().unwrap())
}
fn form(job: &Job, version: &str, body: &[u8]) -> Result<String, Failure> {
    let text = std::str::from_utf8(body).map_err(|_| invalid("invalid anchor encoding"))?;
    let challenge = text
        .split_once("id=\"recaptcha-token\" value=\"")
        .and_then(|(_, tail)| tail.split_once('"'))
        .map(|(value, _)| value)
        .filter(|value| !value.is_empty())
        .ok_or_else(|| invalid("missing flow challenge"))?;
    let mut fields = job.fixture.flow_fields.clone();
    for (key, value) in [
        ("k", "test-key"),
        ("co", "dGVzdA"),
        ("sa", "matrix-action"),
        ("v", version),
        ("c", challenge),
    ] {
        fields.insert(key.into(), value.into());
    }
    let mut encoded = reqwest::Url::parse("https://localhost/").unwrap();
    encoded.query_pairs_mut().extend_pairs(fields);
    Ok(encoded.query().unwrap_or("").into())
}
fn token(body: Vec<u8>) -> Operation {
    let text = std::str::from_utf8(&body)
        .map_err(|_| invalid("invalid reload encoding"))?
        .trim();
    let text = text.strip_prefix(")]}'").unwrap_or(text).trim();
    let value: serde_json::Value =
        serde_json::from_str(text).map_err(|_| invalid("invalid reload JSON"))?;
    if value.get(0).and_then(serde_json::Value::as_str) != Some("rresp")
        || value.get(1).and_then(serde_json::Value::as_str) != Some("fixture-token")
    {
        return Err(invalid("incorrect flow token"));
    }
    Ok(b"fixture-token".to_vec())
}
struct Request<'a> {
    url: String,
    form: Option<&'a str>,
    cookie: Option<&'a str>,
    timeout: Duration,
}
type Response = Result<(Vec<u8>, Option<String>), Failure>;
fn sync_request(client: &SyncClient, job: &Job, input: Request<'_>) -> Response {
    let limit = job.profile.max_response_bytes;
    match client {
        SyncClient::Reqwest(client) => {
            let mut request = if input.form.is_some() {
                client.post(&input.url)
            } else {
                client.get(&input.url)
            }
            .timeout(input.timeout);
            if let Some(cookie) = input.cookie {
                request = request.header("Cookie", cookie);
            }
            if let Some(form) = input.form {
                request = request
                    .header("Content-Type", "application/x-www-form-urlencoded")
                    .body(form.to_owned());
            }
            let response = request.send().map_err(reqwest_failure)?;
            status(response.status().as_u16())?;
            let cookie = response
                .headers()
                .get("set-cookie")
                .and_then(|value| value.to_str().ok())
                .map(str::to_owned);
            Ok((read_body(response, limit)?, cookie))
        }
        SyncClient::Isahc(client) => {
            let mut request = isahc::Request::builder()
                .uri(&input.url)
                .method(if input.form.is_some() { "POST" } else { "GET" })
                .timeout(input.timeout);
            if let Some(cookie) = input.cookie {
                request = request.header("Cookie", cookie);
            }
            if input.form.is_some() {
                request = request.header("Content-Type", "application/x-www-form-urlencoded");
            }
            let request = request
                .body(input.form.unwrap_or("").to_owned())
                .map_err(|_| invalid("invalid isahc flow request"))?;
            let mut response = client
                .send(request)
                .map_err(|error| isahc_failure(&error).detail(&error))?;
            status(response.status().as_u16())?;
            let cookie = response
                .headers()
                .get("set-cookie")
                .and_then(|value| value.to_str().ok())
                .map(str::to_owned);
            Ok((read_body(response.body_mut(), limit)?, cookie))
        }
        SyncClient::Ureq(client) => {
            let response = if let Some(form) = input.form {
                let mut request = client
                    .post(&input.url)
                    .header("Content-Type", "application/x-www-form-urlencoded");
                if let Some(cookie) = input.cookie {
                    request = request.header("Cookie", cookie);
                }
                request
                    .config()
                    .timeout_global(Some(input.timeout))
                    .build()
                    .send(form)
            } else {
                let mut request = client.get(&input.url);
                if let Some(cookie) = input.cookie {
                    request = request.header("Cookie", cookie);
                }
                request
                    .config()
                    .timeout_global(Some(input.timeout))
                    .build()
                    .call()
            };
            let mut response = response.map_err(|error| ureq_failure(&error).detail(&error))?;
            status(response.status().as_u16())?;
            let cookie = response
                .headers()
                .get("set-cookie")
                .and_then(|value| value.to_str().ok())
                .map(str::to_owned);
            Ok((read_body(response.body_mut().as_reader(), limit)?, cookie))
        }
        SyncClient::Api(_) => Err(invalid("raw flow cannot call API transport")),
    }
}
pub(super) fn sync(client: &SyncClient, job: &Job) -> Operation {
    let deadline = Instant::now() + duration(job);
    let remaining = || {
        deadline
            .checked_duration_since(Instant::now())
            .filter(|value| !value.is_zero())
            .ok_or_else(|| Failure::new("deadline"))
    };
    let (script, header) = sync_request(
        client,
        job,
        Request {
            url: url(job, "/recaptcha/api.js"),
            form: None,
            cookie: None,
            timeout: remaining()?,
        },
    )?;
    let version = version(&script)?;
    let cookie = cookie(header)?;
    let (anchor, _) = sync_request(
        client,
        job,
        Request {
            url: url(job, &anchor_path(&version)),
            form: None,
            cookie: Some(&cookie),
            timeout: remaining()?,
        },
    )?;
    let form = form(job, &version, &anchor)?;
    let (reload, _) = sync_request(
        client,
        job,
        Request {
            url: url(job, "/recaptcha/api2/reload?k=test-key"),
            form: Some(&form),
            cookie: Some(&cookie),
            timeout: remaining()?,
        },
    )?;
    token(reload)
}
async fn async_request(
    client: &reqwest::Client,
    job: &Job,
    path: &str,
    form: Option<&str>,
    cookie: Option<&str>,
) -> Response {
    let mut request = if form.is_some() {
        client.post(url(job, path))
    } else {
        client.get(url(job, path))
    };
    if let Some(cookie) = cookie {
        request = request.header("Cookie", cookie);
    }
    if let Some(form) = form {
        request = request
            .header("Content-Type", "application/x-www-form-urlencoded")
            .body(form.to_owned());
    }
    let mut response = request.send().await.map_err(reqwest_failure)?;
    status(response.status().as_u16())?;
    let cookie = response
        .headers()
        .get("set-cookie")
        .and_then(|value| value.to_str().ok())
        .map(str::to_owned);
    let mut body = Vec::new();
    while let Some(chunk) = response.chunk().await.map_err(reqwest_failure)? {
        if chunk.len() > job.profile.max_response_bytes.saturating_sub(body.len()) {
            return Err(Failure::new("response_limit"));
        }
        body.extend_from_slice(&chunk);
    }
    Ok((body, cookie))
}
pub(super) async fn asynchronous(client: &reqwest::Client, job: &Job) -> Operation {
    let operation = async {
        let (script, header) = async_request(client, job, "/recaptcha/api.js", None, None).await?;
        let version = version(&script)?;
        let cookie = cookie(header)?;
        let (anchor, _) =
            async_request(client, job, &anchor_path(&version), None, Some(&cookie)).await?;
        let form = form(job, &version, &anchor)?;
        let (reload, _) = async_request(
            client,
            job,
            "/recaptcha/api2/reload?k=test-key",
            Some(&form),
            Some(&cookie),
        )
        .await?;
        token(reload)
    };
    tokio::time::timeout(duration(job), operation)
        .await
        .unwrap_or(Err(Failure::new("deadline")))
}
