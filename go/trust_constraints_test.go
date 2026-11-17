package anchorpass

import (
	"context"
	"crypto/ed25519"
	"crypto/rand"
	"crypto/tls"
	"crypto/x509"
	"errors"
	"math/big"
	"net/http"
	"net/http/httptest"
	"sync/atomic"
	"testing"
	"time"
)

func constrainedTLSLeaf(t *testing.T) (tls.Certificate, *x509.CertPool, *x509.CertPool) {
	t.Helper()
	ca, broad := testTLS(t)
	parent, err := x509.ParseCertificate(ca.Certificate[0])
	if err != nil {
		t.Fatal(err)
	}
	pub, priv, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	template := &x509.Certificate{SerialNumber: big.NewInt(2), DNSNames: []string{"localhost"}, NotBefore: time.Now().Add(-time.Hour), NotAfter: time.Now().Add(time.Hour), KeyUsage: x509.KeyUsageDigitalSignature, ExtKeyUsage: []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth}}
	der, err := x509.CreateCertificate(rand.Reader, template, parent, pub, ca.PrivateKey)
	if err != nil {
		t.Fatal(err)
	}
	strict := x509.NewCertPool()
	strict.AddCertWithConstraint(parent, func([]*x509.Certificate) error { return errors.New("strict TLS policy rejection") })
	if !broad.Equal(strict) {
		t.Fatal("negative control: pool equality changed")
	}
	return tls.Certificate{Certificate: [][]byte{der}, PrivateKey: priv}, broad, strict
}

func TestCustomTLSConstraintsCannotShareDownloadedBundles(t *testing.T) {
	for _, boundary := range []string{"target", "proxy"} {
		t.Run(boundary, func(t *testing.T) {
			signer, trust := testTrust(t)
			raw := signedPayload(t, signer, testPayload(t, "2"))
			var downloads atomic.Int64
			targetCert, targetRoots := testTLS(t)
			proxyCert, proxyRoots := testTLS(t)
			leaf, broadPool, strictPool := constrainedTLSLeaf(t)
			if boundary == "target" {
				targetCert = leaf
				targetRoots = broadPool
			} else {
				proxyCert = leaf
				proxyRoots = broadPool
			}
			origin := httptest.NewUnstartedServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { downloads.Add(1); _, _ = w.Write(raw) }))
			origin.TLS = &tls.Config{Certificates: []tls.Certificate{targetCert}}
			origin.StartTLS()
			defer origin.Close()
			proxy := newProxyFixture(t, "", "", &proxyCert)
			options := []Option{WithBundleURL(localhostURL(origin.URL)), WithTrustedKeys(trust), WithRootCAs(targetRoots), WithProxyRootCAs(proxyRoots), WithUpdatesProxy(UpdatesVia(localhostURL(proxy.server.URL)))}
			broad, err := NewClient(options...)
			if err != nil {
				t.Fatal(err)
			}
			defer broad.Close()
			strictOptions := append([]Option(nil), options...)
			expectedCode := ProxyConnectError
			if boundary == "target" {
				strictOptions = append(strictOptions, WithRootCAs(strictPool))
				expectedCode = TransportError
			} else {
				strictOptions = append(strictOptions, WithProxyRootCAs(strictPool))
			}
			strict, err := NewClient(strictOptions...)
			if err != nil {
				t.Fatal(err)
			}
			defer strict.Close()
			_, err = strict.Refresh(context.Background())
			requireCode(t, err, expectedCode)
			if downloads.Load() != 0 {
				t.Fatal("strict negative control unexpectedly reached target")
			}
			_, err = strict.LoadBG(context.Background())
			requireCode(t, err, expectedCode)
			info, err := broad.Refresh(context.Background())
			if err != nil {
				t.Fatal(err)
			}
			if info.Revision != "2" {
				t.Fatal("broad client did not verify fresh signed bundle")
			}
			_, err = strict.LoadBG(context.Background())
			requireCode(t, err, expectedCode)
			if bg, err := broad.LoadBG(context.Background()); err != nil || bg == "" {
				t.Fatalf("broad client lost verified bundle: %v", err)
			}
			if downloads.Load() != 1 {
				t.Fatal("unexpected network download count")
			}
			_, err = strict.Refresh(context.Background())
			requireCode(t, err, expectedCode)
			t.Logf("boundary=%s strict_refresh_before_and_after=%s broad_verified_revision=%s strict_LoadBG=DENIED downloads=%d", boundary, expectedCode, info.Revision, downloads.Load())
		})
	}
}
