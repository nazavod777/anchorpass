package main

import (
	"compress/gzip"
	"context"
	"crypto/x509"
	"encoding/pem"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

func testJob(t *testing.T, candidate string) job {
	t.Helper()
	server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/recaptcha/api.js":
			if r.Header.Get("Cookie") != "" {
				t.Error("flow cookie leaked between operations")
			}
			w.Header().Set("Set-Cookie", "operation=42; Path=/")
			_, _ = fmt.Fprint(w, "/recaptcha/releases/matrix-version/recaptcha.js")
		case "/recaptcha/api2/anchor":
			if r.Header.Get("Cookie") != "operation=42" || r.URL.Query().Get("v") != "matrix-version" {
				t.Error("flow anchor state missing")
			}
			_, _ = fmt.Fprint(w, `<input id="recaptcha-token" value="anchor-42">`)
		case "/recaptcha/api2/reload":
			if r.Method != "POST" || r.Header.Get("Cookie") != "operation=42" {
				t.Error("flow reload route/cookie incorrect")
			}
			if err := r.ParseForm(); err != nil {
				t.Error(err)
			}
			if r.PostForm.Get("c") != "anchor-42" || r.PostForm.Get("bg") != "matrix-bg" || r.PostForm.Get("v") != "matrix-version" {
				t.Error("flow form missing fields")
			}
			_, _ = fmt.Fprint(w, `["rresp","fixture-token"]`)
		case "/gzip":
			w.Header().Set("Content-Encoding", "gzip")
			z := gzip.NewWriter(w)
			_, _ = z.Write([]byte(strings.Repeat("x", 4096)))
			_ = z.Close()
		case "/drip":
			for i := 0; i < 32; i++ {
				if _, err := fmt.Fprint(w, "x"); err != nil {
					return
				}
				w.(http.Flusher).Flush()
				select {
				case <-r.Context().Done():
					return
				case <-time.After(10 * time.Millisecond):
				}
			}
		case "/status":
			w.WriteHeader(429)
		case "/redirect":
			http.Redirect(w, r, "/bytes", 302)
		default:
			_, _ = fmt.Fprint(w, strings.Repeat("x", 1024))
		}
	}))
	t.Cleanup(server.Close)
	ca := filepath.Join(t.TempDir(), "ca.pem")
	cert, err := x509.ParseCertificate(server.TLS.Certificates[0].Certificate[0])
	if err != nil {
		t.Fatal(err)
	}
	if err = os.WriteFile(ca, pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: cert.Raw}), 0600); err != nil {
		t.Fatal(err)
	}
	return job{SchemaVersion: 1, RunID: "test", Candidate: candidate, Fixture: fixture{HTTPSURL: server.URL, CAFile: ca, ProxyCAFile: ca}, Profile: profile{Kind: "transport", Route: "direct", ConnectionMode: "warm", Concurrency: 2, Warmup: 1, Requests: 2, TimeoutMS: 1000, MaxResponseBytes: 2048, ExpectedBytes: 1024, Path: "/bytes"}}
}
func TestBoundedResponsesAndDeadline(t *testing.T) {
	for _, candidate := range []string{"go-net-http", "go-resty", "go-fasthttp"} {
		t.Run(candidate, func(t *testing.T) {
			j := testJob(t, candidate)
			r, err := newRequester(j, j.Profile)
			if err != nil {
				t.Fatal(err)
			}
			defer r.close()
			if s := operate(j, j.Profile, r); !s.OK {
				t.Fatalf("success: %+v", s)
			}
			for _, tc := range []struct {
				path, category string
				timeout        int
			}{{"/gzip", "response_limit", 1000}, {"/status", "http_status", 1000}, {"/redirect", "http_status", 1000}, {"/drip", "deadline", 50}} {
				p := j.Profile
				p.Path = tc.path
				p.TimeoutMS = tc.timeout
				p.ExpectedError = &tc.category
				s := operate(j, p, r)
				if !s.OK || s.Error == nil || *s.Error != tc.category {
					t.Fatalf("%s: %+v", tc.path, s)
				}
			}
			if s := operate(j, j.Profile, r); !s.OK {
				t.Fatalf("recovery: %+v", s)
			}
		})
	}
}
func TestNoTransportRetry(t *testing.T) {
	for _, candidate := range []string{"go-net-http", "go-resty", "go-fasthttp"} {
		t.Run(candidate, func(t *testing.T) {
			j := testJob(t, candidate)
			var requests atomic.Int32
			server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if requests.Add(1) == 1 {
					_, _ = fmt.Fprint(w, strings.Repeat("x", 1024))
					return
				}
				c, _, err := w.(http.Hijacker).Hijack()
				if err == nil {
					_ = c.Close()
				}
			}))
			defer server.Close()
			// httptest uses the same local test certificate for both origins.
			j.Fixture.HTTPSURL = server.URL
			r, err := newRequester(j, j.Profile)
			if err != nil {
				t.Fatal(err)
			}
			defer r.close()
			if s := operate(j, j.Profile, r); !s.OK {
				t.Fatalf("warm connection: %+v", s)
			}
			expected := "transport"
			p := j.Profile
			p.ExpectedError = &expected
			if s := operate(j, p, r); !s.OK {
				t.Fatalf("reset: %+v", s)
			}
			if got := requests.Load(); got != 2 {
				t.Fatalf("hidden retry: received %d requests", got)
			}
		})
	}
}
func TestBatchCompletesEveryOperation(t *testing.T) {
	j := testJob(t, "go-net-http")
	r, err := newRequester(j, j.Profile)
	if err != nil {
		t.Fatal(err)
	}
	defer r.close()
	samples := batch(j, j.Profile, r, 7)
	if len(samples) != 7 {
		t.Fatal("missing results")
	}
	for _, s := range samples {
		if !s.OK {
			t.Fatalf("sample: %+v", s)
		}
	}
}
func TestClassifyDoesNotAcceptUnrelatedError(t *testing.T) {
	if got := classify(fmt.Errorf("arbitrary failure")); got != "unclassified" {
		t.Fatal(got)
	}
	if got := classify(context.DeadlineExceeded); got != "deadline" {
		t.Fatal(got)
	}
}

func TestFixtureValidationRejectsNonLoopbackWithoutRequests(t *testing.T) {
	j := testJob(t, "go-net-http")
	j.Fixture.BadTargetURL = j.Fixture.HTTPSURL
	j.Fixture.BundleURL = j.Fixture.HTTPSURL + "/bundle"
	j.Fixture.Proxies = map[string]string{"http": "http://localhost:1234"}
	if err := validate(j); err != nil {
		t.Fatal("valid loopback fixture rejected", err)
	}
	for _, field := range []*string{&j.Fixture.HTTPSURL, &j.Fixture.BadTargetURL, &j.Fixture.BundleURL} {
		saved := *field
		*field = "https://example.com/fixture"
		if err := validate(j); err == nil {
			t.Fatal("non-loopback fixture accepted")
		}
		*field = saved
	}
	j.Fixture.Proxies["http"] = "http://proxy.example.com:1234"
	if err := validate(j); err == nil {
		t.Fatal("non-loopback proxy accepted")
	}
	j.Fixture.Proxies["http"] = "http://[::1]:1234"
	j.Fixture.HTTPSURL = "https://[::1]:1235"
	if err := validate(j); err != nil {
		t.Fatal("IPv6 loopback fixture rejected", err)
	}
}

func TestFullFlowPreservesPostBodyAndIsolatesCookies(t *testing.T) {
	for _, candidate := range []string{"go-net-http", "go-resty", "go-fasthttp"} {
		t.Run(candidate, func(t *testing.T) {
			j := testJob(t, candidate)
			j.Profile.Kind = "flow"
			j.Fixture.FlowFields = map[string]string{"bg": "matrix-bg"}
			requester, err := newRequester(j, j.Profile)
			if err != nil {
				t.Fatal(err)
			}
			defer requester.close()
			for range 2 {
				if sample := operate(j, j.Profile, requester); !sample.OK {
					t.Fatalf("flow: %+v", sample)
				}
			}
		})
	}
}

func TestFasthttpHTTPSProxyStillHandshakesTargetTLS(t *testing.T) {
	j := testJob(t, "go-fasthttp")
	target, err := url.Parse(j.Fixture.HTTPSURL)
	if err != nil {
		t.Fatal(err)
	}
	proxy := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodConnect || r.Host != target.Host {
			http.Error(w, "invalid CONNECT", 400)
			return
		}
		upstream, err := net.DialTimeout("tcp", target.Host, time.Second)
		if err != nil {
			http.Error(w, "dial failed", 502)
			return
		}
		client, _, err := w.(http.Hijacker).Hijack()
		if err != nil {
			upstream.Close()
			t.Error(err)
			return
		}
		defer client.Close()
		defer upstream.Close()
		if _, err := io.WriteString(client, "HTTP/1.1 200 Connection Established\r\n\r\n"); err != nil {
			return
		}
		copied := make(chan struct{})
		go func() { _, _ = io.Copy(upstream, client); _ = upstream.Close(); close(copied) }()
		_, _ = io.Copy(client, upstream)
		_ = client.Close()
		<-copied
	}))
	defer proxy.Close()
	j.Profile.Kind = "flow"
	j.Profile.Route = "https"
	j.Fixture.FlowFields = map[string]string{"bg": "matrix-bg"}
	j.Fixture.Proxies = map[string]string{"https": proxy.URL}
	requester, err := newRequester(j, j.Profile)
	if err != nil {
		t.Fatal(err)
	}
	defer requester.close()
	if sample := operate(j, j.Profile, requester); !sample.OK {
		t.Fatalf("nested TLS flow: %+v", sample)
	}
}
