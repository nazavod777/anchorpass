package anchorpass

import (
	"bytes"
	"encoding/json"
	"io"
	"net/url"
	"regexp"
	"strings"
	"unicode/utf8"

	"golang.org/x/net/html"
)

const protocolFamily = "recaptcha-v3/1"
const maxResponseBytes = 1048576
const maxBundleBytes = 65536

var revisionPattern = regexp.MustCompile(`^[1-9][0-9]{0,19}$`)
var keyPattern = regexp.MustCompile(`^[A-Za-z0-9_-]{1,64}$`)
var versionPattern = regexp.MustCompile(`releases/([A-Za-z0-9_-]{1,256})/`)

func validRevision(s string) bool { return revisionPattern.MatchString(s) }
func validKeyID(s string) bool    { return keyPattern.MatchString(s) }
func compareRevision(a, b string) int {
	if len(a) < len(b) {
		return -1
	}
	if len(a) > len(b) {
		return 1
	}
	return strings.Compare(a, b)
}
func validText(s string, limit int) bool {
	if len(s) > limit || !utf8.ValidString(s) {
		return false
	}
	for _, r := range s {
		if r < 32 || r == 127 {
			return false
		}
	}
	return true
}
func badEscape(s string) bool { _, e := url.QueryUnescape(s); return e != nil }

type anchor struct {
	url    *url.URL
	family string
	values url.Values
}

func parseAnchor(raw string) (*anchor, error) {
	if len(raw) > 16384 || strings.ContainsAny(raw, "\r\n\t ") || strings.Contains(raw, "#") || badEscape(raw) {
		return nil, failure(InvalidInput, "anchor-url")
	}
	u, e := url.Parse(raw)
	if e != nil || u == nil || u.Opaque != "" || u.Scheme != "https" || u.User != nil || (u.Host != u.Hostname() && u.Host != u.Hostname()+":443") || (u.Hostname() != "google.com" && u.Hostname() != "www.google.com") {
		return nil, failure(InvalidInput, "anchor-url")
	}
	family := ""
	switch u.EscapedPath() {
	case "/recaptcha/api2/anchor":
		family = "api2"
	case "/recaptcha/enterprise/anchor":
		family = "enterprise"
	default:
		return nil, failure(InvalidInput, "anchor-url")
	}
	q, e := url.ParseQuery(u.RawQuery)
	if e != nil {
		return nil, failure(InvalidInput, "anchor-url")
	}
	for k, v := range q {
		if len(v) != 1 || !validText(k, 256) || !validText(v[0], 8192) {
			return nil, failure(InvalidInput, "anchor-url")
		}
	}
	if q.Get("k") == "" || q.Get("co") == "" {
		return nil, failure(InvalidInput, "anchor-url")
	}
	return &anchor{url: u, family: family, values: q}, nil
}

func parseVersion(body []byte) (string, error) {
	counts := map[string]int{}
	best := ""
	for _, m := range versionPattern.FindAllSubmatch(body, -1) {
		v := string(m[1])
		counts[v]++
		if best == "" || counts[v] > counts[best] {
			best = v
		}
	}
	if best == "" {
		return "", failure(ProtocolError, "version")
	}
	return best, nil
}
func parseAnchorToken(body []byte) (string, error) {
	if !utf8.Valid(body) {
		return "", failure(ProtocolError, "anchor")
	}
	token := ""
	parser := html.NewTokenizer(bytes.NewReader(body))
	parser.SetMaxBuf(maxResponseBytes)
	for {
		kind := parser.Next()
		if kind == html.ErrorToken {
			if parser.Err() != io.EOF {
				return "", failure(ProtocolError, "anchor")
			}
			break
		}
		if kind != html.StartTagToken && kind != html.SelfClosingTagToken {
			continue
		}
		tag := parser.Token()
		if tag.Data != "input" {
			continue
		}
		attrs := map[string]string{}
		for _, attribute := range tag.Attr {
			if _, exists := attrs[attribute.Key]; exists {
				return "", failure(ProtocolError, "anchor")
			}
			attrs[attribute.Key] = attribute.Val
		}
		if attrs["id"] == "recaptcha-token" {
			if token != "" || attrs["value"] == "" || !validText(attrs["value"], 16384) {
				return "", failure(ProtocolError, "anchor")
			}
			token = attrs["value"]
		}
	}
	if token == "" {
		return "", failure(ProtocolError, "anchor")
	}
	return token, nil
}
func parseReload(body []byte) (string, error) {
	body = bytes.TrimSpace(bytes.TrimPrefix(body, []byte(")]}'")))
	if e := checkJSON(body); e != nil {
		return "", failure(ProtocolError, "reload")
	}
	var values []json.RawMessage
	if json.Unmarshal(body, &values) != nil || len(values) < 2 {
		return "", failure(ProtocolError, "reload")
	}
	var kind, token string
	if json.Unmarshal(values[0], &kind) != nil || kind != "rresp" || json.Unmarshal(values[1], &token) != nil || token == "" || !validText(token, 65536) {
		return "", failure(ProtocolError, "reload")
	}
	return token, nil
}
func reloadFields(a *anchor, b *bundle, version, token string) url.Values {
	fields := url.Values{}
	for k, v := range b.Fields {
		fields.Set(k, v)
	}
	fields.Set("v", version)
	fields.Set("c", token)
	fields.Set("k", a.values.Get("k"))
	fields.Set("co", a.values.Get("co"))
	if action, ok := a.values["sa"]; ok {
		fields.Set("sa", action[0])
	}
	return fields
}

// ExtractAction validates an anchor URL and returns its sa query value, or an
// empty string when absent. It performs no network or filesystem operations.
func ExtractAction(anchorURL string) (string, error) {
	a, e := parseAnchor(anchorURL)
	if e != nil {
		return "", e
	}
	return a.values.Get("sa"), nil
}
