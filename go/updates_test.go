package anchorpass

import (
	"bytes"
	"context"
	"crypto/ed25519"
	"crypto/rand"
	"encoding/base64"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"
)

func signedPayload(t *testing.T, key ed25519.PrivateKey, payload []byte) []byte {
	t.Helper()
	message := append([]byte("anchorpass.bundle.v1\x00test\x00"), payload...)
	env := envelope{KeyID: "test", Payload: base64.StdEncoding.EncodeToString(payload), Signature: base64.StdEncoding.EncodeToString(ed25519.Sign(key, message))}
	raw, e := json.Marshal(env)
	if e != nil {
		t.Fatal(e)
	}
	return raw
}
func testPayload(t *testing.T, revision string) []byte {
	t.Helper()
	keys, e := defaultKeys()
	if e != nil {
		t.Fatal(e)
	}
	b, e := verifyBundle(bundledEnvelope, keys, time.Now())
	if e != nil {
		t.Fatal(e)
	}
	b.Revision = revision
	b.IssuedAt = time.Now().Add(-time.Minute).Unix()
	b.ExpiresAt = time.Now().Add(time.Hour).Unix()
	raw, e := json.Marshal(b)
	if e != nil {
		t.Fatal(e)
	}
	return raw
}
func testTrust(t *testing.T) (ed25519.PrivateKey, map[string]ed25519.PublicKey) {
	t.Helper()
	pub, key, e := ed25519.GenerateKey(rand.Reader)
	if e != nil {
		t.Fatal(e)
	}
	return key, map[string]ed25519.PublicKey{"test": pub}
}
func TestBundleSignatureAndStrictJSON(t *testing.T) {
	key, trust := testTrust(t)
	payload := testPayload(t, "2")
	raw := signedPayload(t, key, payload)
	b, e := verifyBundle(raw, trust, time.Now())
	if e != nil || b.Revision != "2" {
		t.Fatal(e)
	}
	cases := map[string][]byte{
		"signature":          bytes.Replace(raw, []byte(`"signature":"`), []byte(`"signature":"A`), 1),
		"duplicate-envelope": append([]byte(`{"key_id":"test",`), raw[1:]...),
		"duplicate-payload":  signedPayload(t, key, bytes.Replace(payload, []byte(`"revision":"2"`), []byte(`"revision":"2","revision":"3"`), 1)),
		"schema":             signedPayload(t, key, bytes.Replace(payload, []byte(`"schema_version":1`), []byte(`"schema_version":2`), 1)),
		"family":             signedPayload(t, key, bytes.Replace(payload, []byte(protocolFamily), []byte("another-family"), 1)),
		"revision":           signedPayload(t, key, bytes.Replace(payload, []byte(`"revision":"2"`), []byte(`"revision":"02"`), 1)),
		"unknown-field":      signedPayload(t, key, bytes.Replace(payload, []byte(`"fields":{`), []byte(`"fields":{"arbitrary":"value",`), 1)),
		"unknown-payload":    signedPayload(t, key, append([]byte(`{"arbitrary":1,`), payload[1:]...)),
		"invalid-unicode":    signedPayload(t, key, bytes.Replace(payload, []byte(`"hl":"en"`), []byte(`"hl":"\ud800"`), 1)),
	}
	for name, raw := range cases {
		t.Run(name, func(t *testing.T) { _, e := verifyBundle(raw, trust, time.Now()); requireCode(t, e, ConfigInvalid) })
	}
	wrong := map[string]ed25519.PublicKey{"another": trust["test"]}
	_, e = verifyBundle(raw, wrong, time.Now())
	requireCode(t, e, ConfigInvalid)
	for _, raw := range []string{`{"x":1,"x":2}`, `{"a":{"x":1,"x":2}}`, `{} {}`, `{"x":"\ud800"}`, `{"x":1,}`} {
		if checkJSON([]byte(raw)) == nil {
			t.Errorf("accepted invalid JSON: %s", raw)
		}
	}
}
func TestUpdateCacheRollbackPinAndFallback(t *testing.T) {
	key, trust := testTrust(t)
	var response atomic.Value
	response.Store(signedPayload(t, key, testPayload(t, "2")))
	var calls atomic.Int64
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls.Add(1)
		if r.Header.Get("Cookie") != "" || r.URL.RawQuery != "" {
			t.Error("captcha data leaked into updates")
		}
		_, _ = w.Write(response.Load().([]byte))
	}))
	defer server.Close()
	path := filepath.Join(t.TempDir(), "bundle.json")
	options := []Option{WithBundleURL(server.URL), WithCachePath(path), WithTrustedKeys(trust)}
	c := testClient(t, "", options...)
	info, e := c.Refresh(context.Background())
	if e != nil || info.Revision != "2" || info.CacheMode != "disk" {
		t.Fatalf("initial refresh: %+v %v", info, e)
	}
	stored, e := os.ReadFile(path)
	if e != nil || !bytes.Equal(stored, response.Load().([]byte)) {
		t.Fatal("cache did not preserve exact signed envelope")
	}
	// Reloading a persisted higher revision prevents a pinned rollback after restart.
	lower := testClient(t, "", append(options, WithPinnedRevision("1"))...)
	_, e = lower.Refresh(context.Background())
	requireCode(t, e, ConfigRollback)
	response.Store(signedPayload(t, key, testPayload(t, "1")))
	_, e = c.Refresh(context.Background())
	requireCode(t, e, ConfigRollback)
	// Different outer whitespace is fine; the digest covers exact payload bytes.
	response.Store(append([]byte(" \n"), stored...))
	if _, e = c.Refresh(context.Background()); e != nil {
		t.Fatal(e)
	}
	changed := bytes.Replace(testPayload(t, "2"), []byte(`"hl":"en"`), []byte(`"hl":"fr"`), 1)
	response.Store(signedPayload(t, key, changed))
	_, e = c.Refresh(context.Background())
	requireCode(t, e, ConfigInvalid)
	// A malformed automatic candidate can fall back to still-valid current data.
	response.Store([]byte(`{"malformed":true}`))
	ctx, cancel := context.WithTimeout(context.Background(), time.Second)
	defer cancel()
	b, e := c.selectBundle(ctx, time.Now().Add(time.Second), false)
	if e != nil || b.Revision != "2" {
		t.Fatal("valid fallback lost", e)
	}
	response.Store(signedPayload(t, key, testPayload(t, "3")))
	info, e = c.Refresh(context.Background())
	if e != nil || info.Revision != "3" {
		t.Fatal(e)
	}
	pinned := testClient(t, "", append(options, WithPinnedRevision("3"))...)
	before := calls.Load()
	if _, e = pinned.selectBundle(ctx, time.Now().Add(time.Second), false); e != nil {
		t.Fatal(e)
	}
	if calls.Load() != before {
		t.Fatal("exact valid pin unexpectedly refreshed")
	}
	if _, e = pinned.Refresh(context.Background()); e != nil {
		t.Fatal(e)
	}
	if calls.Load() != before {
		t.Fatal("explicit refresh changed valid pin policy")
	}
	if e = os.WriteFile(path, []byte(`{"broken":true}`), 0600); e != nil {
		t.Fatal(e)
	}
	_, e = pinned.Refresh(context.Background())
	requireCode(t, e, ConfigInvalid)
}
func TestExpiryOfflineAndUpdateDeadline(t *testing.T) {
	key, trust := testTrust(t)
	payload := testPayload(t, "2")
	var object map[string]any
	if e := json.Unmarshal(payload, &object); e != nil {
		t.Fatal(e)
	}
	object["issued_at"] = time.Now().Add(-2 * time.Hour).Unix()
	object["expires_at"] = time.Now().Add(-time.Hour).Unix()
	expired, e := json.Marshal(object)
	if e != nil {
		t.Fatal(e)
	}
	path := filepath.Join(t.TempDir(), "bundle.json")
	if e = os.WriteFile(path, signedPayload(t, key, expired), 0600); e != nil {
		t.Fatal(e)
	}
	offline := testClient(t, "", WithCachePath(path), WithOffline(true), WithTrustedKeys(trust))
	_, e = offline.Refresh(context.Background())
	requireCode(t, e, ConfigExpired)
	var pinnedRequests atomic.Int64
	pinnedServer := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		pinnedRequests.Add(1)
		_, _ = w.Write(signedPayload(t, key, testPayload(t, "2")))
	}))
	defer pinnedServer.Close()
	expiredPin := testClient(t, "", WithCachePath(path), WithPinnedRevision("2"), WithTrustedKeys(trust), WithBundleURL(pinnedServer.URL))
	_, e = expiredPin.Refresh(context.Background())
	requireCode(t, e, ConfigExpired)
	if pinnedRequests.Load() != 0 {
		t.Fatal("expired immutable pin unexpectedly requested replacement data")
	}
	empty := testClient(t, "", WithMemoryCache(), WithOffline(true), WithTrustedKeys(trust))
	_, e = empty.Refresh(context.Background())
	requireCode(t, e, ConfigUnavailable)
	slow := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { <-r.Context().Done() }))
	defer slow.Close()
	c := testClient(t, "", WithBundleURL(slow.URL), WithTrustedKeys(trust), WithTimeout(80*time.Millisecond))
	_, e = c.Solve(context.Background(), goodAnchor)
	requireCode(t, e, DeadlineExceeded)
	object["issued_at"] = time.Now().Add(time.Hour).Unix()
	object["expires_at"] = time.Now().Add(2 * time.Hour).Unix()
	future, e := json.Marshal(object)
	if e != nil {
		t.Fatal(e)
	}
	_, e = verifyBundle(signedPayload(t, key, future), trust, time.Now())
	requireCode(t, e, ConfigInvalid)
}
func TestConcurrentCacheAndSingleRefresh(t *testing.T) {
	key, trust := testTrust(t)
	raw := signedPayload(t, key, testPayload(t, "2"))
	var calls atomic.Int64
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) { calls.Add(1); _, _ = w.Write(raw) }))
	defer server.Close()
	path := filepath.Join(t.TempDir(), "bundle.json")
	c := testClient(t, "", WithCachePath(path), WithBundleURL(server.URL), WithTrustedKeys(trust))
	var group sync.WaitGroup
	for i := 0; i < 8; i++ {
		group.Add(1)
		go func() {
			defer group.Done()
			ctx, cancel := context.WithTimeout(context.Background(), time.Second)
			defer cancel()
			b, e := c.selectBundle(ctx, time.Now().Add(time.Second), false)
			if e != nil || b.Revision != "2" {
				t.Error(e)
			}
		}()
	}
	group.Wait()
	if calls.Load() != 1 {
		t.Fatalf("concurrent refresh count: %d", calls.Load())
	}
	// Distinct clients coordinate their atomic cache writes under the same kernel lock.
	clients := []*Client{testClient(t, "", WithCachePath(path), WithBundleURL(server.URL), WithTrustedKeys(trust)), testClient(t, "", WithCachePath(path), WithBundleURL(server.URL), WithTrustedKeys(trust))}
	for _, client := range clients {
		group.Add(1)
		go func(c *Client) {
			defer group.Done()
			if _, e := c.Refresh(context.Background()); e != nil {
				t.Error(e)
			}
		}(client)
	}
	group.Wait()
	stored, e := os.ReadFile(path)
	if e != nil {
		t.Fatal(e)
	}
	if _, e = verifyBundle(stored, trust, time.Now()); e != nil {
		t.Fatal(e)
	}
}
func TestCacheSubprocess(t *testing.T) {
	if path := os.Getenv("ANCHORPASS_TEST_LOCK"); path != "" {
		ctx, cancel := context.WithTimeout(context.Background(), 80*time.Millisecond)
		defer cancel()
		unlock, e := lockCache(ctx, path)
		if e == nil {
			unlock()
			t.Fatal("cross-process lock bypassed")
		}
		requireCode(t, e, DeadlineExceeded)
		return
	}
	path := filepath.Join(t.TempDir(), "lock")
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	unlock, e := lockCache(ctx, path)
	if e != nil {
		t.Fatal(e)
	}
	defer unlock()
	binary, e := os.Executable()
	if e != nil {
		t.Fatal(e)
	}
	command := exec.CommandContext(ctx, binary, "-test.run=^TestCacheSubprocess$")
	command.Env = append(os.Environ(), "ANCHORPASS_TEST_LOCK="+path)
	output, e := command.CombinedOutput()
	if e != nil {
		t.Fatalf("subprocess: %s %v", output, e)
	}
	if !strings.Contains(string(output), "PASS") {
		t.Fatalf("no successful child result: %s", output)
	}
}
func TestUpdatesProxyPolicies(t *testing.T) {
	key, trust := testTrust(t)
	raw := signedPayload(t, key, testPayload(t, "2"))
	var requests atomic.Int64
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) { requests.Add(1); _, _ = w.Write(raw) }))
	defer server.Close()
	proxy := newProxyFixture(t, "", "", nil)
	for _, mode := range []string{"inherit", "direct", "override"} {
		t.Run(mode, func(t *testing.T) {
			options := []Option{WithBundleURL(server.URL), WithTrustedKeys(trust)}
			before := proxy.calls.Load()
			switch mode {
			case "inherit":
				options = append(options, WithProxy(proxy.server.URL))
			case "direct":
				options = append(options, WithProxy("http://127.0.0.1:1"), WithUpdatesProxy(UpdatesDirect))
			case "override":
				options = append(options, WithProxy("http://127.0.0.1:1"), WithUpdatesProxy(UpdatesVia(proxy.server.URL)))
			}
			c := testClient(t, "", options...)
			if _, e := c.Refresh(context.Background()); e != nil {
				t.Fatal(e)
			}
			used := proxy.calls.Load() > before
			if used != (mode != "direct") {
				t.Fatalf("wrong update route for %s", mode)
			}
		})
	}
	c := testClient(t, "", WithProxy("http://127.0.0.1:1"), WithBundleURL(server.URL), WithTrustedKeys(trust))
	before := requests.Load()
	_, e := c.Refresh(context.Background())
	requireCode(t, e, ProxyConnectError)
	if requests.Load() != before {
		t.Fatal("update proxy failure used direct route")
	}
}

func TestSignedBundleSchemaBounds(t *testing.T) {
	key, trust := testTrust(t)
	for _, test := range []struct {
		revision string
		valid    bool
	}{{"1", true}, {strings.Repeat("9", 20), true}, {"0", false}, {strings.Repeat("9", 21), false}} {
		raw := signedPayload(t, key, testPayload(t, test.revision))
		_, e := verifyBundle(raw, trust, time.Now())
		if test.valid {
			if e != nil {
				t.Fatal(e)
			}
		} else {
			requireCode(t, e, ConfigInvalid)
		}
	}
	var object map[string]any
	if e := json.Unmarshal(testPayload(t, "2"), &object); e != nil {
		t.Fatal(e)
	}
	fields := object["fields"].(map[string]any)
	fields["hl"] = strings.Repeat("h", 16384)
	fields["bg"] = strings.Repeat("b", 16384)
	object["issued_at"] = int64(0)
	object["expires_at"] = int64(maxSafeTimestamp)
	raw, e := json.Marshal(object)
	if e != nil {
		t.Fatal(e)
	}
	if _, e = verifyBundle(signedPayload(t, key, raw), trust, time.Now()); e != nil {
		t.Fatal("valid schema boundaries rejected", e)
	}
	for _, field := range []string{"hl", "bg"} {
		saved := fields[field]
		fields[field] = strings.Repeat("x", 16385)
		raw, e = json.Marshal(object)
		if e != nil {
			t.Fatal(e)
		}
		_, e = verifyBundle(signedPayload(t, key, raw), trust, time.Now())
		requireCode(t, e, ConfigInvalid)
		fields[field] = saved
	}
	object["expires_at"] = int64(maxSafeTimestamp) + 1
	raw, e = json.Marshal(object)
	if e != nil {
		t.Fatal(e)
	}
	_, e = verifyBundle(signedPayload(t, key, raw), trust, time.Now())
	requireCode(t, e, ConfigInvalid)
}
