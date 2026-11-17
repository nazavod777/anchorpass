package anchorpass

import (
	"net/http"
	"net/http/cookiejar"

	"golang.org/x/net/publicsuffix"
)

const maxOperationCookies = 64
const maxCookieBytes = 16384

// One operation owns its jar and budgets while keeping the client's pooled transport.
type cookieTransport struct {
	base  http.RoundTripper
	count int
	bytes int
}

func (t *cookieTransport) RoundTrip(req *http.Request) (*http.Response, error) {
	if len(req.Header.Get("Cookie")) > maxCookieBytes {
		return nil, failure(ResourceLimit, "cookies")
	}
	response, err := t.base.RoundTrip(req)
	if err != nil {
		return nil, err
	}
	for _, value := range response.Header.Values("Set-Cookie") {
		t.count++
		t.bytes += len(value)
		if t.count > maxOperationCookies || t.bytes > maxCookieBytes {
			response.Body.Close()
			return nil, failure(ResourceLimit, "cookies")
		}
	}
	return response, nil
}

func operationHTTPClient(base *http.Client) (*http.Client, error) {
	jar, err := cookiejar.New(&cookiejar.Options{PublicSuffixList: publicsuffix.List})
	if err != nil {
		return nil, failure(InvalidInput, "cookies")
	}
	client := *base
	client.Jar = jar
	client.Transport = &cookieTransport{base: base.Transport}
	return &client, nil
}
