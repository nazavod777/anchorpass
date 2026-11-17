package anchorpass

import (
	"context"
	"errors"
	"io"
	"os"
	"path/filepath"
	"time"
)

func readCache(path string, keys settings, now time.Time) (*bundle, error) {
	file, e := openRegularFile(path, os.O_RDONLY, 0)
	if errors.Is(e, os.ErrNotExist) {
		return nil, nil
	}
	if e != nil {
		return nil, failure(ConfigUnavailable, "cache")
	}
	defer file.Close()
	raw, e := io.ReadAll(io.LimitReader(file, maxBundleBytes+1))
	if e != nil {
		return nil, failure(ConfigUnavailable, "cache")
	}
	if len(raw) > maxBundleBytes {
		return nil, failure(ConfigInvalid, "cache")
	}
	return verifyBundle(raw, keys.trusted, now)
}
func writeCache(path string, b *bundle) error {
	dir := filepath.Dir(path)
	file, e := os.CreateTemp(dir, ".anchorpass-*")
	if e != nil {
		return failure(ConfigUnavailable, "cache")
	}
	name := file.Name()
	defer os.Remove(name)
	if _, e = file.Write(b.envelope); e == nil {
		e = file.Sync()
	}
	closeErr := file.Close()
	if e != nil || closeErr != nil {
		return failure(ConfigUnavailable, "cache")
	}
	if e = os.Rename(name, path); e != nil {
		return failure(ConfigUnavailable, "cache")
	}
	directory, e := os.Open(dir)
	if e != nil {
		return failure(ConfigUnavailable, "cache")
	}
	defer directory.Close()
	if e = directory.Sync(); e != nil {
		return failure(ConfigUnavailable, "cache")
	}
	return nil
}
func cacheLockContext(parent context.Context, path string) (func(), error) {
	if e := os.MkdirAll(filepath.Dir(path), 0700); e != nil {
		return nil, failure(ConfigUnavailable, "cache")
	}
	ctx, cancel := context.WithTimeout(parent, connectTimeout)
	defer cancel()
	return lockCache(ctx, path+".lock")
}
