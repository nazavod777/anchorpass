//go:build linux || darwin || freebsd || openbsd || netbsd || dragonfly

package anchorpass

import (
	"context"
	"os"
	"os/exec"
	"path/filepath"
	"syscall"
	"testing"
	"time"
)

func TestCacheRejectsSpecialFilesWithoutBlocking(t *testing.T) {
	if path := os.Getenv("ANCHORPASS_TEST_SPECIAL_CACHE"); path != "" {
		c := testClient(t, "", WithCachePath(path), WithOffline(true), WithTimeout(50*time.Millisecond))
		_, e := c.LoadBG(context.Background())
		requireCode(t, e, ConfigUnavailable)
		return
	}
	for _, mode := range []string{"fifo-cache", "fifo-lock", "symlink-cache"} {
		t.Run(mode, func(t *testing.T) {
			path := filepath.Join(t.TempDir(), "bundle.json")
			switch mode {
			case "fifo-cache":
				if e := syscall.Mkfifo(path, 0600); e != nil {
					t.Fatal(e)
				}
			case "fifo-lock":
				if e := syscall.Mkfifo(path+".lock", 0600); e != nil {
					t.Fatal(e)
				}
			case "symlink-cache":
				target := path + ".target"
				if e := os.WriteFile(target, bundledEnvelope, 0600); e != nil {
					t.Fatal(e)
				}
				if e := os.Symlink(target, path); e != nil {
					t.Fatal(e)
				}
			}
			ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
			defer cancel()
			binary, e := os.Executable()
			if e != nil {
				t.Fatal(e)
			}
			command := exec.CommandContext(ctx, binary, "-test.run=^TestCacheRejectsSpecialFilesWithoutBlocking$")
			command.Env = append(os.Environ(), "ANCHORPASS_TEST_SPECIAL_CACHE="+path)
			output, e := command.CombinedOutput()
			if e != nil {
				t.Fatalf("special cache blocked or failed: %s %v", output, e)
			}
		})
	}
}
