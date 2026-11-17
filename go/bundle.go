package anchorpass

import (
	"bytes"
	"crypto/ed25519"
	"crypto/sha256"
	_ "embed"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"io"
	"strconv"
	"time"
	"unicode/utf8"
)

//go:embed data/bundle.json
var bundledEnvelope []byte

//go:embed data/keys.json
var bundledKeyData []byte

const maxSafeTimestamp = 9007199254740991

type bundle struct {
	SchemaVersion  int               `json:"schema_version"`
	ProtocolFamily string            `json:"protocol_family"`
	Revision       string            `json:"revision"`
	IssuedAt       int64             `json:"issued_at"`
	ExpiresAt      int64             `json:"expires_at"`
	Fields         map[string]string `json:"fields"`
	envelope       []byte
	digest         string
}

type envelope struct {
	KeyID     string `json:"key_id"`
	Payload   string `json:"payload"`
	Signature string `json:"signature"`
}

// JSON permits escaped UTF-16 pairs, but encoding/json substitutes U+FFFD for
// unpaired surrogates. Validate escapes first so valid literal U+FFFD stays data.
func validJSONUnicode(raw []byte) bool {
	inString := false
	for i := 0; i < len(raw); i++ {
		if !inString {
			if raw[i] == '"' {
				inString = true
			}
			continue
		}
		if raw[i] == '"' {
			inString = false
			continue
		}
		if raw[i] != '\\' {
			continue
		}
		i++
		if i >= len(raw) {
			return false
		}
		if raw[i] != 'u' {
			continue
		}
		if i+5 > len(raw) {
			return false
		}
		code, e := strconv.ParseUint(string(raw[i+1:i+5]), 16, 16)
		if e != nil {
			return false
		}
		i += 4
		if code >= 0xd800 && code <= 0xdbff {
			if i+6 >= len(raw) || raw[i+1] != '\\' || raw[i+2] != 'u' {
				return false
			}
			low, e := strconv.ParseUint(string(raw[i+3:i+7]), 16, 16)
			if e != nil || low < 0xdc00 || low > 0xdfff {
				return false
			}
			i += 6
		} else if code >= 0xdc00 && code <= 0xdfff {
			return false
		}
	}
	return true
}

// checkJSON rejects duplicate object members at every level, trailing values,
// malformed UTF-8, and unbounded nesting before ordinary typed decoding.
func checkJSON(raw []byte) error {
	if !utf8.Valid(raw) || !validJSONUnicode(raw) {
		return failure(ConfigInvalid, "bundle")
	}
	d := json.NewDecoder(bytes.NewReader(raw))
	d.UseNumber()
	var walk func(int) error
	walk = func(depth int) error {
		if depth > 16 {
			return failure(ConfigInvalid, "bundle")
		}
		token, e := d.Token()
		if e != nil {
			return e
		}
		delim, ok := token.(json.Delim)
		if !ok {
			return nil
		}
		switch delim {
		case '{':
			seen := map[string]bool{}
			for d.More() {
				key, e := d.Token()
				if e != nil {
					return e
				}
				s, ok := key.(string)
				if !ok || seen[s] {
					return failure(ConfigInvalid, "bundle")
				}
				seen[s] = true
				if e = walk(depth + 1); e != nil {
					return e
				}
			}
			end, e := d.Token()
			if e != nil || end != json.Delim('}') {
				return failure(ConfigInvalid, "bundle")
			}
		case '[':
			for d.More() {
				if e := walk(depth + 1); e != nil {
					return e
				}
			}
			end, e := d.Token()
			if e != nil || end != json.Delim(']') {
				return failure(ConfigInvalid, "bundle")
			}
		default:
			return failure(ConfigInvalid, "bundle")
		}
		return nil
	}
	if e := walk(0); e != nil {
		return e
	}
	if _, e := d.Token(); e != io.EOF {
		return failure(ConfigInvalid, "bundle")
	}
	return nil
}
func strictDecode(raw []byte, v any) error {
	if e := checkJSON(raw); e != nil {
		return e
	}
	d := json.NewDecoder(bytes.NewReader(raw))
	d.DisallowUnknownFields()
	return d.Decode(v)
}
func exactKeys(raw []byte, keys ...string) bool {
	var object map[string]json.RawMessage
	if json.Unmarshal(raw, &object) != nil || len(object) != len(keys) {
		return false
	}
	for _, k := range keys {
		if _, ok := object[k]; !ok {
			return false
		}
	}
	return true
}
func decodeBase64(raw string) ([]byte, error) {
	b, e := base64.StdEncoding.Strict().DecodeString(raw)
	if e != nil || base64.StdEncoding.EncodeToString(b) != raw {
		return nil, failure(ConfigInvalid, "bundle")
	}
	return b, nil
}
func defaultKeys() (map[string]ed25519.PublicKey, error) {
	var raw map[string]string
	if e := strictDecode(bundledKeyData, &raw); e != nil {
		return nil, e
	}
	keys := map[string]ed25519.PublicKey{}
	for id, encoded := range raw {
		b, e := decodeBase64(encoded)
		if e != nil || !validKeyID(id) || len(b) != ed25519.PublicKeySize {
			return nil, failure(ConfigInvalid, "trusted-keys")
		}
		keys[id] = ed25519.PublicKey(b)
	}
	return keys, nil
}
func verifyBundle(raw []byte, keys map[string]ed25519.PublicKey, now time.Time) (*bundle, error) {
	invalid := func() (*bundle, error) { return nil, failure(ConfigInvalid, "bundle") }
	if len(raw) > maxBundleBytes || !exactKeys(raw, "key_id", "payload", "signature") {
		return invalid()
	}
	var env envelope
	if strictDecode(raw, &env) != nil || !validKeyID(env.KeyID) {
		return invalid()
	}
	key, ok := keys[env.KeyID]
	if !ok || len(key) != ed25519.PublicKeySize {
		return invalid()
	}
	payload, e := decodeBase64(env.Payload)
	if e != nil || len(payload) > maxBundleBytes {
		return invalid()
	}
	signature, e := decodeBase64(env.Signature)
	if e != nil || len(signature) != ed25519.SignatureSize {
		return invalid()
	}
	message := append([]byte("anchorpass.bundle.v1\x00"+env.KeyID+"\x00"), payload...)
	if !ed25519.Verify(key, message, signature) {
		return invalid()
	}
	if !exactKeys(payload, "schema_version", "protocol_family", "revision", "issued_at", "expires_at", "fields") {
		return invalid()
	}
	var timestamps struct {
		IssuedAt  *int64 `json:"issued_at"`
		ExpiresAt *int64 `json:"expires_at"`
	}
	if json.Unmarshal(payload, &timestamps) != nil || timestamps.IssuedAt == nil || timestamps.ExpiresAt == nil {
		return invalid()
	}
	var b bundle
	if strictDecode(payload, &b) != nil || b.SchemaVersion != 1 || b.ProtocolFamily != protocolFamily || !validRevision(b.Revision) || b.IssuedAt < 0 || b.IssuedAt > maxSafeTimestamp || b.ExpiresAt > maxSafeTimestamp || b.ExpiresAt <= b.IssuedAt || b.IssuedAt > now.Add(5*time.Minute).Unix() {
		return invalid()
	}
	if len(b.Fields) != 6 {
		return invalid()
	}
	for _, name := range []string{"reason", "hl", "size", "chr", "vh", "bg"} {
		value, ok := b.Fields[name]
		if !ok || value == "" || !utf8.ValidString(value) || len(value) > 16384 {
			return invalid()
		}
	}
	digest := sha256.Sum256(payload)
	b.digest = hex.EncodeToString(digest[:])
	b.envelope = append([]byte(nil), raw...)
	return &b, nil
}
func usableBundle(b *bundle, deadline time.Time, pin string) bool {
	return b != nil && (pin == "" || pin == b.Revision) && b.ExpiresAt > deadline.Unix()
}
func newerBundle(current, candidate *bundle) error {
	if current == nil {
		return nil
	}
	comparison := compareRevision(candidate.Revision, current.Revision)
	if comparison < 0 {
		return failure(ConfigRollback, "bundle")
	}
	if comparison == 0 && current.digest != candidate.digest {
		return failure(ConfigInvalid, "bundle")
	}
	return nil
}
