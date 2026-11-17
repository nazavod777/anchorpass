package anchorpass

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"
)

func TestAuthenticatedExpiredUpdateEstablishesFloor(t *testing.T) {
	for _, disk := range []bool{false, true} {
		for _, force := range []bool{false, true} {
			t.Run(fmt.Sprintf("disk=%v/force=%v", disk, force), func(t *testing.T) {
				key, trust := testTrust(t)
				var response atomic.Value
				response.Store(signedPayload(t, key, testPayload(t, "2")))
				server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
					_, _ = w.Write(response.Load().([]byte))
				}))
				defer server.Close()
				options := []Option{WithTrustedKeys(trust), WithBundleURL(server.URL), WithRefreshInterval(0)}
				path := filepath.Join(t.TempDir(), "bundle.json")
				if disk {
					options = append(options, WithCachePath(path))
				}
				client := testClient(t, "", options...)
				if _, err := client.Refresh(context.Background()); err != nil {
					t.Fatal(err)
				}
				var payload map[string]any
				if err := json.Unmarshal(testPayload(t, "4"), &payload); err != nil {
					t.Fatal(err)
				}
				payload["issued_at"] = time.Now().Add(-2 * time.Hour).Unix()
				payload["expires_at"] = time.Now().Add(-time.Hour).Unix()
				raw, err := json.Marshal(payload)
				if err != nil {
					t.Fatal(err)
				}
				expired := signedPayload(t, key, raw)
				response.Store(expired)
				_, err = client.selectBundle(context.Background(), time.Now().Add(time.Second), force)
				requireCode(t, err, ConfigExpired)
				if client.bundleState.current.Revision != "4" {
					t.Fatal("authenticated expired revision did not establish memory floor")
				}
				if disk {
					stored, err := os.ReadFile(path)
					if err != nil || string(stored) != string(expired) {
						t.Fatal("expired floor was not persisted", err)
					}
					client.Close()
					client = testClient(t, "", options...)
				}
				response.Store(signedPayload(t, key, testPayload(t, "3")))
				_, err = client.Refresh(context.Background())
				requireCode(t, err, ConfigRollback)
				response.Store(signedPayload(t, key, testPayload(t, "4")))
				_, err = client.Refresh(context.Background())
				requireCode(t, err, ConfigInvalid)
				wrongKey, _ := testTrust(t)
				response.Store(signedPayload(t, wrongKey, testPayload(t, "9")))
				_, err = client.Refresh(context.Background())
				requireCode(t, err, ConfigInvalid)
				response.Store(signedPayload(t, key, testPayload(t, "5")))
				info, err := client.Refresh(context.Background())
				if err != nil || info.Revision != "5" {
					t.Fatal("invalid signature raised the floor", err)
				}
			})
		}
	}
}

func TestSignedTimestampsRequireIntegers(t *testing.T) {
	key, trust := testTrust(t)
	var payload map[string]any
	if err := json.Unmarshal(testPayload(t, "2"), &payload); err != nil {
		t.Fatal(err)
	}
	for _, field := range []string{"issued_at", "expires_at"} {
		saved := payload[field]
		for _, value := range []any{nil, false, "0", 0.5} {
			payload[field] = value
			raw, err := json.Marshal(payload)
			if err != nil {
				t.Fatal(err)
			}
			_, err = verifyBundle(signedPayload(t, key, raw), trust, time.Now())
			requireCode(t, err, ConfigInvalid)
		}
		payload[field] = saved
	}
	payload["issued_at"] = 0
	raw, err := json.Marshal(payload)
	if err != nil {
		t.Fatal(err)
	}
	if _, err = verifyBundle(signedPayload(t, key, raw), trust, time.Now()); err != nil {
		t.Fatal("zero integer issue time must remain valid", err)
	}
}

func TestSelectedURLActionBoundBeforeNetwork(t *testing.T) {
	var requests atomic.Int64
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		requests.Add(1)
		fmt.Fprint(w, "missing version")
	}))
	defer server.Close()
	client := testClient(t, server.URL, WithOffline(true), WithBundleURL(server.URL))
	for _, action := range []string{strings.Repeat("a", 257), strings.Repeat("💫", 65)} {
		input := "https://google.com/recaptcha/api2/anchor?k=x&co=y&sa=" + url.QueryEscape(action)
		_, err := client.Solve(context.Background(), input)
		requireCode(t, err, InvalidInput)
	}
	if requests.Load() != 0 {
		t.Fatal("invalid selected URL action reached network")
	}
	input := "https://google.com/recaptcha/api2/anchor?k=x&co=y&sa=" + strings.Repeat("a", 257)
	_, err := client.Solve(context.Background(), input, WithAction("valid"))
	requireCode(t, err, ProtocolError)
	if requests.Load() != 1 {
		t.Fatal("valid explicit action failed to replace URL action")
	}
}

func TestClientRejectsOperationOverridesAndOneShotAcceptsThem(t *testing.T) {
	for _, option := range []Option{WithAction("action"), WithBG("bg")} {
		client, err := NewClient(option)
		if client != nil {
			client.Close()
		}
		requireCode(t, err, InvalidInput)
		_, err = LoadBG(context.Background(), option)
		requireCode(t, err, InvalidInput)
	}
	origin := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch {
		case strings.HasSuffix(r.URL.Path, ".js"):
			fmt.Fprint(w, "releases/current/script.js")
		case strings.HasSuffix(r.URL.Path, "/anchor"):
			fmt.Fprint(w, `<input id=recaptcha-token value=challenge>`)
		case strings.HasSuffix(r.URL.Path, "/reload"):
			if err := r.ParseForm(); err != nil {
				t.Error(err)
			}
			if r.PostForm.Get("sa") != "action" || r.PostForm.Get("bg") != "bg" {
				t.Error("one-shot overrides were lost")
			}
			fmt.Fprint(w, `["rresp","fixture"]`)
		}
	}))
	defer origin.Close()
	privateOrigin := Option{apply: func(s *settings) error { s.testOrigin = origin.URL; return nil }}
	if _, err := Solve(context.Background(), goodAnchor, WithOffline(true), privateOrigin, WithAction("action"), WithBG("bg")); err != nil {
		t.Fatal(err)
	}
}

func TestHTML5AnchorContextsAndEntities(t *testing.T) {
	for _, body := range []string{
		`<!-- before > <input id=recaptcha-token value=fake> -->`,
		`<script>const x='<input id=recaptcha-token value=fake>';</script>`,
		`<style><input id=recaptcha-token value=fake></style>`,
		`<textarea><input id=recaptcha-token value=fake></textarea>`,
	} {
		_, err := parseAnchorToken([]byte(body))
		requireCode(t, err, ProtocolError)
		token, err := parseAnchorToken([]byte(body + `<input id=recaptcha-token value=actual>`))
		if err != nil || token != "actual" {
			t.Fatal("non-element token overrode actual input", err)
		}
	}
	for input, expected := range map[string]string{"a&copytail": "a&copytail", "a&copy=tail": "a&copy=tail", "a&copy b": "a© b", "a&#x80;b": "a€b", "a&#0;b": "a�b", "a&notit;b": "a&notit;b"} {
		token, err := parseAnchorToken([]byte(`<input value='` + input + `' id=recaptcha-token>`))
		if err != nil || token != expected {
			t.Fatalf("attribute %q: got %q, %v", input, token, err)
		}
	}
}

func TestOperationCookiesRequiredAndIsolated(t *testing.T) {
	var sequence atomic.Int64
	var reloaded atomic.Int64
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch {
		case strings.HasSuffix(r.URL.Path, ".js"):
			if r.Header.Get("Cookie") != "" {
				t.Error("cookie crossed operations")
			}
			http.SetCookie(w, &http.Cookie{Name: "operation", Value: fmt.Sprint(sequence.Add(1)), Path: "/recaptcha/"})
			fmt.Fprint(w, "releases/current/script.js")
		case strings.HasSuffix(r.URL.Path, "/anchor"):
			cookie, err := r.Cookie("operation")
			if err != nil {
				http.Error(w, "missing operation", http.StatusForbidden)
				return
			}
			fmt.Fprintf(w, `<input id=recaptcha-token value=%s>`, cookie.Value)
		case strings.HasSuffix(r.URL.Path, "/reload"):
			cookie, err := r.Cookie("operation")
			if err != nil || r.ParseForm() != nil || cookie.Value != r.PostForm.Get("c") {
				http.Error(w, "wrong operation", http.StatusForbidden)
				return
			}
			reloaded.Add(1)
			fmt.Fprint(w, `["rresp","fixture"]`)
		}
	}))
	defer server.Close()
	client := testClient(t, server.URL, WithOffline(true))
	var group sync.WaitGroup
	for i := 0; i < 4; i++ {
		group.Add(1)
		go func() {
			defer group.Done()
			if _, err := client.Solve(context.Background(), goodAnchor); err != nil {
				t.Error(err)
			}
		}()
	}
	group.Wait()
	if reloaded.Load() != 4 {
		t.Fatal("cookies were not delivered to every reload")
	}
}

func TestCookieScopeAndLimits(t *testing.T) {
	client, err := operationHTTPClient(&http.Client{Transport: http.DefaultTransport})
	if err != nil {
		t.Fatal(err)
	}
	origin, _ := url.Parse("https://www.google.com/recaptcha/api2/anchor")
	client.Jar.SetCookies(origin, []*http.Cookie{
		{Name: "host", Value: "value", Path: "/recaptcha/"},
		{Name: "parent", Value: "value", Domain: ".google.com", Path: "/"},
		{Name: "wrong", Value: "value", Domain: "example.com", Path: "/"},
		{Name: "suffix", Value: "value", Domain: "com", Path: "/"},
		{Name: "secure", Value: "value", Secure: true, Path: "/"},
		{Name: "expired", Value: "value", Expires: time.Unix(1, 0), Path: "/"},
	})
	for _, test := range []struct{ address, want string }{
		{"https://www.google.com/recaptcha/api2/reload", "host,parent,secure"},
		{"https://google.com/recaptcha/api2/reload", "parent"},
		{"http://www.google.com/recaptcha/api2/reload", "host,parent"},
		{"https://www.google.com/recaptchax/reload", "parent,secure"},
		{"https://example.com/recaptcha/api2/reload", ""},
	} {
		u, _ := url.Parse(test.address)
		var names []string
		for _, cookie := range client.Jar.Cookies(u) {
			names = append(names, cookie.Name)
		}
		if strings.Join(names, ",") != test.want {
			t.Fatalf("cookie scope %s: %v", test.address, names)
		}
	}
	for _, countLimit := range []bool{false, true} {
		server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			if countLimit {
				for i := 0; i < maxOperationCookies; i++ {
					w.Header().Add("Set-Cookie", fmt.Sprintf("cookie%d=value", i))
				}
			} else {
				w.Header().Add("Set-Cookie", "cookie="+strings.Repeat("x", maxCookieBytes))
			}
			fmt.Fprint(w, "releases/current/script.js")
		}))
		client := testClient(t, server.URL, WithOffline(true))
		_, err := client.Solve(context.Background(), goodAnchor)
		requireCode(t, err, ResourceLimit)
		server.Close()
	}
}

func TestOutgoingCookieHeaderBudgetBeforeRequest(t *testing.T) {
	var calls atomic.Int64
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		calls.Add(1)
		for _, name := range []string{"a", "b"} {
			w.Header().Add("Set-Cookie", name+"="+strings.Repeat("x", maxCookieBytes/2-len(name)-1))
		}
		fmt.Fprint(w, "releases/current/script.js")
	}))
	defer server.Close()
	client := testClient(t, server.URL, WithOffline(true))
	_, err := client.Solve(context.Background(), goodAnchor)
	requireCode(t, err, ResourceLimit)
	if calls.Load() != 1 {
		t.Fatal("oversized outgoing cookie header reached network")
	}
}
