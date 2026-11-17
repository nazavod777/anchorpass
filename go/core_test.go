package anchorpass

import (
	"encoding/json"
	"errors"
	"net/url"
	"os"
	"strings"
	"testing"
)

func requireCode(t *testing.T, err error, code Code) {
	t.Helper()
	var target *Error
	if !errors.As(err, &target) || target.Code != code {
		t.Fatalf("expected %s, got %v", code, err)
	}
}

const goodAnchor = "https://www.google.com/recaptcha/api2/anchor?k=site%2Bkey&co=origin%2Fvalue&v=old&sa=url-action&extra=keep%26value"

func TestAnchorInput(t *testing.T) {
	a, e := parseAnchor(goodAnchor)
	if e != nil || a.values.Get("k") != "site+key" || a.values.Get("extra") != "keep&value" {
		t.Fatalf("parse: %v", e)
	}
	for _, raw := range []string{"http://www.google.com/recaptcha/api2/anchor?k=x&co=y", "https://www.google.com.evil/recaptcha/api2/anchor?k=x&co=y", "https://user@www.google.com/recaptcha/api2/anchor?k=x&co=y", "https://www.google.com:444/recaptcha/api2/anchor?k=x&co=y", goodAnchor + "#", goodAnchor + "&k=another", goodAnchor + "&%6b=another", goodAnchor + "&x=%xx", "https://www.google.com/recaptcha/api2/anchor?k=&co=y", "https://www.google.com/recaptcha/api2/anchor?k=x&co="} {
		_, e := parseAnchor(raw)
		requireCode(t, e, InvalidInput)
	}
	for _, family := range []string{"api2", "enterprise"} {
		for _, authority := range []string{"google.com", "google.com:443", "www.google.com:443"} {
			if _, e := parseAnchor("https://" + authority + "/recaptcha/" + family + "/anchor?k=x&co=y"); e != nil {
				t.Fatal(e)
			}
		}
	}
}
func TestSharedProxyVectors(t *testing.T) {
	raw, e := os.ReadFile("testdata/proxies.json")
	if e != nil {
		t.Fatal(e)
	}
	var vectors []struct {
		Value *string `json:"value"`
		Valid bool    `json:"valid"`
	}
	if e = json.Unmarshal(raw, &vectors); e != nil {
		t.Fatal(e)
	}
	for _, v := range vectors {
		if v.Value == nil {
			continue
		}
		_, e := parseProxy(*v.Value)
		if (e == nil) != v.Valid {
			t.Errorf("proxy validity expected %v", v.Valid)
		}
	}
	p, e := parseProxy("http://u%2B:p%40%2520@127.0.0.1:8080")
	if e != nil {
		t.Fatal(e)
	}
	password, _ := p.User.Password()
	if p.User.Username() != "u+" || password != "p@%20" {
		t.Fatal("credentials decoded incorrectly")
	}
}
func TestParsersAndEncoding(t *testing.T) {
	for _, body := range []string{`<input value='a&amp;b' id="recaptcha-token">`, `<INPUT id=recaptcha-token value="a&amp;b">`} {
		token, e := parseAnchorToken([]byte(body))
		if e != nil || token != "a&b" {
			t.Fatalf("token: %q %v", token, e)
		}
	}
	for _, body := range []string{`<input id=recaptcha-token>`, `<input id=recaptcha-token value="">`, `<input id=recaptcha-token value="x" value="y">`, `<input id=recaptcha-token value="x"><input id=recaptcha-token value="y">`} {
		_, e := parseAnchorToken([]byte(body))
		requireCode(t, e, ProtocolError)
	}
	if token, e := parseReload([]byte(")]}'\n[\"rresp\",\"result\",null]")); e != nil || token != "result" {
		t.Fatal(e)
	}
	for _, raw := range []string{`["rresp",""]`, `["wrong","x"]`, `{"a":1,"a":2}`, `["rresp","x"] garbage`, `["rresp",4]`} {
		_, e := parseReload([]byte(raw))
		requireCode(t, e, ProtocolError)
	}
	a, _ := parseAnchor(goodAnchor)
	b := &bundle{Fields: map[string]string{"bg": "a+b&x =/%"}}
	form := reloadFields(a, b, "version", "anchor&token")
	decoded, e := url.ParseQuery(form.Encode())
	if e != nil || decoded.Get("bg") != "a+b&x =/%" || decoded.Get("c") != "anchor&token" || decoded.Get("sa") != "url-action" {
		t.Fatal("form encoding mismatch")
	}
	if version, e := parseVersion([]byte(`releases/v1/a releases/v2/b releases/v2/c`)); e != nil || version != "v2" {
		t.Fatal(e)
	}
}
func TestNoSecretsInFormatting(t *testing.T) {
	c, e := NewClient(WithProxy("http://user:password@127.0.0.1:1234"))
	if e != nil {
		t.Fatal(e)
	}
	defer c.Close()
	if strings.Contains(c.String(), "password") {
		t.Fatal("proxy credentials exposed")
	}
	result := SolveResult{Token: "secret-token"}
	if strings.Contains(result.String(), result.Token) {
		t.Fatal("token exposed")
	}
}

func TestRevisionContractBounds(t *testing.T) {
	for _, revision := range []string{"1", strings.Repeat("9", 20)} {
		if !validRevision(revision) {
			t.Errorf("valid revision rejected: %s", revision)
		}
	}
	for _, revision := range []string{"0", "01", "-1", "", strings.Repeat("9", 21)} {
		if validRevision(revision) {
			t.Errorf("invalid revision accepted: %s", revision)
		}
		_, e := NewClient(WithPinnedRevision(revision))
		requireCode(t, e, InvalidInput)
	}
}
