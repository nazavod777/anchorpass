package anchorpass

import (
	"context"
	"encoding/binary"
	"fmt"
	"io"
	"net"
	"strings"
	"sync"
	"testing"
	"time"
)

type socksRequest struct {
	host               string
	addressType        byte
	username, password string
}
type socksFixture struct {
	listener           net.Listener
	mu                 sync.Mutex
	requests           []socksRequest
	connections        map[net.Conn]bool
	target             string
	wantUser, wantPass string
	workers            sync.WaitGroup
}

func startSOCKS(t *testing.T, target, user, password string) *socksFixture {
	t.Helper()
	listener, e := net.Listen("tcp", "127.0.0.1:0")
	if e != nil {
		t.Fatal(e)
	}
	fixture := &socksFixture{listener: listener, target: target, wantUser: user, wantPass: password, connections: map[net.Conn]bool{}}
	fixture.workers.Add(1)
	go func() {
		defer fixture.workers.Done()
		for {
			conn, e := listener.Accept()
			if e != nil {
				return
			}
			fixture.mu.Lock()
			fixture.connections[conn] = true
			fixture.mu.Unlock()
			fixture.workers.Add(1)
			go func() {
				defer fixture.workers.Done()
				defer conn.Close()
				defer func() { fixture.mu.Lock(); delete(fixture.connections, conn); fixture.mu.Unlock() }()
				fixture.handle(conn)
			}()
		}
	}()
	t.Cleanup(func() {
		listener.Close()
		fixture.mu.Lock()
		for conn := range fixture.connections {
			conn.Close()
		}
		fixture.mu.Unlock()
		fixture.workers.Wait()
	})
	return fixture
}
func (f *socksFixture) proxyURL(scheme string) string {
	credentials := ""
	if f.wantUser != "" {
		credentials = "user%2B:p%40ss@"
	}
	return scheme + "://" + credentials + f.listener.Addr().String()
}
func (f *socksFixture) handle(conn net.Conn) {
	_ = conn.SetDeadline(time.Now().Add(3 * time.Second))
	header := make([]byte, 2)
	if _, e := io.ReadFull(conn, header); e != nil || header[0] != 5 {
		return
	}
	methods := make([]byte, int(header[1]))
	if _, e := io.ReadFull(conn, methods); e != nil {
		return
	}
	var request socksRequest
	if f.wantUser != "" {
		if _, e := conn.Write([]byte{5, 2}); e != nil {
			return
		}
		if _, e := io.ReadFull(conn, header); e != nil {
			return
		}
		user := make([]byte, int(header[1]))
		if _, e := io.ReadFull(conn, user); e != nil {
			return
		}
		var n [1]byte
		if _, e := io.ReadFull(conn, n[:]); e != nil {
			return
		}
		password := make([]byte, int(n[0]))
		if _, e := io.ReadFull(conn, password); e != nil {
			return
		}
		request.username = string(user)
		request.password = string(password)
		status := byte(0)
		if request.username != f.wantUser || request.password != f.wantPass {
			status = 1
		}
		if _, e := conn.Write([]byte{1, status}); e != nil || status != 0 {
			return
		}
	} else {
		if _, e := conn.Write([]byte{5, 0}); e != nil {
			return
		}
	}
	prefix := make([]byte, 4)
	if _, e := io.ReadFull(conn, prefix); e != nil {
		return
	}
	request.addressType = prefix[3]
	size := 0
	switch prefix[3] {
	case 1:
		size = 4
	case 4:
		size = 16
	case 3:
		var n [1]byte
		if _, e := io.ReadFull(conn, n[:]); e != nil {
			return
		}
		size = int(n[0])
	default:
		return
	}
	host := make([]byte, size)
	if _, e := io.ReadFull(conn, host); e != nil {
		return
	}
	if prefix[3] == 3 {
		request.host = string(host)
	} else {
		request.host = net.IP(host).String()
	}
	var port [2]byte
	if _, e := io.ReadFull(conn, port[:]); e != nil {
		return
	}
	f.mu.Lock()
	f.requests = append(f.requests, request)
	f.mu.Unlock()
	target := f.target
	if target == "" {
		target = net.JoinHostPort(request.host, fmt.Sprint(binary.BigEndian.Uint16(port[:])))
	}
	upstream, e := net.DialTimeout("tcp", target, time.Second)
	if e != nil {
		return
	}
	defer upstream.Close()
	if _, e := conn.Write([]byte{5, 0, 0, 1, 127, 0, 0, 1, 0, 1}); e != nil {
		return
	}
	_ = conn.SetDeadline(time.Time{})
	copied := make(chan struct{})
	go func() { _, _ = io.Copy(upstream, conn); upstream.Close(); close(copied) }()
	_, _ = io.Copy(conn, upstream)
	conn.Close()
	<-copied
}
func TestSOCKSDNSAndAuthentication(t *testing.T) {
	origin, roots, requests := protocolFixture(t)
	target := strings.TrimPrefix(origin.URL, "https://")
	_, port, e := net.SplitHostPort(target)
	if e != nil {
		t.Fatal(e)
	}
	// Only the proxy maps this name to the test origin. Client TLS still sees it.
	remoteOrigin := "https://proxy-only.invalid:" + port
	remote := startSOCKS(t, target, "user+", "p@ss")
	c := testClient(t, remoteOrigin, WithRootCAs(roots), WithProxy(remote.proxyURL("socks5h")), WithOffline(true))
	if _, e := c.Solve(context.Background(), goodAnchor, WithAction("explicit-action")); e != nil {
		t.Fatal(e)
	}
	remote.mu.Lock()
	if len(remote.requests) != 1 || remote.requests[0].addressType != 3 || remote.requests[0].host != "proxy-only.invalid" || remote.requests[0].username != "user+" || remote.requests[0].password != "p@ss" {
		t.Error("SOCKS remote DNS/auth not preserved")
	}
	remote.mu.Unlock()
	before := requests.Load()
	localMissing := testClient(t, remoteOrigin, WithRootCAs(roots), WithProxy(remote.proxyURL("socks5")), WithOffline(true))
	_, e = localMissing.Solve(context.Background(), goodAnchor)
	requireCode(t, e, TransportError)
	if requests.Load() != before {
		t.Fatal("local DNS unexpectedly resolved remote-only hostname")
	}
	local := startSOCKS(t, target, "", "")
	localClient := testClient(t, origin.URL, WithRootCAs(roots), WithProxy(local.proxyURL("socks5")), WithOffline(true))
	if _, e := localClient.Solve(context.Background(), goodAnchor, WithAction("explicit-action")); e != nil {
		t.Fatal(e)
	}
	local.mu.Lock()
	if len(local.requests) != 1 || local.requests[0].addressType != 1 || net.ParseIP(local.requests[0].host) == nil {
		t.Error("SOCKS local route did not send an IP")
	}
	local.mu.Unlock()
	bad := testClient(t, origin.URL, WithRootCAs(roots), WithProxy("socks5h://wrong:wrong@"+remote.listener.Addr().String()), WithOffline(true))
	before = requests.Load()
	_, e = bad.Solve(context.Background(), goodAnchor)
	requireCode(t, e, ProxyAuthError)
	if requests.Load() != before {
		t.Fatal("bad SOCKS credentials reached origin")
	}
}
func TestSOCKSLocalPreservesHostAndSNI(t *testing.T) {
	origin, roots, _ := protocolFixture(t, "localhost")
	target := strings.TrimPrefix(origin.URL, "https://")
	_, port, e := net.SplitHostPort(target)
	if e != nil {
		t.Fatal(e)
	}
	proxy := startSOCKS(t, target, "", "")
	c := testClient(t, "https://localhost:"+port, WithRootCAs(roots), WithProxy(proxy.proxyURL("socks5")), WithOffline(true))
	if _, e = c.Solve(context.Background(), goodAnchor, WithAction("explicit-action")); e != nil {
		t.Fatal(e)
	}
	proxy.mu.Lock()
	defer proxy.mu.Unlock()
	if len(proxy.requests) != 1 || proxy.requests[0].addressType == 3 {
		t.Fatal("local DNS sent hostname instead of resolved IP")
	}
}

func TestSOCKSRemotePreservesHostAndSNI(t *testing.T) {
	origin, roots, _ := protocolFixture(t, "proxy-only.invalid")
	target := strings.TrimPrefix(origin.URL, "https://")
	_, port, e := net.SplitHostPort(target)
	if e != nil {
		t.Fatal(e)
	}
	proxy := startSOCKS(t, target, "", "")
	c := testClient(t, "https://proxy-only.invalid:"+port, WithRootCAs(roots), WithProxy(proxy.proxyURL("socks5h")), WithOffline(true))
	if _, e = c.Solve(context.Background(), goodAnchor, WithAction("explicit-action")); e != nil {
		t.Fatal(e)
	}
}
