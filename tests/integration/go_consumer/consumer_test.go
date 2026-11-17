package smoke

import (
	"context"
	"crypto/ed25519"
	"crypto/rand"
	"crypto/tls"
	"crypto/x509"
	"encoding/base64"
	"fmt"
	"io"
	"math/big"
	"net"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	anchorpass "github.com/nazavod777/anchorpass/go"
)

func TestArchiveStandalone(t *testing.T) {
	pub, key, e := ed25519.GenerateKey(rand.Reader)
	if e != nil {
		t.Fatal(e)
	}
	template := &x509.Certificate{SerialNumber: big.NewInt(4), NotBefore: time.Now().Add(-time.Hour), NotAfter: time.Now().Add(time.Hour), DNSNames: []string{"www.google.com"}, KeyUsage: x509.KeyUsageDigitalSignature | x509.KeyUsageCertSign, ExtKeyUsage: []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth}, IsCA: true, BasicConstraintsValid: true}
	der, e := x509.CreateCertificate(rand.Reader, template, template, pub, key)
	if e != nil {
		t.Fatal(e)
	}
	cert, e := x509.ParseCertificate(der)
	if e != nil {
		t.Fatal(e)
	}
	roots := x509.NewCertPool()
	roots.AddCert(cert)
	var reloads atomic.Int64
	origin := httptest.NewUnstartedServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Host != "www.google.com" || r.TLS.ServerName != "www.google.com" || r.Header.Get("Proxy-Authorization") != "" {
			t.Error("target identity or credentials leaked")
		}
		switch {
		case strings.HasSuffix(r.URL.Path, ".js"):
			fmt.Fprint(w, "releases/archive-version/script.js")
		case strings.HasSuffix(r.URL.Path, "/anchor"):
			if r.URL.Query().Get("sa") != "archive-action" {
				t.Error("action absent")
			}
			fmt.Fprint(w, `<input id="recaptcha-token" value="archive-anchor">`)
		case strings.HasSuffix(r.URL.Path, "/reload"):
			reloads.Add(1)
			if e := r.ParseForm(); e != nil {
				t.Error(e)
			}
			if r.PostForm.Get("sa") != "archive-action" || r.PostForm.Get("v") != "archive-version" || r.PostForm.Get("bg") == "" {
				t.Error("incorrect protocol fields")
			}
			fmt.Fprint(w, `["rresp","archive-token"]`)
		}
	}))
	origin.TLS = &tls.Config{Certificates: []tls.Certificate{{Certificate: [][]byte{der}, PrivateKey: key}}}
	origin.StartTLS()
	defer origin.Close()
	var connects atomic.Int64
	auth := "Basic " + base64.StdEncoding.EncodeToString([]byte("test:pass"))
	proxy := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != "CONNECT" || r.Host != "www.google.com:443" || r.Header.Get("Proxy-Authorization") != auth {
			t.Error("unexpected proxy request")
			w.WriteHeader(407)
			return
		}
		connects.Add(1)
		upstream, e := net.DialTimeout("tcp", strings.TrimPrefix(origin.URL, "https://"), time.Second)
		if e != nil {
			t.Error(e)
			w.WriteHeader(502)
			return
		}
		downstream, buffer, e := w.(http.Hijacker).Hijack()
		if e != nil {
			upstream.Close()
			t.Error(e)
			return
		}
		fmt.Fprint(buffer, "HTTP/1.1 200 Connection Established\r\n\r\n")
		if e = buffer.Flush(); e != nil {
			upstream.Close()
			downstream.Close()
			return
		}
		go func() { defer upstream.Close(); defer downstream.Close(); _, _ = io.Copy(upstream, buffer) }()
		go func() { defer upstream.Close(); defer downstream.Close(); _, _ = io.Copy(downstream, upstream) }()
	}))
	defer proxy.Close()
	proxyURL := strings.Replace(proxy.URL, "://", "://test:pass@", 1)
	options := []anchorpass.Option{anchorpass.WithProxy(proxyURL), anchorpass.WithRootCAs(roots), anchorpass.WithOffline(true)}
	client, e := anchorpass.NewClient(options...)
	if e != nil {
		t.Fatal(e)
	}
	defer client.Close()
	onceBG, e := anchorpass.LoadBG(context.Background(), options...)
	if e != nil || onceBG == "" {
		t.Fatal("top-level embedded data unavailable", e)
	}
	bg, e := client.LoadBG(context.Background())
	if e != nil || bg == "" {
		t.Fatal("embedded data unavailable", e)
	}
	if action, e := anchorpass.ExtractAction("https://www.google.com/recaptcha/api2/anchor?k=fixture&co=fixture&sa=archive-action"); e != nil || action != "archive-action" {
		t.Fatal(e)
	}
	for _, family := range []string{"api2", "enterprise"} {
		result, e := client.Solve(context.Background(), "https://www.google.com/recaptcha/"+family+"/anchor?k=fixture&co=fixture", anchorpass.WithAction("archive-action"), anchorpass.WithBG("custom-bg"))
		if e != nil || result.Token != "archive-token" {
			t.Fatal("reusable client", e)
		}
	}
	once, e := anchorpass.Solve(context.Background(), "https://www.google.com/recaptcha/api2/anchor?k=fixture&co=fixture&sa=archive-action", options...)
	if e != nil || once.Token != "archive-token" {
		t.Fatal("one-shot", e)
	}
	if reloads.Load() != 3 || connects.Load() != 2 {
		t.Fatalf("reloads=%d connects=%d", reloads.Load(), connects.Load())
	}
	t.Logf("standalone archive: verified embedded BG, pure action, two reusable solves and one-shot via authenticated CONNECT; reloads=%d connects=%d", reloads.Load(), connects.Load())
}
