package anchorpass

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestJSONUnicodeScalars(t *testing.T) {
	for _, raw := range []string{`{"value":"�"}`, `{"value":"\ufffd"}`, `{"value":"\ud83d\ude00"}`, `{"value":"\\ud800"}`, `{"value":"\u0000\u007f"}`} {
		if e := checkJSON([]byte(raw)); e != nil {
			t.Errorf("valid Unicode JSON rejected: %s", raw)
		}
	}
	for _, raw := range []string{`{"value":"\ud800"}`, `{"value":"\udc00"}`, `{"value":"\ud800\u0041"}`, `{"value":"\ud800\\udc00"}`} {
		if checkJSON([]byte(raw)) == nil {
			t.Errorf("unpaired surrogate accepted: %s", raw)
		}
	}
}
func TestUTF8DataControlsPreserved(t *testing.T) {
	key, trust := testTrust(t)
	data := "utf8\x00\x7f+�😀"
	var object map[string]any
	if e := json.Unmarshal(testPayload(t, "2"), &object); e != nil {
		t.Fatal(e)
	}
	fields := object["fields"].(map[string]any)
	fields["bg"] = data
	fields["hl"] = data
	payload, e := json.Marshal(object)
	if e != nil {
		t.Fatal(e)
	}
	signed := signedPayload(t, key, payload)
	updates := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) { _, _ = w.Write(signed) }))
	defer updates.Close()
	origin := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch {
		case strings.HasSuffix(r.URL.Path, ".js"):
			fmt.Fprint(w, "releases/fixture-version/script.js")
		case strings.HasSuffix(r.URL.Path, "/anchor"):
			if r.URL.Query().Get("sa") != data {
				t.Error("anchor action control bytes changed")
			}
			fmt.Fprint(w, `<input id="recaptcha-token" value="fixture">`)
		case strings.HasSuffix(r.URL.Path, "/reload"):
			if e := r.ParseForm(); e != nil {
				t.Error(e)
			}
			for _, field := range []string{"bg", "hl", "sa"} {
				if r.PostForm.Get(field) != data {
					t.Errorf("reload %s data changed", field)
				}
			}
			fmt.Fprint(w, `["rresp","fixture-token"]`)
		}
	}))
	defer origin.Close()
	c := testClient(t, origin.URL, WithTrustedKeys(trust), WithBundleURL(updates.URL))
	if bg, e := c.LoadBG(context.Background()); e != nil || bg != data {
		t.Fatal("signed UTF-8 fields rejected", e)
	}
	if _, e := c.Solve(context.Background(), goodAnchor, WithBG(data), WithAction(data)); e != nil {
		t.Fatal(e)
	}
}
