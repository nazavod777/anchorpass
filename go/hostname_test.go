package anchorpass

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"strings"
	"sync"
	"testing"
)

func TestSharedSolveOptionVectors(t *testing.T) {
	raw, err := os.ReadFile("testdata/solve-options.json")
	if err != nil {
		t.Fatal(err)
	}
	var vectors struct {
		Hostname []struct {
			Co, Hostname string
			Valid        bool
			ExpectedCo   string `json:"expected_co"`
		}
		UserAgent []struct {
			Value string
			Valid bool
		} `json:"user_agent"`
	}
	if err = json.Unmarshal(raw, &vectors); err != nil {
		t.Fatal(err)
	}
	for _, v := range vectors.Hostname {
		actual, err := hostnameOrigin(v.Co, v.Hostname)
		if (err == nil) != v.Valid || v.Valid && actual != v.ExpectedCo {
			t.Errorf("hostname %q co %q: got %q %v", v.Hostname, v.Co, actual, err)
		}
	}
	for _, v := range vectors.UserAgent {
		err := WithUserAgent(v.Value).apply(&settings{})
		if (err == nil) != v.Valid {
			t.Errorf("User-Agent %q: %v", v.Value, err)
		}
	}
}

func TestHostnamePerOperationAndNoLeakage(t *testing.T) {
	var mu sync.Mutex
	captured := map[string]int{}
	origin := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch {
		case strings.HasSuffix(r.URL.Path, ".js"):
			fmt.Fprint(w, "releases/test-version/script.js")
		case strings.HasSuffix(r.URL.Path, "/anchor"):
			mu.Lock()
			captured[r.URL.Query().Get("co")]++
			mu.Unlock()
			fmt.Fprint(w, `<input id="recaptcha-token" value="anchor">`)
		default:
			if err := r.ParseForm(); err != nil {
				t.Error(err)
			}
			mu.Lock()
			captured[r.PostForm.Get("co")]++
			mu.Unlock()
			fmt.Fprint(w, `["rresp","fixture-token"]`)
		}
	}))
	defer origin.Close()
	c := testClient(t, origin.URL, WithOffline(true))
	encoded := "aHR0cHM6Ly9vbGQuZXhhbXBsZTo4NDQz"
	input := "https://www.google.com/recaptcha/api2/anchor?k=x&co=" + url.QueryEscape(encoded)
	var workers sync.WaitGroup
	for _, hostname := range []string{"first.example", "second.example"} {
		workers.Add(1)
		go func(hostname string) {
			defer workers.Done()
			if _, err := c.Solve(context.Background(), input, WithHostname(hostname)); err != nil {
				t.Error(err)
			}
		}(hostname)
	}
	workers.Wait()
	if _, err := c.Solve(context.Background(), input); err != nil {
		t.Fatal(err)
	}
	// A legacy non-origin co remains untouched unless a hostname override is selected.
	if _, err := c.Solve(context.Background(), goodAnchor); err != nil {
		t.Fatal(err)
	}
	privateOrigin := Option{apply: func(s *settings) error { s.testOrigin = origin.URL; return nil }}
	if _, err := Solve(context.Background(), input, privateOrigin, WithOffline(true), WithHostname("helper.example")); err != nil {
		t.Fatal(err)
	}
	mu.Lock()
	defer mu.Unlock()
	for _, hostname := range []string{"first.example", "second.example", "helper.example"} {
		expected, err := hostnameOrigin(encoded, hostname)
		if err != nil {
			t.Fatal(err)
		}
		if captured[expected] != 2 {
			t.Errorf("hostname %q: got %d", hostname, captured[expected])
		}
	}
	if captured[encoded] != 2 {
		t.Error("hostname override leaked into next operation")
	}
	parsed, _ := parseAnchor(goodAnchor)
	if captured[parsed.values.Get("co")] != 2 {
		t.Error("legacy co was modified")
	}
}

func TestInvalidHostnameBeforeNetwork(t *testing.T) {
	origin, roots, requests := protocolFixture(t)
	c := testClient(t, origin.URL, WithRootCAs(roots), WithBundleURL(origin.URL+"/update"))
	for _, hostname := range []string{"", "bad.example:443", "https://new.example", "a\rb", "пример.рф"} {
		_, err := c.Solve(context.Background(), goodAnchor, WithHostname(hostname))
		requireCode(t, err, InvalidInput)
	}
	_, err := c.Solve(context.Background(), goodAnchor, WithHostname("valid.example"))
	requireCode(t, err, InvalidInput)
	if requests.Load() != 0 {
		t.Fatal("invalid override reached network")
	}
}
