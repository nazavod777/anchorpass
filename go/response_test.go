package anchorpass

import (
	"compress/gzip"
	"context"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
)

func TestDecompressedResponseBound(t *testing.T) {
	origin := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Encoding", "gzip")
		writer := gzip.NewWriter(w)
		_, _ = writer.Write([]byte(strings.Repeat("x", maxResponseBytes+1)))
		_ = writer.Close()
	}))
	defer origin.Close()
	c := testClient(t, origin.URL, WithOffline(true))
	_, e := c.Solve(context.Background(), goodAnchor)
	requireCode(t, e, ResourceLimit)
}
func TestReloadNeverRetries(t *testing.T) {
	var reloads atomic.Int64
	origin := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch {
		case strings.HasSuffix(r.URL.Path, ".js"):
			fmt.Fprint(w, "releases/test-version/script.js")
		case strings.HasSuffix(r.URL.Path, "/anchor"):
			fmt.Fprint(w, `<input id="recaptcha-token" value="fixture">`)
		case strings.HasSuffix(r.URL.Path, "/reload"):
			reloads.Add(1)
			conn, _, e := w.(http.Hijacker).Hijack()
			if e != nil {
				t.Error(e)
				return
			}
			_ = conn.Close()
		}
	}))
	defer origin.Close()
	c := testClient(t, origin.URL, WithOffline(true))
	_, e := c.Solve(context.Background(), goodAnchor)
	requireCode(t, e, TransportError)
	if reloads.Load() != 1 {
		t.Fatalf("POST replayed %d times", reloads.Load())
	}
}
