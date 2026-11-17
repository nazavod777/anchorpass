package anchorpass

import (
	"context"
	"fmt"
	"net/http"
	"net/url"
	"strings"
	"sync"
	"time"
)

// SolveResult reports a received token, not a server acceptance or score.
type SolveResult struct {
	Token           string        `json:"token"`
	BundleRevision  string        `json:"bundle_revision"`
	ProtocolVersion string        `json:"protocol_version"`
	Duration        time.Duration `json:"duration"`
}

func (r SolveResult) String() string {
	return "anchorpass.SolveResult{token:[redacted], bundle_revision:" + r.BundleRevision + ", protocol_version:" + r.ProtocolVersion + "}"
}
func (r SolveResult) GoString() string { return r.String() }

// BundleInfo contains safe metadata about the currently selected signed data.
type BundleInfo struct {
	Revision  string
	ExpiresAt time.Time
	CacheMode string
}

// Client owns bounded reusable pools. It supports concurrent Solve calls.
// Close cancels active operations and closes idle connections; it is idempotent.
type Client struct {
	settings        settings
	http            *http.Client
	updates         *http.Client
	transport       *http.Transport
	updateTransport *http.Transport
	updateProxy     *url.URL
	lifetime        context.Context
	cancel          context.CancelFunc
	closeOnce       sync.Once
	permits         chan struct{}
	bundleState     *sharedBundles
	bundleStateKey  string
}

func (c *Client) String() string   { return "anchorpass.Client" }
func (c *Client) GoString() string { return c.String() }

// NewClient performs no network requests or filesystem writes. By default it uses
// memory cache; use WithCachePath for a persistent cross-process rollback floor.
func NewClient(options ...Option) (*Client, error) {
	keys, e := defaultKeys()
	if e != nil {
		return nil, e
	}
	s := settings{timeout: 30 * time.Second, memory: true, updatesMode: UpdatesInherit, maxInFlight: 16, refreshInterval: time.Hour, trusted: keys}
	for _, option := range options {
		if option.apply == nil || option.solveOnly {
			return nil, failure(InvalidInput, "options")
		}
		if e = option.apply(&s); e != nil {
			return nil, e
		}
	}
	var updateProxy *url.URL
	switch s.updatesMode.mode {
	case "inherit":
		updateProxy = s.proxy
	case "proxy":
		updateProxy, e = parseProxy(s.updatesMode.url)
		if e != nil {
			return nil, e
		}
	}
	stateKey, state, e := acquireBundleState(s, updateProxy)
	if e != nil {
		return nil, e
	}
	tr := newHTTPTransport(s, s.proxy)
	utr := newHTTPTransport(s, updateProxy)
	lifetime, cancel := context.WithCancel(context.Background())
	c := &Client{settings: s, http: newHTTPClient(tr), updates: newHTTPClient(utr), transport: tr, updateTransport: utr, updateProxy: updateProxy, lifetime: lifetime, cancel: cancel, permits: make(chan struct{}, s.maxInFlight), bundleState: state, bundleStateKey: stateKey}
	return c, nil
}
func (c *Client) Close() error {
	c.closeOnce.Do(func() {
		c.cancel()
		c.transport.CloseIdleConnections()
		c.updateTransport.CloseIdleConnections()
		releaseBundleState(c.bundleStateKey, c.bundleState)
	})
	return nil
}

func (c *Client) operation(parent context.Context) (context.Context, func(), error) {
	if parent == nil {
		return nil, nil, failure(InvalidInput, "context")
	}
	if c.lifetime.Err() != nil {
		return nil, nil, failure(ClientClosed, "client")
	}
	ctx, cancel := context.WithTimeout(parent, c.settings.timeout)
	stop := context.AfterFunc(c.lifetime, cancel)
	cleanup := func() { stop(); cancel() }
	if ctx.Err() != nil {
		cleanup()
		return nil, nil, contextError(ctx, "solve")
	}
	select {
	case c.permits <- struct{}{}:
		return ctx, func() { <-c.permits; cleanup() }, nil
	default:
		cleanup()
		return nil, nil, failure(ResourceLimit, "concurrency")
	}
}

// Solve creates and closes a client for one operation. Reuse a Client for pooling.
func Solve(ctx context.Context, anchorURL string, options ...Option) (SolveResult, error) {
	start := time.Now()
	if ctx == nil {
		return SolveResult{}, failure(InvalidInput, "context")
	}
	var clientOptions, solveOptions []Option
	for _, option := range options {
		if option.solveOnly {
			solveOptions = append(solveOptions, option)
		} else {
			clientOptions = append(clientOptions, option)
		}
	}
	c, e := NewClient(clientOptions...)
	if e != nil {
		return SolveResult{}, e
	}
	defer c.Close()
	operationCtx, cancel := context.WithDeadline(ctx, start.Add(c.settings.timeout))
	defer cancel()
	result, e := c.Solve(operationCtx, anchorURL, solveOptions...)
	if e == nil {
		result.Duration = time.Since(start)
	}
	return result, e
}

// Solve uses a single signed bundle snapshot and version for the entire operation.
// WithAction, WithBG, WithUserAgent and WithHostname are per-operation options.
func (c *Client) Solve(parent context.Context, anchorURL string, options ...Option) (SolveResult, error) {
	start := time.Now()
	ctx, done, e := c.operation(parent)
	if e != nil {
		return SolveResult{}, e
	}
	defer done()
	a, e := parseAnchor(anchorURL)
	if e != nil {
		return SolveResult{}, e
	}
	operation := settings{}
	for _, option := range options {
		if !option.solveOnly || option.apply == nil {
			return SolveResult{}, failure(InvalidInput, "solve-options")
		}
		if e = option.apply(&operation); e != nil {
			return SolveResult{}, e
		}
	}
	if operation.actionSet {
		a.values.Set("sa", operation.action)
	} else if action, exists := a.values["sa"]; exists && (action[0] == "" || len(action[0]) > 256) {
		return SolveResult{}, failure(InvalidInput, "action")
	}
	if operation.hostnameSet {
		value, err := hostnameOrigin(a.values.Get("co"), operation.hostname)
		if err != nil {
			return SolveResult{}, err
		}
		a.values.Set("co", value)
	}
	deadline, _ := ctx.Deadline()
	b, e := c.selectBundle(ctx, deadline, false)
	if e != nil {
		return SolveResult{}, e
	}
	httpClient, e := operationHTTPClient(c.http)
	if e != nil {
		return SolveResult{}, e
	}
	origin := "https://" + a.url.Host
	if c.settings.testOrigin != "" {
		origin = c.settings.testOrigin
	}
	scriptPath := "/recaptcha/api.js"
	if a.family == "enterprise" {
		scriptPath = "/recaptcha/enterprise.js"
	}
	scriptURL := origin + scriptPath + "?" + url.Values{"render": {a.values.Get("k")}}.Encode()
	script, e := fetch(ctx, httpClient, c.settings.proxy, http.MethodGet, scriptURL, nil, maxResponseBytes, "version", operation.userAgent)
	if e != nil {
		return SolveResult{}, e
	}
	version, e := parseVersion(script)
	if e != nil {
		return SolveResult{}, e
	}
	a.values.Set("v", version)
	anchorBody, e := fetch(ctx, httpClient, c.settings.proxy, http.MethodGet, origin+a.url.Path+"?"+a.values.Encode(), nil, maxResponseBytes, "anchor", operation.userAgent)
	if e != nil {
		return SolveResult{}, e
	}
	token, e := parseAnchorToken(anchorBody)
	if e != nil {
		return SolveResult{}, e
	}
	fields := reloadFields(a, b, version, token)
	if operation.bgSet {
		fields.Set("bg", operation.bg)
	}
	reloadURL := origin + "/recaptcha/" + a.family + "/reload?" + url.Values{"k": {a.values.Get("k")}}.Encode()
	reload, e := fetch(ctx, httpClient, c.settings.proxy, http.MethodPost, reloadURL, strings.NewReader(fields.Encode()), maxResponseBytes, "reload", operation.userAgent)
	if e != nil {
		return SolveResult{}, e
	}
	result, e := parseReload(reload)
	if e != nil {
		return SolveResult{}, e
	}
	if ctx.Err() != nil {
		return SolveResult{}, contextError(ctx, "solve")
	}
	return SolveResult{Token: result, BundleRevision: b.Revision, ProtocolVersion: version, Duration: time.Since(start)}, nil
}

// Refresh explicitly checks the configured channel. Offline mode never uses it.
func (c *Client) Refresh(parent context.Context) (BundleInfo, error) {
	ctx, done, e := c.operation(parent)
	if e != nil {
		return BundleInfo{}, e
	}
	defer done()
	deadline, _ := ctx.Deadline()
	b, e := c.selectBundle(ctx, deadline, true)
	if e != nil {
		return BundleInfo{}, e
	}
	if ctx.Err() != nil {
		return BundleInfo{}, contextError(ctx, "updates")
	}
	mode := "memory"
	if !c.settings.memory {
		mode = "disk"
	}
	return BundleInfo{Revision: b.Revision, ExpiresAt: time.Unix(b.ExpiresAt, 0), CacheMode: mode}, nil
}
func (c *Client) selectBundle(ctx context.Context, deadline time.Time, force bool) (*bundle, error) {
	select {
	case c.bundleState.gate <- struct{}{}:
		defer func() { <-c.bundleState.gate }()
	case <-ctx.Done():
		return nil, contextError(ctx, "bundle")
	}
	if ctx.Err() != nil {
		return nil, contextError(ctx, "bundle")
	}
	if !c.settings.memory {
		unlock, e := cacheLockContext(ctx, c.settings.cachePath)
		if e != nil {
			return nil, e
		}
		defer unlock()
		cached, e := readCache(c.settings.cachePath, c.settings, time.Now())
		if e != nil {
			return nil, e
		}
		if cached != nil {
			if c.bundleState.current == nil || compareRevision(cached.Revision, c.bundleState.current.Revision) > 0 {
				c.bundleState.current = cached
			} else if compareRevision(cached.Revision, c.bundleState.current.Revision) == 0 && cached.digest != c.bundleState.current.digest {
				return nil, failure(ConfigInvalid, "cache")
			}
		}
	}
	current := c.bundleState.current
	if c.settings.pin != "" && current != nil && compareRevision(c.settings.pin, current.Revision) < 0 {
		return nil, failure(ConfigRollback, "pin")
	}
	if c.settings.pin != "" && current != nil && current.Revision == c.settings.pin && current.ExpiresAt <= deadline.Unix() {
		return nil, failure(ConfigExpired, "bundle")
	}
	valid := usableBundle(current, deadline, c.settings.pin)
	needRefresh := !valid || (c.settings.pin == "" && (force || time.Since(c.bundleState.lastRefresh) >= c.settings.refreshInterval))
	if c.settings.offline || c.settings.bundleURL == "" || !needRefresh {
		if valid {
			return current, nil
		}
		if current != nil && current.ExpiresAt <= deadline.Unix() {
			return nil, failure(ConfigExpired, "bundle")
		}
		return nil, failure(ConfigUnavailable, "bundle")
	}
	updateCtx, cancel := context.WithTimeout(ctx, connectTimeout)
	defer cancel()
	c.bundleState.lastRefresh = time.Now()
	raw, e := fetch(updateCtx, c.updates, c.updateProxy, http.MethodGet, c.settings.bundleURL, nil, maxBundleBytes, "updates")
	var candidate *bundle
	if e == nil {
		candidate, e = verifyBundle(raw, c.settings.trusted, time.Now())
	}
	if e == nil {
		e = newerBundle(current, candidate)
	}
	if e == nil {
		// Authenticated revisions establish the rollback floor even when expired
		// or unavailable for the requested pin.
		c.bundleState.current = candidate
		current = candidate
		valid = usableBundle(current, deadline, c.settings.pin)
		if !c.settings.memory {
			if e = writeCache(c.settings.cachePath, candidate); e != nil {
				return nil, e
			}
		}
	}
	if e == nil && c.settings.pin != "" && candidate.Revision != c.settings.pin {
		e = failure(ConfigUnavailable, "pin")
	}
	if e == nil && !usableBundle(candidate, deadline, c.settings.pin) {
		e = failure(ConfigExpired, "bundle")
	}
	if e != nil {
		if ctx.Err() != nil {
			return nil, contextError(ctx, "updates")
		}
		if valid && !force {
			return current, nil
		}
		return nil, e
	}
	c.bundleState.current = candidate
	c.bundleState.lastRefresh = time.Now()
	return candidate, nil
}

// Ensure fmt never falls back to formatting unexported client internals.
var _ fmt.Stringer = (*Client)(nil)

// LoadBG selects verified compatible data using the same update, pin, expiry, and
// cache policy as Solve. It does not send captcha requests.
func (c *Client) LoadBG(parent context.Context) (string, error) {
	ctx, done, e := c.operation(parent)
	if e != nil {
		return "", e
	}
	defer done()
	deadline, _ := ctx.Deadline()
	b, e := c.selectBundle(ctx, deadline, false)
	if e != nil {
		return "", e
	}
	if ctx.Err() != nil {
		return "", contextError(ctx, "bundle")
	}
	return b.Fields["bg"], nil
}

// LoadBG creates and closes a client to load a verified bundle value once.
func LoadBG(ctx context.Context, options ...Option) (string, error) {
	start := time.Now()
	if ctx == nil {
		return "", failure(InvalidInput, "context")
	}
	c, e := NewClient(options...)
	if e != nil {
		return "", e
	}
	defer c.Close()
	operationCtx, cancel := context.WithDeadline(ctx, start.Add(c.settings.timeout))
	defer cancel()
	return c.LoadBG(operationCtx)
}
