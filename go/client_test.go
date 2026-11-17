package anchorpass

import (
	"context"
	"crypto/ed25519"
	"crypto/rand"
	"crypto/tls"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/base64"
	"fmt"
	"io"
	"math/big"
	"net"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"
)

func testTLS(t *testing.T) (tls.Certificate, *x509.CertPool) {
	t.Helper()
	pub, key, e := ed25519.GenerateKey(rand.Reader)
	if e != nil {
		t.Fatal(e)
	}
	template := &x509.Certificate{SerialNumber: big.NewInt(1), Subject: pkix.Name{CommonName: "anchorpass-test"}, NotBefore: time.Now().Add(-time.Hour), NotAfter: time.Now().Add(time.Hour), KeyUsage: x509.KeyUsageDigitalSignature | x509.KeyUsageCertSign, ExtKeyUsage: []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth}, IsCA: true, BasicConstraintsValid: true, DNSNames: []string{"localhost", "proxy-only.invalid", "example.com"}, IPAddresses: []net.IP{net.ParseIP("127.0.0.1"), net.ParseIP("::1")}}
	der, e := x509.CreateCertificate(rand.Reader, template, template, pub, key)
	if e != nil {
		t.Fatal(e)
	}
	cert, e := x509.ParseCertificate(der)
	if e != nil {
		t.Fatal(e)
	}
	pool := x509.NewCertPool()
	pool.AddCert(cert)
	return tls.Certificate{Certificate: [][]byte{der}, PrivateKey: key}, pool
}
func testClient(t *testing.T, origin string, options ...Option) *Client {
	t.Helper()
	options = append(options, Option{apply: func(s *settings) error { s.testOrigin = origin; return nil }})
	c, e := NewClient(options...)
	if e != nil {
		t.Fatal(e)
	}
	t.Cleanup(func() { _ = c.Close() })
	return c
}
func protocolFixture(t *testing.T, expectedHost ...string) (*httptest.Server, *x509.CertPool, *atomic.Int64) {
	t.Helper()
	certificate, roots := testTLS(t)
	requests := &atomic.Int64{}
	server := httptest.NewUnstartedServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		requests.Add(1)
		if r.Method == http.MethodGet {
			raw, err := io.ReadAll(r.Body)
			if err != nil || len(raw) != 0 || r.ContentLength != 0 || len(r.TransferEncoding) != 0 || r.Header.Get("Content-Length") != "" || r.Header.Get("Transfer-Encoding") != "" {
				t.Error("GET wire semantics changed through TLS/proxy route")
			}
		}
		if len(expectedHost) > 0 {
			host, _, err := net.SplitHostPort(r.Host)
			if err != nil || host != expectedHost[0] || r.TLS == nil || r.TLS.ServerName != expectedHost[0] {
				t.Errorf("original HTTP host or TLS SNI was not preserved")
			}
		}
		if r.Header.Get("Proxy-Authorization") != "" {
			t.Error("proxy credentials leaked to origin")
		}
		if strings.HasSuffix(r.URL.Path, ".js") {
			if r.Header.Get("Cookie") != "" {
				t.Error("cookies leaked between operations")
			}
		} else if r.Header.Get("Cookie") != "session=fixture" {
			t.Error("operation cookies were not preserved")
		}
		w.Header().Set("Set-Cookie", "session=fixture; Path=/")
		switch {
		case strings.HasSuffix(r.URL.Path, ".js"):
			if r.URL.Query().Get("render") != "site+key" {
				t.Error("incorrect render key")
			}
			fmt.Fprint(w, `https://www.gstatic.com/recaptcha/releases/test-version/recaptcha__en.js`)
		case strings.HasSuffix(r.URL.Path, "/anchor"):
			if r.URL.Query().Get("v") != "test-version" || r.URL.Query().Get("sa") != "explicit-action" || r.URL.Query().Get("extra") != "keep&value" {
				t.Errorf("incorrect anchor fields: %s", r.URL.RawQuery)
			}
			fmt.Fprint(w, `<input value="anchor&amp;token" id="recaptcha-token">`)
		case strings.HasSuffix(r.URL.Path, "/reload"):
			if r.Method != "POST" {
				t.Error("reload was not POST")
			}
			if e := r.ParseForm(); e != nil {
				t.Error(e)
			}
			keys, e := defaultKeys()
			if e != nil {
				t.Error(e)
				return
			}
			b, e := verifyBundle(bundledEnvelope, keys, time.Now())
			if e != nil {
				t.Error(e)
				return
			}
			a, _ := parseAnchor(goodAnchor)
			a.values.Set("sa", "explicit-action")
			expected := reloadFields(a, b, "test-version", "anchor&token")
			if r.PostForm.Encode() != expected.Encode() {
				t.Error("reload fields differ from exact bundled fields")
			}
			fmt.Fprint(w, ")]}'\n[\"rresp\",\"fixture-token\",null]")
		default:
			http.NotFound(w, r)
		}
	}))
	server.TLS = &tls.Config{Certificates: []tls.Certificate{certificate}}
	server.StartTLS()
	t.Cleanup(server.Close)
	return server, roots, requests
}

func TestSolveBothFamiliesAndLifecycle(t *testing.T) {
	origin, roots, requests := protocolFixture(t)
	c := testClient(t, origin.URL, WithRootCAs(roots), WithOffline(true))
	for _, family := range []string{"api2", "enterprise"} {
		result, e := c.Solve(context.Background(), strings.Replace(goodAnchor, "api2", family, 1), WithAction("explicit-action"))
		if e != nil {
			t.Fatal(e)
		}
		if result.Token != "fixture-token" || result.BundleRevision != "1" || result.ProtocolVersion != "test-version" {
			t.Fatalf("unexpected result: %s", result)
		}
	}
	if requests.Load() != 6 {
		t.Fatalf("request count: %d", requests.Load())
	}
	if e := c.Close(); e != nil {
		t.Fatal(e)
	}
	_, e := c.Solve(context.Background(), goodAnchor)
	requireCode(t, e, ClientClosed)
	_ = c.Close()
}
func TestConcurrentSolves(t *testing.T) {
	origin, roots, _ := protocolFixture(t)
	c := testClient(t, origin.URL, WithRootCAs(roots), WithOffline(true))
	var group sync.WaitGroup
	for i := 0; i < 8; i++ {
		group.Add(1)
		go func() {
			defer group.Done()
			_, e := c.Solve(context.Background(), goodAnchor, WithAction("explicit-action"))
			if e != nil {
				t.Error(e)
			}
		}()
	}
	group.Wait()
}
func TestLimitDeadlineCancelClose(t *testing.T) {
	entered := make(chan struct{}, 1)
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		select {
		case entered <- struct{}{}:
		default:
		}
		<-r.Context().Done()
	}))
	defer server.Close()
	c := testClient(t, server.URL, WithTimeout(120*time.Millisecond), WithMaxInFlight(1), WithOffline(true))
	result := make(chan error, 1)
	go func() { _, e := c.Solve(context.Background(), goodAnchor); result <- e }()
	<-entered
	_, e := c.Solve(context.Background(), goodAnchor)
	requireCode(t, e, ResourceLimit)
	requireCode(t, <-result, DeadlineExceeded)
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	_, e = c.Solve(ctx, goodAnchor)
	requireCode(t, e, Cancelled)
	go func() { _, e := c.Solve(context.Background(), goodAnchor); result <- e }()
	<-entered
	_ = c.Close()
	requireCode(t, <-result, Cancelled)
}
func TestBodyBoundAndRedirect(t *testing.T) {
	for _, mode := range []string{"oversize", "redirect", "drip"} {
		t.Run(mode, func(t *testing.T) {
			var targetCalls atomic.Int64
			target := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) { targetCalls.Add(1) }))
			defer target.Close()
			origin := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				switch mode {
				case "oversize":
					fmt.Fprint(w, strings.Repeat("x", maxResponseBytes+1))
				case "redirect":
					http.Redirect(w, r, target.URL, http.StatusFound)
				case "drip":
					for i := 0; i < 30; i++ {
						fmt.Fprint(w, "x")
						w.(http.Flusher).Flush()
						select {
						case <-r.Context().Done():
							return
						case <-time.After(15 * time.Millisecond):
						}
					}
				}
			}))
			defer origin.Close()
			c := testClient(t, origin.URL, WithOffline(true), WithTimeout(80*time.Millisecond))
			_, e := c.Solve(context.Background(), goodAnchor)
			switch mode {
			case "oversize":
				requireCode(t, e, ResourceLimit)
			case "redirect":
				requireCode(t, e, HTTPStatusError)
			case "drip":
				requireCode(t, e, DeadlineExceeded)
			}
			if targetCalls.Load() != 0 {
				t.Fatal("redirect escaped origin")
			}
		})
	}
}

type proxyFixture struct {
	server *httptest.Server
	calls  atomic.Int64
	auth   string
	target string
}

func newProxyFixture(t *testing.T, target, auth string, certificate *tls.Certificate) *proxyFixture {
	t.Helper()
	p := &proxyFixture{target: target, auth: auth}
	handler := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		p.calls.Add(1)
		if r.Header.Get("Proxy-Authorization") != auth {
			w.WriteHeader(407)
			return
		}
		if r.Method == "CONNECT" {
			destination := r.Host
			if p.target != "" {
				destination = p.target
			}
			upstream, e := net.DialTimeout("tcp", destination, time.Second)
			if e != nil {
				w.WriteHeader(502)
				return
			}
			downstream, buffer, e := w.(http.Hijacker).Hijack()
			if e != nil {
				upstream.Close()
				return
			}
			fmt.Fprint(buffer, "HTTP/1.1 200 Connection Established\r\n\r\n")
			if e = buffer.Flush(); e != nil {
				downstream.Close()
				upstream.Close()
				return
			}
			go func() { defer downstream.Close(); defer upstream.Close(); _, _ = io.Copy(upstream, buffer) }()
			go func() { defer downstream.Close(); defer upstream.Close(); _, _ = io.Copy(downstream, upstream) }()
			return
		}
		outgoing := r.Clone(r.Context())
		outgoing.RequestURI = ""
		outgoing.Header.Del("Proxy-Authorization")
		tr := &http.Transport{Proxy: nil}
		defer tr.CloseIdleConnections()
		res, e := tr.RoundTrip(outgoing)
		if e != nil {
			w.WriteHeader(502)
			return
		}
		defer res.Body.Close()
		for k, v := range res.Header {
			w.Header()[k] = v
		}
		w.WriteHeader(res.StatusCode)
		_, _ = io.Copy(w, res.Body)
	})
	p.server = httptest.NewUnstartedServer(handler)
	if certificate != nil {
		p.server.TLS = &tls.Config{Certificates: []tls.Certificate{*certificate}}
		p.server.StartTLS()
	} else {
		p.server.Start()
	}
	t.Cleanup(p.server.Close)
	return p
}
func TestHTTPAndHTTPSProxy(t *testing.T) {
	origin, roots, requests := protocolFixture(t)
	for _, secure := range []bool{false, true} {
		t.Run(fmt.Sprint(secure), func(t *testing.T) {
			var certificate *tls.Certificate
			var proxyRoots *x509.CertPool
			if secure {
				cert, trust := testTLS(t)
				proxyRoots = trust
				certificate = &cert
			}
			auth := "Basic " + base64.StdEncoding.EncodeToString([]byte("user+:p@ss"))
			p := newProxyFixture(t, "", auth, certificate)
			raw := strings.Replace(p.server.URL, "://", "://user%2B:p%40ss@", 1)
			options := []Option{WithRootCAs(roots), WithProxy(raw), WithOffline(true)}
			if proxyRoots != nil {
				options = append(options, WithProxyRootCAs(proxyRoots))
			}
			c := testClient(t, origin.URL, options...)
			if _, e := c.Solve(context.Background(), goodAnchor, WithAction("explicit-action")); e != nil {
				t.Fatal(e)
			}
			if p.calls.Load() != 1 {
				t.Fatalf("expected one pooled CONNECT, got %d", p.calls.Load())
			}
			options = append(options, WithProxy(p.server.URL))
			bad := testClient(t, origin.URL, options...)
			before := requests.Load()
			_, e := bad.Solve(context.Background(), goodAnchor)
			requireCode(t, e, ProxyAuthError)
			if requests.Load() != before {
				t.Fatal("auth failure reached origin")
			}
		})
	}
}
func TestBrokenProxyNeverDirectAndEnvironmentIgnored(t *testing.T) {
	origin, roots, requests := protocolFixture(t)
	for _, name := range []string{"HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"} {
		t.Setenv(name, "http://127.0.0.1:1")
	}
	t.Setenv("NO_PROXY", "*")
	t.Setenv("no_proxy", "*")
	direct := testClient(t, origin.URL, WithRootCAs(roots), WithOffline(true))
	if _, e := direct.Solve(context.Background(), goodAnchor, WithAction("explicit-action")); e != nil {
		t.Fatal(e)
	}
	before := requests.Load()
	broken := testClient(t, origin.URL, WithRootCAs(roots), WithProxy("http://127.0.0.1:1"), WithOffline(true))
	_, e := broken.Solve(context.Background(), goodAnchor)
	requireCode(t, e, ProxyConnectError)
	if requests.Load() != before {
		t.Fatal("broken proxy silently fell back to direct")
	}
}

func TestProxyDoesNotMisclassifyTargetFailures(t *testing.T) {
	origin, roots, requests := protocolFixture(t)
	proxy := newProxyFixture(t, "", "", nil)
	untrusted := testClient(t, origin.URL, WithProxy(proxy.server.URL), WithOffline(true))
	before := requests.Load()
	_, e := untrusted.Solve(context.Background(), goodAnchor)
	requireCode(t, e, TransportError)
	if requests.Load() != before {
		t.Fatal("untrusted target TLS was accepted")
	}
	target := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { w.WriteHeader(http.StatusServiceUnavailable) }))
	defer target.Close()
	statusClient := testClient(t, target.URL, WithProxy(proxy.server.URL), WithRootCAs(roots), WithOffline(true))
	_, e = statusClient.Solve(context.Background(), goodAnchor)
	requireCode(t, e, HTTPStatusError)
}
