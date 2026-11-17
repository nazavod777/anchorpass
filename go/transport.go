package anchorpass

import (
	"context"
	"crypto/tls"
	"encoding/binary"
	"errors"
	"io"
	"net"
	"net/http"
	"net/http/httptrace"
	"net/url"
	"strconv"
	"strings"
	"sync/atomic"
	"syscall"
	"time"
)

const connectTimeout = 5 * time.Second

type proxyConnectFailure struct{ cause error }

func (e proxyConnectFailure) Error() string { return "proxy connection failed" }
func (e proxyConnectFailure) Unwrap() error { return e.cause }

type proxyAuthFailure struct{}

func (proxyAuthFailure) Error() string { return "proxy authentication failed" }

func parseProxy(raw string) (*url.URL, error) {
	invalid := func() (*url.URL, error) { return nil, failure(InvalidProxy, "proxy") }
	if raw == "" || strings.ContainsAny(raw, " \t\r\n") || strings.ContainsAny(raw, "?#") || badEscape(raw) {
		return invalid()
	}
	u, e := url.Parse(raw)
	if e != nil || u.Opaque != "" || u.Hostname() == "" || u.Port() == "" || (u.Path != "" && u.Path != "/") {
		return invalid()
	}
	switch u.Scheme {
	case "http", "https", "socks5", "socks5h":
	default:
		return invalid()
	}
	port, e := strconv.Atoi(u.Port())
	if e != nil || port < 1 || port > 65535 || strconv.Itoa(port) != u.Port() {
		return invalid()
	}
	if strings.Contains(u.Hostname(), ":") && !strings.HasPrefix(u.Host, "[") {
		return invalid()
	}
	if strings.ContainsAny(u.Hostname(), "%\\/") {
		return invalid()
	}
	if u.User != nil {
		username := u.User.Username()
		password, _ := u.User.Password()
		for _, s := range []string{username, password} {
			if len(s) > 255 {
				return invalid()
			}
			for _, r := range s {
				if r < 32 || r > 126 {
					return invalid()
				}
			}
		}
		if strings.Contains(username, ":") {
			return invalid()
		}
	}
	return u, nil
}
func validateBundleURL(raw string) error {
	if len(raw) > 8192 || strings.ContainsAny(raw, " \t\r\n#") || badEscape(raw) {
		return failure(InvalidInput, "bundle-url")
	}
	u, e := url.Parse(raw)
	if e != nil || u.Hostname() == "" || u.User != nil || u.Opaque != "" {
		return failure(InvalidInput, "bundle-url")
	}
	if u.Scheme == "https" {
		return nil
	}
	ip := net.ParseIP(u.Hostname())
	if u.Scheme == "http" && (u.Hostname() == "localhost" || (ip != nil && ip.IsLoopback())) {
		return nil
	}
	return failure(InvalidInput, "bundle-url")
}

// idleConn bounds a stalled socket read in addition to the operation's total deadline.
type idleConn struct{ net.Conn }

func (c *idleConn) Read(p []byte) (int, error) {
	if e := c.SetReadDeadline(time.Now().Add(connectTimeout)); e != nil {
		return 0, e
	}
	return c.Conn.Read(p)
}
func (c *idleConn) Write(p []byte) (int, error) {
	if e := c.SetWriteDeadline(time.Now().Add(connectTimeout)); e != nil {
		return 0, e
	}
	return c.Conn.Write(p)
}
func newHTTPTransport(s settings, proxy *url.URL) *http.Transport {
	dialer := &net.Dialer{Timeout: connectTimeout, KeepAlive: 30 * time.Second}
	dial := func(ctx context.Context, network, address string) (net.Conn, error) {
		c, e := dialer.DialContext(ctx, network, address)
		if e != nil {
			if proxy != nil && address == proxy.Host {
				return nil, proxyConnectFailure{e}
			}
			return nil, e
		}
		return &idleConn{c}, nil
	}
	tr := &http.Transport{DialContext: dial, ForceAttemptHTTP2: false, MaxIdleConns: s.maxInFlight, MaxIdleConnsPerHost: s.maxInFlight, MaxConnsPerHost: s.maxInFlight, IdleConnTimeout: 60 * time.Second, TLSHandshakeTimeout: connectTimeout, ResponseHeaderTimeout: connectTimeout, ExpectContinueTimeout: time.Second, TLSClientConfig: &tls.Config{MinVersion: tls.VersionTLS12, RootCAs: s.roots}, OnProxyConnectResponse: func(_ context.Context, _ *url.URL, _ *http.Request, response *http.Response) error {
		if response.StatusCode == http.StatusProxyAuthRequired {
			return proxyAuthFailure{}
		}
		if response.StatusCode != http.StatusOK {
			return proxyConnectFailure{}
		}
		return nil
	}}
	if proxy != nil {
		if proxy.Scheme == "http" || proxy.Scheme == "https" {
			tr.Proxy = http.ProxyURL(proxy)
		} else {
			tr.DialContext = func(ctx context.Context, network, address string) (net.Conn, error) {
				return dialSOCKS(ctx, dialer, proxy, address)
			}
		}
	}
	// Only the first TLS hop uses DialTLSContext. net/http performs CONNECT and
	// verifies nested target TLS separately with TLSClientConfig's target roots.
	if proxy != nil && proxy.Scheme == "https" {
		tr.DialTLSContext = func(ctx context.Context, network, address string) (net.Conn, error) {
			handshakeCtx, cancel := context.WithTimeout(ctx, connectTimeout)
			defer cancel()
			raw, e := dialer.DialContext(handshakeCtx, network, address)
			if e != nil {
				return nil, proxyConnectFailure{e}
			}
			secure := tls.Client(&idleConn{raw}, &tls.Config{MinVersion: tls.VersionTLS12, RootCAs: s.proxyRoots, ServerName: proxy.Hostname()})
			if e = secure.HandshakeContext(handshakeCtx); e != nil {
				_ = raw.Close()
				return nil, proxyConnectFailure{e}
			}
			return secure, nil
		}
	}
	return tr
}
func newHTTPClient(tr *http.Transport) *http.Client {
	return &http.Client{Transport: tr, CheckRedirect: func(_ *http.Request, _ []*http.Request) error { return http.ErrUseLastResponse }}
}
func dialSOCKS(ctx context.Context, dialer *net.Dialer, proxy *url.URL, address string) (_ net.Conn, err error) {
	host, portText, e := net.SplitHostPort(address)
	if e != nil {
		return nil, e
	}
	port, e := strconv.Atoi(portText)
	if e != nil {
		return nil, e
	}
	if proxy.Scheme == "socks5" && net.ParseIP(host) == nil {
		ips, e := net.DefaultResolver.LookupIPAddr(ctx, host)
		if e != nil {
			return nil, e
		}
		if len(ips) == 0 {
			return nil, &net.DNSError{Err: "no addresses"}
		}
		host = ips[0].IP.String()
	}
	conn, e := dialer.DialContext(ctx, "tcp", proxy.Host)
	if e != nil {
		return nil, proxyConnectFailure{e}
	}
	defer func() {
		if err != nil {
			var auth proxyAuthFailure
			if !errors.As(err, &auth) {
				err = proxyConnectFailure{err}
			}
		}
	}()
	success := false
	defer func() {
		if !success {
			_ = conn.Close()
		}
	}()
	stop := context.AfterFunc(ctx, func() { _ = conn.Close() })
	defer stop()
	deadline := time.Now().Add(connectTimeout)
	if d, ok := ctx.Deadline(); ok && d.Before(deadline) {
		deadline = d
	}
	if e = conn.SetDeadline(deadline); e != nil {
		return nil, e
	}
	methods := []byte{5, 1, 0}
	if proxy.User != nil {
		methods = []byte{5, 1, 2}
	}
	if _, e = conn.Write(methods); e != nil {
		return nil, e
	}
	reply := make([]byte, 2)
	if _, e = io.ReadFull(conn, reply); e != nil {
		return nil, e
	}
	if reply[0] != 5 {
		return nil, errors.New("invalid SOCKS version")
	}
	if reply[1] == 2 && proxy.User != nil {
		username := proxy.User.Username()
		password, _ := proxy.User.Password()
		auth := append([]byte{1, byte(len(username))}, []byte(username)...)
		auth = append(auth, byte(len(password)))
		auth = append(auth, []byte(password)...)
		if _, e = conn.Write(auth); e != nil {
			return nil, e
		}
		if _, e = io.ReadFull(conn, reply); e != nil {
			return nil, e
		}
		if reply[0] != 1 || reply[1] != 0 {
			return nil, proxyAuthFailure{}
		}
	} else if reply[1] != 0 || proxy.User != nil {
		return nil, proxyAuthFailure{}
	}
	request := []byte{5, 1, 0}
	ip := net.ParseIP(host)
	if ip4 := ip.To4(); ip4 != nil {
		request = append(request, 1)
		request = append(request, ip4...)
	} else if ip != nil {
		request = append(request, 4)
		request = append(request, ip.To16()...)
	} else {
		if len(host) > 255 {
			return nil, errors.New("SOCKS hostname too long")
		}
		request = append(request, 3, byte(len(host)))
		request = append(request, []byte(host)...)
	}
	request = binary.BigEndian.AppendUint16(request, uint16(port))
	if _, e = conn.Write(request); e != nil {
		return nil, e
	}
	header := make([]byte, 4)
	if _, e = io.ReadFull(conn, header); e != nil {
		return nil, e
	}
	if header[0] != 5 || header[1] != 0 || header[2] != 0 {
		return nil, errors.New("SOCKS connection rejected")
	}
	length := 0
	switch header[3] {
	case 1:
		length = 4
	case 4:
		length = 16
	case 3:
		var n [1]byte
		if _, e = io.ReadFull(conn, n[:]); e != nil {
			return nil, e
		}
		length = int(n[0])
	default:
		return nil, errors.New("invalid SOCKS address")
	}
	if _, e = io.CopyN(io.Discard, conn, int64(length+2)); e != nil {
		return nil, e
	}
	if ctx.Err() != nil {
		return nil, ctx.Err()
	}
	if e = conn.SetDeadline(time.Time{}); e != nil {
		return nil, e
	}
	success = true
	return &idleConn{conn}, nil
}

// nonReplayableEmptyBody disables net/http's unbounded replay across stale idle
// connections. The transport probes this EOF-only body and emits an ordinary GET
// with no body, Content-Length, or Transfer-Encoding on the wire. GetBody stays
// nil, so retry ownership remains here while connection pooling stays enabled.
type nonReplayableEmptyBody struct{}

func (nonReplayableEmptyBody) Read([]byte) (int, error) { return 0, io.EOF }
func (nonReplayableEmptyBody) Close() error             { return nil }
func retryableGETError(err error) bool {
	return errors.Is(err, io.EOF) || errors.Is(err, io.ErrUnexpectedEOF) || errors.Is(err, syscall.ECONNRESET) || errors.Is(err, syscall.EPIPE)
}
func fetch(ctx context.Context, client *http.Client, proxy *url.URL, method, address string, body io.Reader, limit int, stage string, userAgent ...string) ([]byte, error) {
	attempts := 1
	if method == http.MethodGet {
		attempts = 2
	}
	var response *http.Response
	var e error
	for attempt := 0; attempt < attempts; attempt++ {
		var reused atomic.Bool
		traced := httptrace.WithClientTrace(ctx, &httptrace.ClientTrace{GotConn: func(info httptrace.GotConnInfo) { reused.Store(info.Reused) }})
		req, err := http.NewRequestWithContext(traced, method, address, body)
		if err != nil {
			return nil, failure(InvalidInput, stage)
		}
		req.Header.Set("User-Agent", "anchorpass/0.1.1")
		if len(userAgent) > 0 && userAgent[0] != "" {
			req.Header.Set("User-Agent", userAgent[0])
		}
		if method == http.MethodPost {
			req.Header.Set("Content-Type", "application/x-www-form-urlencoded")
		}
		if method == http.MethodGet {
			req.Body = nonReplayableEmptyBody{}
			req.GetBody = nil
		}
		response, e = client.Do(req)
		if e == nil || ctx.Err() != nil || method != http.MethodGet || !reused.Load() || !retryableGETError(e) {
			break
		}
	}
	if e != nil {
		if ctx.Err() != nil {
			return nil, contextError(ctx, stage)
		}
		var bounded *Error
		if errors.As(e, &bounded) && bounded.Code == ResourceLimit {
			return nil, failure(ResourceLimit, stage)
		}
		var auth proxyAuthFailure
		if errors.As(e, &auth) {
			return nil, failure(ProxyAuthError, stage)
		}
		var connection proxyConnectFailure
		if errors.As(e, &connection) {
			return nil, failure(ProxyConnectError, stage)
		}
		return nil, failure(TransportError, stage)
	}
	defer response.Body.Close()
	if response.StatusCode == 407 && proxy != nil {
		return nil, failure(ProxyAuthError, stage)
	}
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		return nil, failure(HTTPStatusError, stage)
	}
	raw, e := io.ReadAll(io.LimitReader(response.Body, int64(limit)+1))
	if e != nil {
		if ctx.Err() != nil {
			return nil, contextError(ctx, stage)
		}
		return nil, failure(TransportError, stage)
	}
	if len(raw) > limit {
		return nil, failure(ResourceLimit, stage)
	}
	return raw, nil
}
