// Command live-check performs one operation for the supervising demo verifier.
package main

import (
	"context"
	"encoding/json"
	"io"
	"os"
	"time"

	anchorpass "github.com/nazavod777/anchorpass/go"
)

func main() {
	var input struct {
		Anchor    string `json:"anchor"`
		Action    string `json:"action"`
		Proxy     string `json:"proxy"`
		TimeoutMS int64  `json:"timeout_ms"`
	}
	if json.NewDecoder(io.LimitReader(os.Stdin, 65536)).Decode(&input) != nil || input.TimeoutMS <= 0 || input.TimeoutMS > 60000 {
		os.Exit(2)
	}
	options := []anchorpass.Option{anchorpass.WithTimeout(time.Duration(input.TimeoutMS) * time.Millisecond)}
	if input.Proxy != "" {
		options = append(options, anchorpass.WithProxy(input.Proxy))
	}
	client, err := anchorpass.NewClient(options...)
	if err != nil {
		os.Exit(2)
	}
	defer client.Close()
	result, err := client.Solve(context.Background(), input.Anchor, anchorpass.WithAction(input.Action))
	if err != nil {
		if public, ok := err.(*anchorpass.Error); ok {
			_ = json.NewEncoder(os.Stdout).Encode(map[string]string{"error": string(public.Code), "stage": public.Stage})
		}
		os.Exit(1)
	}
	_ = json.NewEncoder(os.Stdout).Encode(map[string]string{"token": result.Token, "bundle_revision": result.BundleRevision})
}
