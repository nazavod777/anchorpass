package anchorpass

import (
	"context"
	"crypto/tls"
	"crypto/x509"
	"encoding/base64"
	"fmt"
	"net"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

func localhostURL(address string) string {
	return strings.Replace(address, "127.0.0.1", "localhost", 1)
}

func TestHTTPSProxySeparatesTargetAndProxyTrust(t *testing.T) {
	origin, targetRoots, requests := protocolFixture(t, "localhost")
	targetCertificate := origin.TLS.Certificates[0]
	proxyCertificate, proxyRoots := testTLS(t)
	auth := "Basic " + base64.StdEncoding.EncodeToString([]byte("proxy-user:proxy-password"))
	proxy := newProxyFixture(t, "", auth, &proxyCertificate)
	proxyURL := strings.Replace(localhostURL(proxy.server.URL), "://", "://proxy-user:proxy-password@", 1)
	targetURL := localhostURL(origin.URL)
	options := []Option{WithRootCAs(targetRoots), WithProxyRootCAs(proxyRoots), WithProxy(proxyURL), WithOffline(true)}
	healthy := testClient(t, targetURL, options...)
	if _, err := healthy.Solve(context.Background(), goodAnchor, WithAction("explicit-action")); err != nil {
		t.Fatal(err)
	}
	if proxy.calls.Load() != 1 {
		t.Fatal("healthy nested TLS did not reuse CONNECT")
	}

	// Both certificates cover localhost, so only independent roots can reject this target.
	wrongTarget := httptest.NewUnstartedServer(origin.Config.Handler)
	wrongTarget.TLS = &tls.Config{Certificates: []tls.Certificate{proxyCertificate}}
	wrongTarget.StartTLS()
	defer wrongTarget.Close()
	badTarget := testClient(t, localhostURL(wrongTarget.URL), options...)
	before := requests.Load()
	_, err := badTarget.Solve(context.Background(), goodAnchor, WithAction("explicit-action"))
	requireCode(t, err, TransportError)
	if requests.Load() != before {
		t.Fatal("target signed by proxy-only root reached HTTP handler")
	}

	wrongProxy := newProxyFixture(t, "", auth, &targetCertificate)
	wrongProxyURL := strings.Replace(localhostURL(wrongProxy.server.URL), "://", "://proxy-user:proxy-password@", 1)
	badProxy := testClient(t, targetURL, WithRootCAs(targetRoots), WithProxyRootCAs(proxyRoots), WithProxy(wrongProxyURL), WithOffline(true))
	_, err = badProxy.Solve(context.Background(), goodAnchor, WithAction("explicit-action"))
	requireCode(t, err, ProxyConnectError)
	if wrongProxy.calls.Load() != 0 || requests.Load() != before {
		t.Fatal("untrusted HTTPS proxy reached HTTP or fell back directly")
	}

	// A custom target pool must not implicitly change the default proxy policy.
	defaultProxyTrust := testClient(t, targetURL, WithRootCAs(targetRoots), WithProxy(proxyURL), WithOffline(true))
	_, err = defaultProxyTrust.Solve(context.Background(), goodAnchor)
	requireCode(t, err, ProxyConnectError)
	// Nor may a custom proxy pool implicitly change the default target policy.
	defaultTargetTrust := testClient(t, targetURL, WithProxyRootCAs(proxyRoots), WithProxy(proxyURL), WithOffline(true))
	_, err = defaultTargetTrust.Solve(context.Background(), goodAnchor)
	requireCode(t, err, TransportError)
	if requests.Load() != before {
		t.Fatal("default trust policy silently inherited the other custom pool")
	}
}

func TestHTTPSUpdateTrustIsolation(t *testing.T) {
	key, signingTrust := testTrust(t)
	raw := signedPayload(t, key, testPayload(t, "2"))
	targetCertificate, targetRoots := testTLS(t)
	proxyCertificate, proxyRoots := testTLS(t)
	_, differentProxyRoots := testTLS(t) // Same subject and hostname, distinct CA key.
	var downloads atomic.Int64
	origin := httptest.NewUnstartedServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		downloads.Add(1)
		if r.Header.Get("Proxy-Authorization") != "" {
			t.Error("proxy credentials reached update target")
		}
		_, _ = w.Write(raw)
	}))
	origin.TLS = &tls.Config{Certificates: []tls.Certificate{targetCertificate}}
	origin.StartTLS()
	defer origin.Close()
	proxy := newProxyFixture(t, "", "", &proxyCertificate)
	options := []Option{WithRootCAs(targetRoots), WithProxyRootCAs(proxyRoots), WithTrustedKeys(signingTrust), WithBundleURL(localhostURL(origin.URL)), WithUpdatesProxy(UpdatesVia(localhostURL(proxy.server.URL)))}
	first := testClient(t, "", options...)
	matching := testClient(t, "", options...)
	different := testClient(t, "", append(options, WithProxyRootCAs(differentProxyRoots))...)
	if first.bundleState == matching.bundleState {
		t.Fatal("custom trust policy shared update state across clients")
	}
	if first.bundleState == different.bundleState {
		t.Fatal("different proxy CA keys with same subject shared verified download")
	}
	if _, err := first.LoadBG(context.Background()); err != nil {
		t.Fatal(err)
	}
	if _, err := matching.LoadBG(context.Background()); err != nil {
		t.Fatal(err)
	}
	if got := downloads.Load(); got != 2 {
		t.Fatalf("isolated clients downloaded %d times", got)
	}
	// Mutating the caller's pool must not alter an already constructed client.
	cert, err := x509.ParseCertificate(proxyCertificate.Certificate[0])
	if err != nil {
		t.Fatal(err)
	}
	differentProxyRoots.AddCert(cert)
	_, err = different.LoadBG(context.Background())
	requireCode(t, err, ProxyConnectError)
	if downloads.Load() != 2 {
		t.Fatal("untrusted proxy fetched updates or reused another policy's snapshot")
	}

	differentTarget := testClient(t, "", append(options, WithRootCAs(proxyRoots))...)
	if differentTarget.bundleState == first.bundleState {
		t.Fatal("different target roots shared update state")
	}
	_, err = differentTarget.LoadBG(context.Background())
	requireCode(t, err, TransportError)
	if downloads.Load() != 2 {
		t.Fatal("untrusted update target reached HTTP handler")
	}
}

func TestProxyRootCAsRejectsNil(t *testing.T) {
	_, err := NewClient(WithProxyRootCAs(nil))
	requireCode(t, err, InvalidInput)
}

func TestHTTPSProxyTLSHandshakeHonorsOperationDeadline(t *testing.T) {
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	defer listener.Close()
	accepted := make(chan net.Conn, 1)
	finished := make(chan struct{})
	go func() {
		defer close(finished)
		conn, e := listener.Accept()
		if e == nil {
			accepted <- conn
		}
	}()
	client := testClient(t, "", WithProxy("https://"+listener.Addr().String()), WithTimeout(80*time.Millisecond), WithOffline(true))
	start := time.Now()
	_, err = client.Solve(context.Background(), goodAnchor)
	requireCode(t, err, DeadlineExceeded)
	if time.Since(start) > 500*time.Millisecond {
		t.Fatal("proxy handshake exceeded operation deadline")
	}
	select {
	case conn := <-accepted:
		_ = conn.Close()
	case <-time.After(time.Second):
		t.Fatal("proxy did not receive connection")
	}
	select {
	case <-finished:
	case <-time.After(time.Second):
		t.Fatal("owned accept did not finish")
	}
}

func TestHTTPSProxyGETRetryBudget(t *testing.T) {
	targetCertificate, targetRoots := testTLS(t)
	proxyCertificate, proxyRoots := testTLS(t)
	var calls atomic.Int64
	origin := httptest.NewUnstartedServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if calls.Add(1) > 1 {
			conn, _, err := w.(http.Hijacker).Hijack()
			if err == nil {
				_ = conn.Close()
			}
			return
		}
		fmt.Fprint(w, "fixture")
	}))
	origin.TLS = &tls.Config{Certificates: []tls.Certificate{targetCertificate}}
	origin.StartTLS()
	defer origin.Close()
	proxy := newProxyFixture(t, "", "", &proxyCertificate)
	c := testClient(t, "", WithProxy(localhostURL(proxy.server.URL)), WithRootCAs(targetRoots), WithProxyRootCAs(proxyRoots), WithOffline(true))
	if _, err := fetch(context.Background(), c.http, c.settings.proxy, http.MethodGet, localhostURL(origin.URL), nil, maxResponseBytes, "version"); err != nil {
		t.Fatal(err)
	}
	_, err := fetch(context.Background(), c.http, c.settings.proxy, http.MethodGet, localhostURL(origin.URL), nil, maxResponseBytes, "version")
	requireCode(t, err, TransportError)
	// One warm request, one failed reused GET, then its single explicit retry.
	if got := calls.Load(); got != 3 {
		t.Fatalf("unexpected retry count: %d", got)
	}
}
