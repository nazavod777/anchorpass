package anchorpass

import (
	"context"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
)

func TestUserAgentPerOperationAndHelpers(t *testing.T) {
	var mu sync.Mutex
	captured := map[string]int{}
	origin := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		mu.Lock()
		captured[r.UserAgent()]++
		mu.Unlock()
		switch {
		case strings.HasSuffix(r.URL.Path, ".js"):
			fmt.Fprint(w, "/recaptcha/releases/test-version/recaptcha.js")
		case strings.HasSuffix(r.URL.Path, "/anchor"):
			fmt.Fprint(w, `<input id="recaptcha-token" value="anchor">`)
		default:
			fmt.Fprint(w, `["rresp","fixture-token"]`)
		}
	}))
	defer origin.Close()
	c := testClient(t, origin.URL, WithOffline(true))
	var workers sync.WaitGroup
	for _, ua := range []string{"first/1", "second/2"} {
		workers.Add(1)
		go func(ua string) {
			defer workers.Done()
			if _, err := c.Solve(context.Background(), goodAnchor, WithUserAgent(ua)); err != nil {
				t.Error(err)
			}
		}(ua)
	}
	workers.Wait()
	if _, err := c.Solve(context.Background(), goodAnchor); err != nil {
		t.Fatal(err)
	}
	privateOrigin := Option{apply: func(s *settings) error { s.testOrigin = origin.URL; return nil }}
	if _, err := Solve(context.Background(), goodAnchor, WithOffline(true), privateOrigin, WithUserAgent("helper/3")); err != nil {
		t.Fatal(err)
	}
	mu.Lock()
	defer mu.Unlock()
	for _, ua := range []string{"first/1", "second/2", "helper/3", "anchorpass/0.1.1"} {
		if captured[ua] != 3 {
			t.Errorf("User-Agent %q: got %d requests", ua, captured[ua])
		}
	}
}

func TestInvalidUserAgentBeforeNetwork(t *testing.T) {
	origin, roots, requests := protocolFixture(t)
	c := testClient(t, origin.URL, WithRootCAs(roots), WithBundleURL(origin.URL+"/update"))
	for _, ua := range []string{"", " ", "\t", "a\rb", "a\nb", "a\x00b", "a\x7fb", "привет", strings.Repeat("a", 1025)} {
		_, err := c.Solve(context.Background(), goodAnchor, WithUserAgent(ua))
		requireCode(t, err, InvalidInput)
		if e, ok := err.(*Error); !ok || e.Stage != "overrides" {
			t.Fatalf("unexpected error: %v", err)
		}
	}
	if requests.Load() != 0 {
		t.Fatal("invalid User-Agent reached network")
	}
	if _, err := NewClient(WithUserAgent("agent")); err == nil {
		t.Fatal("solve-only option accepted by client")
	}
	if _, err := LoadBG(context.Background(), WithUserAgent("agent")); err == nil {
		t.Fatal("solve-only option accepted by LoadBG")
	}
}
