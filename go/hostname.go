package anchorpass

import (
	"encoding/base64"
	"fmt"
	"net"
	"net/netip"
	"net/url"
	"strconv"
	"strings"
	"unicode/utf8"
)

func normalizeHostname(value string) (string, error) {
	invalid := func() (string, error) { return "", failure(InvalidInput, "overrides") }
	if value == "" || len(value) > 253 {
		return invalid()
	}
	for i := range value {
		if value[i] < 0x21 || value[i] > 0x7e {
			return invalid()
		}
	}
	ipValue := value
	if strings.HasPrefix(value, "[") && strings.HasSuffix(value, "]") {
		ipValue = value[1 : len(value)-1]
	}
	if ip, err := netip.ParseAddr(ipValue); err == nil {
		if ip.Zone() != "" || strings.HasPrefix(value, "[") && !ip.Is6() {
			return invalid()
		}
		if ip.Is4In6() {
			segments := ip.As16()
			return fmt.Sprintf("::ffff:%x:%x", uint16(segments[12])<<8|uint16(segments[13]), uint16(segments[14])<<8|uint16(segments[15])), nil
		}
		return ip.String(), nil
	}
	if strings.ContainsAny(value, "[]:/?#@%\\") {
		return invalid()
	}
	for _, label := range strings.Split(strings.TrimSuffix(value, "."), ".") {
		if len(label) == 0 || len(label) > 63 || label[0] == '-' || label[len(label)-1] == '-' {
			return invalid()
		}
		for i := range label {
			c := label[i]
			if !(c >= 'A' && c <= 'Z' || c >= 'a' && c <= 'z' || c >= '0' && c <= '9' || c == '-') {
				return invalid()
			}
		}
	}
	return strings.ToLower(value), nil
}

func hostnameOrigin(encoded, hostname string) (string, error) {
	invalid := func() (string, error) { return "", failure(InvalidInput, "overrides") }
	normalized, err := normalizeHostname(hostname)
	if err != nil {
		return "", err
	}
	decoded, err := decodeOriginBase64(encoded)
	if err != nil || !utf8.Valid(decoded) {
		return invalid()
	}
	originText := string(decoded)
	if strings.ContainsAny(originText, "?#\\") {
		return invalid()
	}
	origin, err := url.Parse(originText)
	if err != nil || origin == nil || origin.Opaque != "" || origin.User != nil || origin.Hostname() == "" || (origin.Scheme != "http" && origin.Scheme != "https") || (origin.EscapedPath() != "" && origin.EscapedPath() != "/") {
		return invalid()
	}
	originHostname := origin.Hostname()
	if strings.HasPrefix(origin.Host, "[") {
		originHostname = "[" + originHostname + "]"
	}
	if _, err = normalizeHostname(originHostname); err != nil {
		return invalid()
	}
	port := origin.Port()
	if port == "" {
		if strings.HasSuffix(origin.Host, ":") {
			return invalid()
		}
		if origin.Scheme == "https" {
			port = "443"
		} else {
			port = "80"
		}
	}
	number, err := strconv.Atoi(port)
	if err != nil || number < 1 || number > 65535 {
		return invalid()
	}
	serialized := origin.Scheme + "://" + net.JoinHostPort(normalized, strconv.Itoa(number))
	return strings.ReplaceAll(base64.StdEncoding.EncodeToString([]byte(serialized)), "=", "."), nil
}

// WithHostname replaces only the encoded origin hostname for one solve.
func WithHostname(hostname string) Option {
	return Option{solveOnly: true, apply: func(s *settings) error {
		if _, err := normalizeHostname(hostname); err != nil {
			return err
		}
		s.hostname = hostname
		s.hostnameSet = true
		return nil
	}}
}

// decodeOriginBase64 accepts canonical standard/URL-safe data and optional Google dot padding.
func decodeOriginBase64(encoded string) ([]byte, error) {
	invalid := func() ([]byte, error) { return nil, failure(InvalidInput, "overrides") }
	raw := strings.TrimRight(encoded, "=.")
	padding := encoded[len(raw):]
	if len(padding) > 0 {
		if len(padding) > 2 || len(encoded)%4 != 0 || padding != strings.Repeat(string(padding[0]), len(padding)) {
			return invalid()
		}
	}
	for _, encoding := range []*base64.Encoding{base64.RawStdEncoding.Strict(), base64.RawURLEncoding.Strict()} {
		decoded, err := encoding.DecodeString(raw)
		if err == nil && encoding.EncodeToString(decoded) == raw {
			return decoded, nil
		}
	}
	return invalid()
}
