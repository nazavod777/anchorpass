package anchorpass

import (
	"crypto/ed25519"
	"crypto/x509"
	"net/url"
	"strings"
	"time"
	"unicode/utf8"
)

// UpdatesProxyMode separates inheritance, direct access, and an explicit route.
type UpdatesProxyMode struct {
	mode string
	url  string
}

var (
	UpdatesInherit = UpdatesProxyMode{mode: "inherit"}
	UpdatesDirect  = UpdatesProxyMode{mode: "direct"}
)

func UpdatesVia(proxyURL string) UpdatesProxyMode {
	return UpdatesProxyMode{mode: "proxy", url: proxyURL}
}

type settings struct {
	proxy           *url.URL
	updatesMode     UpdatesProxyMode
	timeout         time.Duration
	bundleURL       string
	cachePath       string
	memory          bool
	offline         bool
	pin             string
	trusted         map[string]ed25519.PublicKey
	roots           *x509.CertPool
	proxyRoots      *x509.CertPool
	maxInFlight     int
	refreshInterval time.Duration
	hostname        string
	hostnameSet     bool
	userAgent       string
	action          string
	actionSet       bool
	bg              string
	bgSet           bool
	// These hooks are package-private: callers cannot bypass production origin checks.
	testOrigin string
}

// Option configures a client or the action for a single solve.
type Option struct {
	apply     func(*settings) error
	solveOnly bool
}

func WithProxy(raw string) Option {
	return Option{apply: func(s *settings) error { p, e := parseProxy(raw); s.proxy = p; return e }}
}
func WithTimeout(d time.Duration) Option {
	return Option{apply: func(s *settings) error {
		if d <= 0 {
			return failure(InvalidInput, "options")
		}
		s.timeout = d
		return nil
	}}
}
func WithBundleURL(raw string) Option {
	return Option{apply: func(s *settings) error {
		if e := validateBundleURL(raw); e != nil {
			return e
		}
		s.bundleURL = raw
		return nil
	}}
}
func WithCachePath(path string) Option {
	return Option{apply: func(s *settings) error {
		if path == "" {
			return failure(InvalidInput, "cache")
		}
		s.cachePath = path
		s.memory = false
		return nil
	}}
}

// WithMemoryCache explicitly disables persistence and its cross-restart rollback floor.
func WithMemoryCache() Option {
	return Option{apply: func(s *settings) error { s.memory = true; s.cachePath = ""; return nil }}
}
func WithUpdatesProxy(mode UpdatesProxyMode) Option {
	return Option{apply: func(s *settings) error {
		switch mode.mode {
		case "inherit", "direct":
		case "proxy":
			if _, e := parseProxy(mode.url); e != nil {
				return e
			}
		default:
			return failure(InvalidInput, "updates-proxy")
		}
		s.updatesMode = mode
		return nil
	}}
}
func WithPinnedRevision(revision string) Option {
	return Option{apply: func(s *settings) error {
		if !validRevision(revision) {
			return failure(InvalidInput, "pin")
		}
		s.pin = revision
		return nil
	}}
}
func WithOffline(offline bool) Option {
	return Option{apply: func(s *settings) error { s.offline = offline; return nil }}
}

// WithTrustedKeys replaces the bundled trust set. The map and key bytes are copied.
func WithTrustedKeys(keys map[string]ed25519.PublicKey) Option {
	return Option{apply: func(s *settings) error {
		if len(keys) == 0 || len(keys) > 32 {
			return failure(InvalidInput, "trusted-keys")
		}
		s.trusted = make(map[string]ed25519.PublicKey, len(keys))
		for id, key := range keys {
			if !validKeyID(id) || len(key) != ed25519.PublicKeySize {
				return failure(InvalidInput, "trusted-keys")
			}
			s.trusted[id] = append(ed25519.PublicKey(nil), key...)
		}
		return nil
	}}
}

// WithRootCAs supplies cloned target TLS roots for captcha and update requests.
// HTTPS proxy TLS uses independent roots configured with WithProxyRootCAs.
func WithRootCAs(roots *x509.CertPool) Option {
	return Option{apply: func(s *settings) error {
		if roots == nil {
			return failure(InvalidInput, "root-cas")
		}
		s.roots = roots.Clone()
		return nil
	}}
}

// WithProxyRootCAs supplies cloned HTTPS proxy TLS roots for captcha and update
// routes. Target TLS uses WithRootCAs; each policy defaults to system roots.
func WithProxyRootCAs(roots *x509.CertPool) Option {
	return Option{apply: func(s *settings) error {
		if roots == nil {
			return failure(InvalidInput, "proxy-root-cas")
		}
		s.proxyRoots = roots.Clone()
		return nil
	}}
}
func WithMaxInFlight(n int) Option {
	return Option{apply: func(s *settings) error {
		if n < 1 || n > 1024 {
			return failure(InvalidInput, "concurrency")
		}
		s.maxInFlight = n
		return nil
	}}
}
func WithRefreshInterval(d time.Duration) Option {
	return Option{apply: func(s *settings) error {
		if d < 0 {
			return failure(InvalidInput, "refresh-interval")
		}
		s.refreshInterval = d
		return nil
	}}
}
func WithAction(action string) Option {
	return Option{solveOnly: true, apply: func(s *settings) error {
		if action == "" || !utf8.ValidString(action) || len(action) > 256 {
			return failure(InvalidInput, "action")
		}
		s.action = action
		s.actionSet = true
		return nil
	}}
}

// WithBG overrides bg for an operation while retaining other signed fields.
func WithBG(bg string) Option {
	return Option{solveOnly: true, apply: func(s *settings) error {
		if bg == "" || !utf8.ValidString(bg) || len(bg) > 16384 {
			return failure(InvalidInput, "bg")
		}
		s.bg = bg
		s.bgSet = true
		return nil
	}}
}

// WithUserAgent overrides User-Agent for all three requests of one solve.
func WithUserAgent(userAgent string) Option {
	return Option{solveOnly: true, apply: func(s *settings) error {
		if len(userAgent) == 0 || len(userAgent) > 1024 || strings.TrimSpace(userAgent) == "" {
			return failure(InvalidInput, "overrides")
		}
		for i := range userAgent {
			if userAgent[i] < 0x20 || userAgent[i] > 0x7e {
				return failure(InvalidInput, "overrides")
			}
		}
		s.userAgent = userAgent
		return nil
	}}
}
