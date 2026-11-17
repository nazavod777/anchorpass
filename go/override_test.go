package anchorpass

import (
	"context"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
)

func TestExtractActionPure(t *testing.T) {
	for _, test := range []struct{ raw, want string }{{goodAnchor, "url-action"}, {"https://google.com/recaptcha/enterprise/anchor?k=x&co=y", ""}, {"https://google.com/recaptcha/api2/anchor?k=x&co=y&sa=hello%2Bworld", "hello+world"}} {
		got, e := ExtractAction(test.raw)
		if e != nil || got != test.want {
			t.Fatalf("action: %q %v", got, e)
		}
	}
	_, e := ExtractAction(goodAnchor + "&sa=duplicate")
	requireCode(t, e, InvalidInput)
}
func TestBGOverrideIsolationAndValidation(t *testing.T) {
	var calls atomic.Int64
	var mu sync.Mutex
	var seen []string
	origin := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls.Add(1)
		switch {
		case strings.HasSuffix(r.URL.Path, ".js"):
			fmt.Fprint(w, "releases/test-version/script.js")
		case strings.HasSuffix(r.URL.Path, "/anchor"):
			fmt.Fprint(w, `<input id="recaptcha-token" value="anchor-token">`)
		case strings.HasSuffix(r.URL.Path, "/reload"):
			if e := r.ParseForm(); e != nil {
				t.Error(e)
			}
			mu.Lock()
			seen = append(seen, r.PostForm.Get("bg"))
			mu.Unlock()
			fmt.Fprint(w, `["rresp","fixture-token"]`)
		}
	}))
	defer origin.Close()
	c := testClient(t, origin.URL, WithOffline(true))
	original, e := c.LoadBG(context.Background())
	if e != nil || original == "" {
		t.Fatal(e)
	}
	for _, override := range []string{"", strings.Repeat("x", 16385), string([]byte{0xff})} {
		_, e := c.Solve(context.Background(), goodAnchor, WithBG(override))
		requireCode(t, e, InvalidInput)
	}
	if calls.Load() != 0 {
		t.Fatal("invalid override or LoadBG accessed network")
	}
	custom := "custom+bg&value =/%"
	if _, e := c.Solve(context.Background(), goodAnchor, WithBG(custom)); e != nil {
		t.Fatal(e)
	}
	if _, e := c.Solve(context.Background(), goodAnchor); e != nil {
		t.Fatal(e)
	}
	after, e := c.LoadBG(context.Background())
	if e != nil || after != original {
		t.Fatal("override mutated shared signed data")
	}
	mu.Lock()
	defer mu.Unlock()
	if len(seen) != 2 || seen[0] != custom || seen[1] != original {
		t.Fatal("bg override leaked into next operation")
	}
}

func TestActionValidationAndEmptyURLOverride(t *testing.T) {
	var calls atomic.Int64
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) { calls.Add(1); fmt.Fprint(w, "not-a-version") }))
	defer server.Close()
	c := testClient(t, server.URL, WithOffline(true))
	emptyURL := "https://www.google.com/recaptcha/api2/anchor?k=site&co=origin&sa="
	for _, action := range []string{"", strings.Repeat("x", 257), string([]byte{0xff})} {
		_, e := c.Solve(context.Background(), goodAnchor, WithAction(action))
		requireCode(t, e, InvalidInput)
	}
	_, e := c.Solve(context.Background(), emptyURL)
	requireCode(t, e, InvalidInput)
	if calls.Load() != 0 {
		t.Fatal("invalid action reached network")
	}
	if action, e := ExtractAction(emptyURL); e != nil || action != "" {
		t.Fatal("pure extraction did not preserve empty action")
	}
	_, e = c.Solve(context.Background(), emptyURL, WithAction("replaced"))
	requireCode(t, e, ProtocolError)
	if calls.Load() != 1 {
		t.Fatal("explicit action did not replace empty URL action")
	}
	if bg, e := LoadBG(context.Background(), WithOffline(true)); e != nil || bg == "" {
		t.Fatal("top-level bundled BG unavailable", e)
	}
}
