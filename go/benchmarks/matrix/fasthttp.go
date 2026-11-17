package main

import (
	"bufio"
	"bytes"
	"compress/gzip"
	"context"
	"crypto/tls"
	"crypto/x509"
	"encoding/base64"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/url"
	"time"

	"github.com/valyala/fasthttp"
)

type fastRequester struct{ client *fasthttp.Client }

// Hide the proxy TLS Handshake method: fasthttp otherwise treats the tunnel's
// outer TLS connection as target TLS and skips the required inner handshake.
type fastTunnelConn struct{ net.Conn }

func newFastRequester(p profile, tr *http.Transport, proxyURL *url.URL, targetRoots *x509.CertPool) *fastRequester {
	timeout := time.Duration(p.TimeoutMS) * time.Millisecond
	dial := func(address string, budget time.Duration) (net.Conn, error) {
		ctx, cancel := context.WithTimeout(context.Background(), budget)
		defer cancel()
		if proxyURL == nil || proxyURL.Scheme == "socks5" || proxyURL.Scheme == "socks5h" {
			return tr.DialContext(ctx, "tcp", address)
		}
		var conn net.Conn
		var err error
		if proxyURL.Scheme == "https" {
			conn, err = tr.DialTLSContext(ctx, "tcp", proxyURL.Host)
		} else {
			conn, err = tr.DialContext(ctx, "tcp", proxyURL.Host)
		}
		if err != nil {
			return nil, err
		}
		success := false
		defer func() {
			if !success {
				conn.Close()
			}
		}()
		deadline, _ := ctx.Deadline()
		if err = conn.SetDeadline(deadline); err != nil {
			return nil, err
		}
		request := &http.Request{Method: http.MethodConnect, URL: &url.URL{Opaque: address}, Host: address, Header: http.Header{}}
		if proxyURL.User != nil {
			password, _ := proxyURL.User.Password()
			request.Header.Set("Proxy-Authorization", "Basic "+base64.StdEncoding.EncodeToString([]byte(proxyURL.User.Username()+":"+password)))
		}
		if err = request.Write(conn); err != nil {
			return nil, err
		}
		limited := &io.LimitedReader{R: conn, N: 65537}
		reader := bufio.NewReader(limited)
		response, err := http.ReadResponse(reader, request)
		if err != nil {
			return nil, err
		}
		if limited.N == 0 {
			return nil, categorizedError{"response_limit", "CONNECT header limit"}
		}
		if response.StatusCode != 200 {
			return nil, categorizedError{"proxy", fmt.Sprintf("CONNECT status %d", response.StatusCode)}
		}
		if reader.Buffered() != 0 {
			return nil, categorizedError{"transport", "unexpected CONNECT tunnel bytes"}
		}
		if err = conn.SetDeadline(time.Time{}); err != nil {
			return nil, err
		}
		success = true
		return &fastTunnelConn{Conn: conn}, nil
	}
	return &fastRequester{client: &fasthttp.Client{DialTimeout: dial, TLSConfig: &tls.Config{MinVersion: tls.VersionTLS12, RootCAs: targetRoots}, MaxConnsPerHost: p.Concurrency, MaxIdleConnDuration: 30 * time.Second, ReadTimeout: timeout, WriteTimeout: timeout, MaxResponseBodySize: p.MaxResponseBytes + 65536, MaxIdemponentCallAttempts: 1, NoDefaultUserAgentHeader: true, DisablePathNormalizing: true}}
}
func (r *fastRequester) exchange(ctx context.Context, method, address, form, cookie string, limit int) ([]byte, http.Header, error) {
	request := fasthttp.AcquireRequest()
	defer fasthttp.ReleaseRequest(request)
	response := fasthttp.AcquireResponse()
	defer fasthttp.ReleaseResponse(response)
	request.SetRequestURI(address)
	request.Header.SetMethod(method)
	request.Header.SetUserAgent("anchorpass-matrix")
	request.Header.Set("Accept-Encoding", "gzip")
	if cookie != "" {
		request.Header.Set("Cookie", cookie)
	}
	if method == http.MethodPost {
		request.Header.SetContentType("application/x-www-form-urlencoded")
		request.SetBodyString(form)
	}
	deadline, _ := ctx.Deadline()
	if err := r.client.DoDeadline(request, response, deadline); err != nil {
		if err == fasthttp.ErrConnectionClosed {
			return nil, nil, categorizedError{"transport", "connection closed"}
		}
		if err == fasthttp.ErrTimeout {
			return nil, nil, context.DeadlineExceeded
		}
		if err == fasthttp.ErrBodyTooLarge {
			return nil, nil, categorizedError{"response_limit", "response limit"}
		}
		return nil, nil, err
	}
	if response.StatusCode() != 200 {
		category := "http_status"
		if response.StatusCode() == 407 {
			category = "proxy"
		}
		return nil, nil, categorizedError{category, fmt.Sprintf("HTTP status %d", response.StatusCode())}
	}
	var reader io.Reader = bytes.NewReader(response.Body())
	if bytes.EqualFold(response.Header.Peek("Content-Encoding"), []byte("gzip")) {
		decoded, err := gzip.NewReader(reader)
		if err != nil {
			return nil, nil, err
		}
		defer decoded.Close()
		reader = decoded
	}
	body, err := io.ReadAll(io.LimitReader(reader, int64(limit)+1))
	if err != nil {
		return nil, nil, err
	}
	if len(body) > limit {
		return nil, nil, categorizedError{"response_limit", "decompressed response exceeds limit"}
	}
	headers := http.Header{}
	response.Header.VisitAll(func(k, v []byte) { headers.Add(string(k), string(v)) })
	return body, headers, nil
}
