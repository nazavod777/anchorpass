// Command matrix runs one loopback transport benchmark job.
package main

import (
	"bytes"
	"context"
	"crypto/ed25519"
	"crypto/tls"
	"crypto/x509"
	"encoding/base64"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/http/cookiejar"
	"net/url"
	"os"
	"path/filepath"
	"regexp"
	"runtime"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"time"

	"github.com/go-resty/resty/v2"
	anchorpass "github.com/nazavod777/anchorpass/go"
	"golang.org/x/net/html"
	"golang.org/x/net/proxy"
)

type fixture struct {
	HTTPSURL     string            `json:"https_url"`
	BadTargetURL string            `json:"bad_target_url"`
	CAFile       string            `json:"ca_file"`
	ProxyCAFile  string            `json:"proxy_ca_file"`
	Proxies      map[string]string `json:"proxies"`
	AnchorURL    string            `json:"anchor_url"`
	BundleURL    string            `json:"bundle_url"`
	TrustedKeys  map[string]string `json:"trusted_keys"`
	FlowFields   map[string]string `json:"flow_fields"`
}
type profile struct {
	Name             string  `json:"name"`
	FlowDelayMS      int     `json:"flow_delay_ms"`
	Kind             string  `json:"kind"`
	Route            string  `json:"route"`
	ConnectionMode   string  `json:"connection_mode"`
	Concurrency      int     `json:"concurrency"`
	Warmup           int     `json:"warmup"`
	Requests         int     `json:"requests"`
	TimeoutMS        int     `json:"timeout_ms"`
	MaxResponseBytes int     `json:"max_response_bytes"`
	Path             string  `json:"path"`
	ExpectedBytes    int     `json:"expected_bytes"`
	ExpectedError    *string `json:"expected_error"`
	RecoveryRequests int     `json:"recovery_requests"`
}
type job struct {
	SchemaVersion int    `json:"schema_version"`
	RunID         string `json:"run_id"`
	Candidate     string `json:"candidate"`
	Round         int    `json:"round"`
	apiCachePath  string
	Fixture       fixture `json:"fixture"`
	Profile       profile `json:"profile"`
}
type observation struct {
	ElapsedNS int64   `json:"elapsed_ns"`
	OK        bool    `json:"ok"`
	Error     *string `json:"error"`
	Bytes     int     `json:"bytes"`
	Detail    string  `json:"detail,omitempty"`
}
type result struct {
	SchemaVersion   int               `json:"schema_version"`
	RunID           string            `json:"run_id"`
	Candidate       string            `json:"candidate"`
	Status          string            `json:"status"`
	Reason          string            `json:"reason,omitempty"`
	Runtime         string            `json:"runtime"`
	Versions        map[string]string `json:"versions"`
	Mode            string            `json:"mode"`
	Notes           []string          `json:"notes"`
	Samples         []observation     `json:"samples"`
	Recovery        []observation     `json:"recovery"`
	WarmupCompleted int               `json:"warmup_completed"`
	WallNS          int64             `json:"wall_ns"`
	CPUNS           int64             `json:"cpu_ns"`
	Resources       map[string]*int   `json:"resources"`
}
type categorizedError struct{ category, message string }

func (e categorizedError) Error() string { return e.message }

type proxyError struct{ err error }

func (e proxyError) Error() string { return "proxy connection failed" }
func (e proxyError) Unwrap() error { return e.err }

// A non-nil, non-replayable empty body prevents net/http's stale-connection GET replay.
type emptyBody struct{}

func (emptyBody) Read([]byte) (int, error) { return 0, io.EOF }
func (emptyBody) Close() error             { return nil }
func noReplay(r *http.Request)             { r.Body = emptyBody{}; r.GetBody = nil }

type requester struct {
	client    *http.Client
	resty     *resty.Client
	transport *http.Transport
	api       *anchorpass.Client
	fast      *fastRequester
}

func roots(path string) (*x509.CertPool, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	pool := x509.NewCertPool()
	if !pool.AppendCertsFromPEM(raw) {
		return nil, fmt.Errorf("no certificates in CA file")
	}
	return pool, nil
}
func newRequester(j job, p profile) (*requester, error) {
	if j.Candidate == "go-api" {
		return newAPIRequester(j, p, true)
	}
	targetRoots, err := roots(j.Fixture.CAFile)
	if err != nil {
		return nil, err
	}
	proxyRoots, err := roots(j.Fixture.ProxyCAFile)
	if err != nil {
		return nil, err
	}
	timeout := time.Duration(p.TimeoutMS) * time.Millisecond
	dialer := &net.Dialer{Timeout: timeout, KeepAlive: 30 * time.Second}
	tr := &http.Transport{Proxy: nil, DialContext: dialer.DialContext, MaxConnsPerHost: p.Concurrency, MaxIdleConns: p.Concurrency, MaxIdleConnsPerHost: p.Concurrency, IdleConnTimeout: 30 * time.Second, TLSHandshakeTimeout: timeout, ResponseHeaderTimeout: timeout, ForceAttemptHTTP2: false, TLSClientConfig: &tls.Config{MinVersion: tls.VersionTLS12, RootCAs: targetRoots}, MaxResponseHeaderBytes: 65536}
	var proxyURL *url.URL
	if p.Route != "direct" {
		route := p.Route
		if route == "bad_target" {
			route = "https"
		}
		raw, ok := j.Fixture.Proxies[route]
		if !ok {
			return nil, fmt.Errorf("missing proxy route %q", p.Route)
		}
		proxyURL, err = url.Parse(raw)
		if err != nil {
			return nil, fmt.Errorf("invalid proxy URL")
		}
		if proxyURL.Hostname() == "" || proxyURL.Port() == "" {
			return nil, fmt.Errorf("proxy needs explicit host and port")
		}
		switch proxyURL.Scheme {
		case "http", "https":
			tr.Proxy = http.ProxyURL(proxyURL)
			tr.OnProxyConnectResponse = func(_ context.Context, _ *url.URL, _ *http.Request, r *http.Response) error {
				if r.StatusCode != 200 {
					return categorizedError{"proxy", fmt.Sprintf("CONNECT status %d", r.StatusCode)}
				}
				return nil
			}
			tr.DialContext = func(ctx context.Context, network, address string) (net.Conn, error) {
				c, e := dialer.DialContext(ctx, network, address)
				if e != nil {
					return nil, proxyError{e}
				}
				return c, nil
			}
		case "socks5", "socks5h":
			var auth *proxy.Auth
			if proxyURL.User != nil {
				password, _ := proxyURL.User.Password()
				auth = &proxy.Auth{User: proxyURL.User.Username(), Password: password}
			}
			socks, e := proxy.SOCKS5("tcp", proxyURL.Host, auth, dialer)
			if e != nil {
				return nil, e
			}
			contextSocks, ok := socks.(proxy.ContextDialer)
			if !ok {
				return nil, fmt.Errorf("SOCKS dialer lacks context support")
			}
			tr.DialContext = func(ctx context.Context, network, address string) (net.Conn, error) {
				ctx, cancel := context.WithTimeout(ctx, timeout)
				defer cancel()
				host, port, e := net.SplitHostPort(address)
				if e != nil {
					return nil, e
				}
				if proxyURL.Scheme == "socks5" && net.ParseIP(host) == nil {
					ips, e := net.DefaultResolver.LookupIPAddr(ctx, host)
					if e != nil {
						return nil, e
					}
					if len(ips) == 0 {
						return nil, fmt.Errorf("no local DNS addresses")
					}
					// Prefer IPv4 on the IPv4 loopback fixture, with an IPv6 fallback.
					chosen := ips[0].IP
					for _, ip := range ips {
						if ip.IP.To4() != nil {
							chosen = ip.IP
							break
						}
					}
					address = net.JoinHostPort(chosen.String(), port)
				}
				c, e := contextSocks.DialContext(ctx, network, address)
				if e != nil {
					return nil, proxyError{e}
				}
				return c, nil
			}
		default:
			return nil, fmt.Errorf("unknown proxy scheme")
		}
	}
	// net/http calls this for the first TLS hop; tunneled target TLS uses TLSClientConfig.
	// HTTPS proxy roots are deliberately never added to the origin pool.
	if proxyURL != nil && proxyURL.Scheme == "https" {
		tr.DialTLSContext = func(ctx context.Context, network, address string) (net.Conn, error) {
			ctx, cancel := context.WithTimeout(ctx, timeout)
			defer cancel()
			c, e := dialer.DialContext(ctx, network, address)
			if e != nil {
				return nil, proxyError{e}
			}
			host, _, e := net.SplitHostPort(address)
			if e != nil {
				c.Close()
				return nil, e
			}
			secure := tls.Client(c, &tls.Config{MinVersion: tls.VersionTLS12, RootCAs: proxyRoots, ServerName: host})
			if e = secure.HandshakeContext(ctx); e != nil {
				c.Close()
				return nil, e
			}
			return secure, nil
		}
	}
	client := &http.Client{Transport: tr, CheckRedirect: func(_ *http.Request, _ []*http.Request) error { return http.ErrUseLastResponse }}
	r := &requester{client: client, transport: tr}
	if j.Candidate == "go-fasthttp" {
		r.fast = newFastRequester(p, tr, proxyURL, targetRoots)
	}
	if j.Candidate == "go-resty" {
		r.resty = resty.NewWithClient(client).SetRetryCount(0).SetHeader("User-Agent", "anchorpass-matrix")
		r.resty.SetPreRequestHook(func(_ *resty.Client, request *http.Request) error {
			if request.Method == http.MethodGet {
				noReplay(request)
			}
			return nil
		})
	}
	return r, nil
}
func (r *requester) close() {
	if r.api != nil {
		_ = r.api.Close()
		return
	}
	if r.fast != nil {
		r.fast.client.CloseIdleConnections()
	}
	r.transport.CloseIdleConnections()
}
func (r *requester) exchange(ctx context.Context, method, address, form, cookie string, limit int) ([]byte, http.Header, error) {
	if r.fast != nil {
		return r.fast.exchange(ctx, method, address, form, cookie, limit)
	}
	var response *http.Response
	if r.resty != nil {
		request := r.resty.R().SetContext(ctx).SetDoNotParseResponse(true)
		if cookie != "" {
			request.SetHeader("Cookie", cookie)
		}
		if method == http.MethodPost {
			request.SetHeader("Content-Type", "application/x-www-form-urlencoded").SetBody(form)
		}
		received, err := request.Execute(method, address)
		if err != nil {
			if received != nil && received.RawResponse != nil && received.RawResponse.Body != nil {
				received.RawResponse.Body.Close()
			}
			return nil, nil, err
		}
		response = received.RawResponse
	} else {
		request, err := http.NewRequestWithContext(ctx, method, address, strings.NewReader(form))
		if err != nil {
			return nil, nil, err
		}
		if method == http.MethodGet {
			noReplay(request)
		}
		request.Header.Set("User-Agent", "anchorpass-matrix")
		if cookie != "" {
			request.Header.Set("Cookie", cookie)
		}
		if method == http.MethodPost {
			request.Header.Set("Content-Type", "application/x-www-form-urlencoded")
		}
		response, err = r.client.Do(request)
		if err != nil {
			return nil, nil, err
		}
	}
	defer response.Body.Close()
	if response.StatusCode == 407 {
		return nil, nil, categorizedError{"proxy", "HTTP status 407"}
	}
	if response.StatusCode != 200 {
		return nil, nil, categorizedError{"http_status", fmt.Sprintf("HTTP status %d", response.StatusCode)}
	}
	body, err := io.ReadAll(io.LimitReader(response.Body, int64(limit)+1))
	if err != nil {
		return nil, nil, err
	}
	if len(body) > limit {
		return nil, nil, categorizedError{"response_limit", "decompressed response exceeds limit"}
	}
	return body, response.Header, nil
}
func (r *requester) get(ctx context.Context, address string, limit, expected int) (int, error) {
	body, _, err := r.exchange(ctx, http.MethodGet, address, "", "", limit)
	if err != nil {
		return 0, err
	}
	if len(body) != expected || strings.Trim(string(body), "x") != "" {
		return len(body), categorizedError{"validation", "incorrect fixture bytes"}
	}
	return len(body), nil
}

var flowVersion = regexp.MustCompile(`releases/([^/]+)/`)

func (r *requester) flow(ctx context.Context, j job, p profile) (int, error) {
	base := strings.TrimRight(j.Fixture.HTTPSURL, "/")
	jar, _ := cookiejar.New(nil)
	exchange := func(method, path, form string) ([]byte, error) {
		u, err := url.Parse(base + path)
		if err != nil {
			return nil, err
		}
		if p.FlowDelayMS > 0 {
			q := u.Query()
			q.Set("delay_ms", strconv.Itoa(p.FlowDelayMS))
			u.RawQuery = q.Encode()
		}
		request := &http.Request{Header: http.Header{}}
		for _, cookie := range jar.Cookies(u) {
			request.AddCookie(cookie)
		}
		body, headers, err := r.exchange(ctx, method, u.String(), form, request.Header.Get("Cookie"), p.MaxResponseBytes)
		if err != nil {
			return nil, err
		}
		jar.SetCookies(u, (&http.Response{Header: headers}).Cookies())
		return body, nil
	}
	script, err := exchange(http.MethodGet, "/recaptcha/api.js", "")
	if err != nil {
		return 0, err
	}
	version := flowVersion.FindSubmatch(script)
	if len(version) != 2 {
		return 0, categorizedError{"validation", "missing flow version"}
	}
	query := url.Values{"k": {"test-key"}, "co": {"dGVzdA"}, "v": {string(version[1])}, "sa": {"matrix-action"}}
	anchor, err := exchange(http.MethodGet, "/recaptcha/api2/anchor?"+query.Encode(), "")
	if err != nil {
		return 0, err
	}
	token := ""
	tokenizer := html.NewTokenizer(bytes.NewReader(anchor))
	for tokenizer.Next() != html.ErrorToken {
		t := tokenizer.Token()
		if t.Type != html.StartTagToken && t.Type != html.SelfClosingTagToken {
			continue
		}
		id, value := "", ""
		for _, a := range t.Attr {
			if a.Key == "id" {
				id = a.Val
			}
			if a.Key == "value" {
				value = a.Val
			}
		}
		if t.Data == "input" && id == "recaptcha-token" {
			token = value
			break
		}
	}
	if token == "" {
		return 0, categorizedError{"validation", "missing flow challenge"}
	}
	fields := url.Values{}
	for k, v := range j.Fixture.FlowFields {
		fields.Set(k, v)
	}
	for k, v := range query {
		fields[k] = v
	}
	fields.Set("c", token)
	reload, err := exchange(http.MethodPost, "/recaptcha/api2/reload?k=test-key", fields.Encode())
	if err != nil {
		return 0, err
	}
	raw := strings.TrimSpace(string(reload))
	if strings.HasPrefix(raw, ")]}'") {
		raw = strings.TrimSpace(strings.TrimPrefix(raw, ")]}'"))
	}
	var result []json.RawMessage
	if json.Unmarshal([]byte(raw), &result) != nil || len(result) < 2 {
		return 0, categorizedError{"validation", "invalid flow reload"}
	}
	var marker, value string
	if json.Unmarshal(result[0], &marker) != nil || json.Unmarshal(result[1], &value) != nil || marker != "rresp" || value != "fixture-token" {
		return 0, categorizedError{"validation", "incorrect flow token"}
	}
	return len(value), nil
}

func classify(err error) string {
	if errors.Is(err, context.DeadlineExceeded) {
		return "deadline"
	}
	var apiError *anchorpass.Error
	if errors.As(err, &apiError) {
		switch apiError.Code {
		case anchorpass.DeadlineExceeded:
			return "deadline"
		case anchorpass.ProxyConnectError, anchorpass.ProxyAuthError:
			return "proxy"
		case anchorpass.HTTPStatusError:
			return "http_status"
		case anchorpass.ResourceLimit:
			return "response_limit"
		}
	}
	var timed net.Error
	if errors.As(err, &timed) && timed.Timeout() {
		return "deadline"
	}
	var category categorizedError
	if errors.As(err, &category) {
		return category.category
	}
	var certificate *tls.CertificateVerificationError
	var unknown x509.UnknownAuthorityError
	var hostname x509.HostnameError
	if errors.As(err, &certificate) || errors.As(err, &unknown) || errors.As(err, &hostname) {
		return "tls"
	}
	var proxied proxyError
	if errors.As(err, &proxied) {
		return "proxy"
	}
	if errors.Is(err, io.EOF) || errors.Is(err, io.ErrUnexpectedEOF) || errors.As(err, &timed) {
		return "transport"
	}
	return "unclassified"
}
func safeDetail(err error, j job) string {
	// url.Error includes the full request URL; its nested error is sufficient here.
	var u *url.Error
	if errors.As(err, &u) {
		err = u.Err
	}
	message := fmt.Sprintf("%T: %v", err, err)
	for _, raw := range j.Fixture.Proxies {
		p, e := url.Parse(raw)
		if e == nil && p.User != nil {
			message = strings.ReplaceAll(message, p.User.String(), "[credentials]")
			password, _ := p.User.Password()
			if password != "" {
				message = strings.ReplaceAll(message, password, "[credentials]")
			}
		}
	}
	if len(message) > 512 {
		message = message[:512]
	}
	return message
}
func operate(j job, p profile, shared *requester) observation {
	start := time.Now()
	ctx, cancel := context.WithTimeout(context.Background(), time.Duration(p.TimeoutMS)*time.Millisecond)
	defer cancel()
	r := shared
	var err error
	if p.ConnectionMode == "cold" {
		r, err = newRequester(j, p)

	}
	count := 0
	if err == nil {
		base := j.Fixture.HTTPSURL
		if p.Route == "bad_target" {
			base = j.Fixture.BadTargetURL
		}
		if r.api != nil {
			var solved anchorpass.SolveResult
			solved, err = r.api.Solve(ctx, j.Fixture.AnchorURL)
			if err == nil && solved.Token != "fixture-token" {
				err = categorizedError{"validation", "incorrect fixture token"}
			}
			if err == nil {
				count = len(solved.Token)
			}
		} else if p.Kind == "flow" {
			count, err = r.flow(ctx, j, p)
		} else {
			count, err = r.get(ctx, strings.TrimRight(base, "/")+p.Path, p.MaxResponseBytes, p.ExpectedBytes)
		}
	}
	if p.ConnectionMode == "cold" && r != nil {
		r.close()
	}
	elapsed := time.Since(start).Nanoseconds()
	s := observation{ElapsedNS: elapsed, Bytes: count}
	if err != nil {
		category := classify(err)
		s.Error = &category
		s.Detail = safeDetail(err, j)
		s.OK = p.ExpectedError != nil && category == *p.ExpectedError
		if category == "deadline" && time.Duration(elapsed) > time.Duration(p.TimeoutMS+100)*time.Millisecond {
			s.OK = false
		}
	} else {
		s.OK = p.ExpectedError == nil
	}
	return s
}
func batch(j job, p profile, r *requester, count int) []observation {
	samples := make([]observation, count)
	jobs := make(chan int)
	var workers sync.WaitGroup
	for worker := 0; worker < p.Concurrency; worker++ {
		workers.Add(1)
		go func() {
			defer workers.Done()
			for index := range jobs {
				samples[index] = operate(j, p, r)
			}
		}()
	}
	for index := range samples {
		jobs <- index
	}
	close(jobs)
	workers.Wait()
	return samples
}
func procCount(path string) *int {
	entries, err := os.ReadDir(path)
	if err != nil {
		return nil
	}
	n := len(entries)
	return &n
}
func cpuNS() int64 {
	var usage syscall.Rusage
	if err := syscall.Getrusage(syscall.RUSAGE_SELF, &usage); err != nil {
		return 0
	}
	return (usage.Utime.Sec+usage.Stime.Sec)*int64(time.Second) + (usage.Utime.Usec+usage.Stime.Usec)*int64(time.Microsecond)
}
func validate(j job) error {
	p := j.Profile
	if j.SchemaVersion != 1 || j.RunID == "" {
		return fmt.Errorf("invalid job schema or run_id")
	}
	if j.Candidate != "go-net-http" && j.Candidate != "go-resty" && j.Candidate != "go-api" && j.Candidate != "go-fasthttp" {
		return fmt.Errorf("unknown candidate")
	}
	if p.Kind != "transport" && p.Kind != "api" && p.Kind != "flow" {
		return fmt.Errorf("unknown profile kind")
	}
	if p.Concurrency < 1 || p.Concurrency > 1024 || p.Requests < 1 || p.Requests > 1000000 || p.Warmup < 0 || p.Warmup > 1000000 || p.RecoveryRequests < 0 || p.RecoveryRequests > 1000000 || p.TimeoutMS < 1 || p.TimeoutMS > 60000 || p.MaxResponseBytes < 1 || p.MaxResponseBytes > 16777216 {
		return fmt.Errorf("job exceeds adapter limits")
	}
	if p.ConnectionMode != "warm" && p.ConnectionMode != "cold" {
		return fmt.Errorf("unknown connection_mode")
	}
	switch p.Route {
	case "direct", "http", "https", "socks5", "socks5h", "bad_auth", "bad_tls", "bad_target":
	default:
		return fmt.Errorf("unknown route")
	}
	if !strings.HasPrefix(p.Path, "/") {
		return fmt.Errorf("path must be origin-relative")
	}
	for _, raw := range []string{j.Fixture.HTTPSURL, j.Fixture.BadTargetURL, j.Fixture.BundleURL} {
		u, err := url.Parse(raw)
		if err != nil || (u.Scheme != "http" && u.Scheme != "https") || !loopbackURL(u) {
			return fmt.Errorf("fixture URLs must use loopback")
		}
	}
	for _, raw := range j.Fixture.Proxies {
		u, err := url.Parse(raw)
		if err != nil || !loopbackURL(u) {
			return fmt.Errorf("fixture proxies must use loopback")
		}
	}
	return nil
}

func loopbackURL(u *url.URL) bool {
	return u.Hostname() == "localhost" || net.ParseIP(u.Hostname()).IsLoopback()
}
func run(j job) (out result, runErr error) {
	if j.Candidate != "go-api" || j.Profile.Kind != "api" || j.Profile.Route != "http" {
		return runPrepared(j)
	}
	directory, err := os.MkdirTemp("", "anchorpass-go-api-cache-")
	if err != nil {
		return out, err
	}
	j.apiCachePath = filepath.Join(directory, "bundle.json")
	defer func() {
		for _, path := range []string{j.apiCachePath, j.apiCachePath + ".lock", directory} {
			if err := os.Remove(path); err != nil && !os.IsNotExist(err) {
				runErr = errors.Join(runErr, fmt.Errorf("API cache cleanup: %w", err))
			}
		}
	}()
	prepared, err := newAPIRequester(j, j.Profile, false)
	if err != nil {
		return out, err
	}
	ctx, cancel := context.WithTimeout(context.Background(), time.Duration(j.Profile.TimeoutMS)*time.Millisecond)
	_, err = prepared.api.Refresh(ctx)
	cancel()
	prepared.close()
	if err != nil {
		return out, fmt.Errorf("API bundle preparation: %w", err)
	}
	return runPrepared(j)
}
func runPrepared(j job) (result, error) {
	out := result{SchemaVersion: 1, RunID: j.RunID, Candidate: j.Candidate, Status: "ok", Runtime: runtime.Version(), Versions: map[string]string{"net/http": runtime.Version()}, Mode: "bounded-goroutines", Notes: []string{"HTTP/1.1; bounded worker pool; no ambient proxies, redirects or retries; limit after automatic gzip decompression", "cold includes client construction and cleanup; CPU/wall cover measured batch only; FD/thread counts are snapshots"}, Samples: []observation{}, Recovery: []observation{}, Resources: map[string]*int{}}
	if j.Candidate == "go-fasthttp" {
		out.Versions["fasthttp"] = "v1.65.0"
		out.Notes = append(out.Notes, "fasthttp v1.65.0 is the last stable release compatible with the package Go1.23 minimum; newer releases require Go1.24/1.25/1.26; bounded custom CONNECT/SOCKS dial adapter; gzip decoded with bounded reader")
	}
	if j.Candidate == "go-resty" {
		out.Versions["resty"] = "v2.16.5"
		out.Notes = append(out.Notes, "Resty request wrapper shares the same qualified net/http.Transport, TLS/SOCKS adapters and bounded body reader with go-net-http")
	}
	if j.Candidate == "go-api" {
		if j.Profile.Kind != "api" || j.Profile.Route != "http" {
			out.Status = "unsupported"
			out.Reason = "Public API adapter qualifies healthy HTTP CONNECT API profiles only"
			return out, nil
		}
		out.Versions["anchorpass"] = "local github.com/nazavod777/anchorpass/go"
		out.Notes = []string{"Real public Anchorpass client; HTTP CONNECT fixture maps google target to local origin; fixture signed bundle prepared with Refresh before timing", "Package policy: at most two GET attempts only on reused EOF/reset; POST not retried; fixed 1048576 decompressed-byte response limit", "Fresh signed persistent cache prepared and preparation client closed before timing; all measured clients are offline for updates; cold includes construction, signed cache verification and Close", "API bytes counts the returned fixture token bytes, not aggregate network response bytes; CPU/wall cover measured batch only"}
	} else if j.Profile.Kind != "transport" && j.Profile.Kind != "flow" {
		out.Status = "unsupported"
		out.Reason = "Raw transport candidate does not implement Anchorpass public API"
		return out, nil
	}
	p := j.Profile
	r, err := newRequester(j, p)
	if err != nil {
		return out, err
	}
	out.Resources["fds_before"] = procCount("/proc/self/fd")
	out.Resources["threads_before"] = procCount("/proc/self/task")
	warm := batch(j, p, r, p.Warmup)
	for _, sample := range warm {
		if !sample.OK {
			r.close()
			return out, fmt.Errorf("warmup failed: %s", sample.Detail)
		}
		out.WarmupCompleted++
	}
	cpu, start := cpuNS(), time.Now()
	out.Samples = batch(j, p, r, p.Requests)
	out.WallNS = time.Since(start).Nanoseconds()
	out.CPUNS = cpuNS() - cpu
	if p.RecoveryRequests > 0 {
		recovery := p
		recovery.Path = "/bytes?size=1024"
		recovery.ExpectedBytes = 1024
		recovery.ExpectedError = nil
		recoveryRequester := r
		if p.Route == "bad_auth" || p.Route == "bad_tls" || p.Route == "bad_target" {
			recovery.Route = "http"
			recoveryRequester, err = newRequester(j, recovery)
			if err != nil {
				r.close()
				return out, err
			}
		}
		out.Recovery = batch(j, recovery, recoveryRequester, p.RecoveryRequests)
		if recoveryRequester != r {
			recoveryRequester.close()
			out.Notes = append(out.Notes, "Invalid route recovery uses a fresh normal HTTP-proxy client; same-client recovery is impossible for immutable invalid routes")
		}
	}
	r.close()
	out.Resources["fds_after"] = procCount("/proc/self/fd")
	out.Resources["threads_after"] = procCount("/proc/self/task")
	return out, nil
}
func main() {
	jobPath := flag.String("job", "", "JSON job path")
	outputPath := flag.String("output", "", "JSON result path")
	flag.Parse()
	if err := execute(*jobPath, *outputPath); err != nil {
		fmt.Fprintln(os.Stderr, "matrix:", err)
		os.Exit(1)
	}
}
func execute(jobPath, outputPath string) error {
	if jobPath == "" || outputPath == "" {
		return fmt.Errorf("--job and --output are required")
	}
	file, err := os.Open(jobPath)
	if err != nil {
		return err
	}
	defer file.Close()
	var j job
	decoder := json.NewDecoder(io.LimitReader(file, 1048577))
	if err = decoder.Decode(&j); err != nil {
		return err
	}
	var trailing any
	if err = decoder.Decode(&trailing); err != io.EOF {
		return fmt.Errorf("trailing job data")
	}
	if err = validate(j); err != nil {
		return err
	}
	out, err := run(j)
	if err != nil {
		return err
	}
	raw, err := json.MarshalIndent(out, "", "  ")
	if err != nil {
		return err
	}
	return os.WriteFile(outputPath, append(raw, '\n'), 0600)
}

func newAPIRequester(j job, p profile, offline bool) (*requester, error) {
	if j.apiCachePath == "" {
		return nil, fmt.Errorf("API signed cache was not prepared")
	}
	targetRoots, err := roots(j.Fixture.CAFile)
	if err != nil {
		return nil, err
	}
	keys := make(map[string]ed25519.PublicKey, len(j.Fixture.TrustedKeys))
	for id, encoded := range j.Fixture.TrustedKeys {
		raw, e := base64.StdEncoding.DecodeString(encoded)
		if e != nil {
			return nil, fmt.Errorf("invalid fixture public key")
		}
		keys[id] = ed25519.PublicKey(raw)
	}
	client, err := anchorpass.NewClient(anchorpass.WithProxy(j.Fixture.Proxies["http"]), anchorpass.WithUpdatesProxy(anchorpass.UpdatesDirect), anchorpass.WithRootCAs(targetRoots), anchorpass.WithTrustedKeys(keys), anchorpass.WithBundleURL(j.Fixture.BundleURL), anchorpass.WithCachePath(j.apiCachePath), anchorpass.WithOffline(offline), anchorpass.WithMaxInFlight(p.Concurrency), anchorpass.WithTimeout(time.Duration(p.TimeoutMS)*time.Millisecond), anchorpass.WithRefreshInterval(time.Hour))
	if err != nil {
		return nil, err
	}
	return &requester{api: client}, nil
}
