package anchorpass

import (
	"context"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"sync"
	"sync/atomic"
	"testing"
)

func TestGETWireRemainsBodyless(t *testing.T) {
	origin := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, e := io.ReadAll(r.Body)
		if e != nil || len(body) != 0 || r.ContentLength != 0 || len(r.TransferEncoding) != 0 || r.Header.Get("Content-Length") != "" || r.Header.Get("Transfer-Encoding") != "" {
			t.Error("GET wire body semantics changed")
		}
		fmt.Fprint(w, "fixture")
	}))
	defer origin.Close()
	proxy := newProxyFixture(t, "", "", nil)
	for _, throughProxy := range []bool{false, true} {
		options := []Option{WithOffline(true)}
		if throughProxy {
			options = append(options, WithProxy(proxy.server.URL))
		}
		c := testClient(t, "", options...)
		if _, e := fetch(context.Background(), c.http, c.settings.proxy, http.MethodGet, origin.URL, nil, maxResponseBytes, "version"); e != nil {
			t.Fatal(e)
		}
	}
}
func TestGETRetryBudgetAcrossStalePooledConnections(t *testing.T) {
	var staleRequests atomic.Int64
	var initial sync.WaitGroup
	initial.Add(3)
	release := make(chan struct{})
	var connections sync.Map
	origin := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if _, seen := connections.LoadOrStore(r.RemoteAddr, true); seen {
			staleRequests.Add(1)
			conn, _, e := w.(http.Hijacker).Hijack()
			if e != nil {
				t.Error(e)
				return
			}
			_ = conn.Close()
			return
		}
		if r.URL.Path == "/warm" {
			initial.Done()
			<-release
		}
		fmt.Fprint(w, "fixture")
	}))
	defer origin.Close()
	c := testClient(t, "", WithOffline(true))
	var group sync.WaitGroup
	for i := 0; i < 3; i++ {
		group.Add(1)
		go func() {
			defer group.Done()
			if _, e := fetch(context.Background(), c.http, nil, http.MethodGet, origin.URL+"/warm", nil, maxResponseBytes, "version"); e != nil {
				t.Error(e)
			}
		}()
	}
	initial.Wait()
	close(release)
	group.Wait()
	_, e := fetch(context.Background(), c.http, nil, http.MethodGet, origin.URL+"/retry", nil, maxResponseBytes, "version")
	requireCode(t, e, TransportError)
	if got := staleRequests.Load(); got < 1 || got > 2 {
		t.Fatalf("GET attempted %d stale connections; budget is two", got)
	}
}
