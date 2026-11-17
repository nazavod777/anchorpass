package anchorpass

import (
	"context"
	"errors"
	"net"
	"os"
	"testing"
)

// Host-file resolution remains available for localhost. All network DNS is denied
// so the remote-only SOCKS name cannot accidentally depend on an external server.
func TestMain(m *testing.M) {
	net.DefaultResolver = &net.Resolver{PreferGo: true, Dial: func(context.Context, string, string) (net.Conn, error) {
		return nil, errors.New("network DNS disabled by local test fixture")
	}}
	os.Exit(m.Run())
}
