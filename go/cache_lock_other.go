//go:build !linux && !darwin && !freebsd && !openbsd && !netbsd && !dragonfly

package anchorpass

import (
	"context"
	"errors"
	"os"
)

// Memory mode remains available on platforms without a verified file lock adapter.
func lockCache(_ context.Context, _ string) (func(), error) {
	return nil, failure(ConfigUnavailable, "cache-lock")
}

func openRegularFile(path string, flags int, mode os.FileMode) (*os.File, error) {
	info, e := os.Lstat(path)
	if e != nil {
		return nil, e
	}
	if !info.Mode().IsRegular() {
		return nil, errors.New("cache must be a regular file")
	}
	return os.OpenFile(path, flags, mode)
}
