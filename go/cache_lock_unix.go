//go:build linux || darwin || freebsd || openbsd || netbsd || dragonfly

package anchorpass

import (
	"context"
	"errors"
	"os"
	"syscall"
	"time"
)

// The lock file stays in place; kernel locks are released even after process death.
func lockCache(ctx context.Context, path string) (func(), error) {
	file, e := openRegularFile(path, os.O_CREATE|os.O_RDWR, 0600)
	if e != nil {
		return nil, failure(ConfigUnavailable, "cache-lock")
	}
	timer := time.NewTicker(10 * time.Millisecond)
	defer timer.Stop()
	for {
		e = syscall.Flock(int(file.Fd()), syscall.LOCK_EX|syscall.LOCK_NB)
		if e == nil {
			return func() { _ = syscall.Flock(int(file.Fd()), syscall.LOCK_UN); _ = file.Close() }, nil
		}
		if !errors.Is(e, syscall.EWOULDBLOCK) && !errors.Is(e, syscall.EAGAIN) {
			_ = file.Close()
			return nil, failure(ConfigUnavailable, "cache-lock")
		}
		select {
		case <-ctx.Done():
			_ = file.Close()
			return nil, contextError(ctx, "cache-lock")
		case <-timer.C:
		}
	}
}

// Nonblocking open followed by fstat cannot get stuck opening an accidental FIFO.
// O_NOFOLLOW prevents a symlink swap from bypassing the regular-file boundary.
func openRegularFile(path string, flags int, mode os.FileMode) (*os.File, error) {
	file, e := os.OpenFile(path, flags|syscall.O_NONBLOCK|syscall.O_NOFOLLOW, mode)
	if e != nil {
		return nil, e
	}
	info, e := file.Stat()
	if e != nil {
		file.Close()
		return nil, e
	}
	if !info.Mode().IsRegular() {
		file.Close()
		return nil, errors.New("cache must be a regular file")
	}
	return file, nil
}
