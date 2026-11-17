package anchorpass

import (
	"crypto/sha256"
	"encoding/hex"
	"io"
	"net/url"
	"sort"
	"strconv"
	"sync"
	"time"
)

const maxUpdateStates = 128

type sharedBundles struct {
	gate        chan struct{}
	current     *bundle // guarded by gate
	lastRefresh time.Time
	refs        int // guarded by bundleRegistry
	baseKey     string
	shareable   bool
}

var bundleRegistry = struct {
	sync.Mutex
	states map[string]*sharedBundles
}{states: make(map[string]*sharedBundles)}

// Only verified public data is shared. The registry key is a digest so it does not
// keep another printable copy of proxy credentials or channel query parameters.
func bundleStateKey(s settings, proxy *url.URL) string {
	h := sha256.New()
	parts := []string{protocolFamily, s.bundleURL, s.cachePath}
	if proxy != nil {
		parts = append(parts, proxy.String())
	}
	for _, part := range parts {
		_, _ = io.WriteString(h, part)
		_, _ = h.Write([]byte{0})
	}
	ids := make([]string, 0, len(s.trusted))
	for id := range s.trusted {
		ids = append(ids, id)
	}
	sort.Strings(ids)
	for _, id := range ids {
		_, _ = io.WriteString(h, id)
		_, _ = h.Write([]byte{0})
		_, _ = h.Write(s.trusted[id])
	}
	return hex.EncodeToString(h.Sum(nil))
}
func acquireBundleState(s settings, proxy *url.URL) (string, *sharedBundles, error) {
	// CertPool.Equal compares certificates, not AddCertWithConstraint callbacks.
	// Opaque custom trust policies must never share downloaded state across clients.
	shareable := s.roots == nil && s.proxyRoots == nil
	key := bundleStateKey(s, proxy)
	bundleRegistry.Lock()
	defer bundleRegistry.Unlock()
	for registryKey, state := range bundleRegistry.states {
		if shareable && state.shareable && state.baseKey == key {
			state.refs++
			return registryKey, state, nil
		}
	}
	if len(bundleRegistry.states) >= maxUpdateStates {
		return "", nil, failure(ResourceLimit, "update-states")
	}
	current, _ := verifyBundle(bundledEnvelope, s.trusted, time.Now())
	state := &sharedBundles{gate: make(chan struct{}, 1), current: current, refs: 1, baseKey: key, shareable: shareable}
	// Custom-pool clients each occupy one bounded slot, even with identical options.
	for slot := 0; slot < maxUpdateStates; slot++ {
		registryKey := key + "/" + strconv.Itoa(slot)
		if bundleRegistry.states[registryKey] == nil {
			bundleRegistry.states[registryKey] = state
			return registryKey, state, nil
		}
	}
	return "", nil, failure(ResourceLimit, "update-states")
}
func releaseBundleState(key string, state *sharedBundles) {
	bundleRegistry.Lock()
	defer bundleRegistry.Unlock()
	state.refs--
	if state.refs == 0 && bundleRegistry.states[key] == state {
		delete(bundleRegistry.states, key)
	}
}
