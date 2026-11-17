// Command pressure exposes bounded public-API batches to the local fixture driver.
package main

import (
	"bufio"
	"context"
	"crypto/ed25519"
	"crypto/x509"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"sync"
	"time"

	anchorpass "github.com/nazavod777/anchorpass/go"
)

type job struct {
	Fixture struct {
		CAFile      string            `json:"ca_file"`
		BundleURL   string            `json:"bundle_url"`
		AnchorURL   string            `json:"anchor_url"`
		Proxies     map[string]string `json:"proxies"`
		TrustedKeys map[string]string `json:"trusted_keys"`
	} `json:"fixture"`
	CachePath string `json:"cache_path"`
}

type command struct {
	Op              string  `json:"op"`
	Offline         bool    `json:"offline"`
	ProxyRoute      string  `json:"proxy_route"`
	BundleURL       string  `json:"bundle_url"`
	RefreshInterval float64 `json:"refresh_interval"`
	TimeoutMS       int64   `json:"timeout_ms"`
	MaxInFlight     int     `json:"max_in_flight"`
	Count           int     `json:"count"`
	Concurrency     int     `json:"concurrency"`
	Milliseconds    int64   `json:"milliseconds"`
}

type sample struct {
	ElapsedNS int64   `json:"elapsed_ns"`
	Error     *string `json:"error"`
	Value     string  `json:"value,omitempty"`
}

func create(j job, c command) (*anchorpass.Client, error) {
	if c.TimeoutMS < 1 || c.TimeoutMS > 60000 || c.RefreshInterval < 0 || c.RefreshInterval > 86400 {
		return nil, errors.New("invalid create limits")
	}
	proxy, ok := j.Fixture.Proxies[c.ProxyRoute]
	if !ok {
		return nil, errors.New("unknown fixture route")
	}
	pem, err := os.ReadFile(j.Fixture.CAFile)
	if err != nil {
		return nil, errors.New("cannot read fixture CA")
	}
	roots := x509.NewCertPool()
	if !roots.AppendCertsFromPEM(pem) {
		return nil, errors.New("invalid fixture CA")
	}
	keys := make(map[string]ed25519.PublicKey, len(j.Fixture.TrustedKeys))
	for id, raw := range j.Fixture.TrustedKeys {
		key, err := base64.StdEncoding.DecodeString(raw)
		if err != nil {
			return nil, errors.New("invalid fixture key")
		}
		keys[id] = ed25519.PublicKey(key)
	}
	bundleURL := j.Fixture.BundleURL
	if c.BundleURL != "" {
		bundleURL = c.BundleURL
	}
	return anchorpass.NewClient(
		anchorpass.WithProxy(proxy), anchorpass.WithRootCAs(roots),
		anchorpass.WithBundleURL(bundleURL), anchorpass.WithTrustedKeys(keys),
		anchorpass.WithCachePath(j.CachePath), anchorpass.WithOffline(c.Offline),
		anchorpass.WithTimeout(time.Duration(c.TimeoutMS)*time.Millisecond),
		anchorpass.WithMaxInFlight(c.MaxInFlight),
		anchorpass.WithRefreshInterval(time.Duration(c.RefreshInterval*float64(time.Second))),
	)
}

func batch(client *anchorpass.Client, j job, c command) ([]sample, error) {
	if client == nil || c.Count < 1 || c.Count > 128 || c.Concurrency < 1 || c.Concurrency > 8 {
		return nil, errors.New("invalid batch or closed client")
	}
	if c.Op != "load" && c.Op != "refresh" && c.Op != "solve" {
		return nil, errors.New("unknown operation")
	}
	samples := make([]sample, c.Count)
	indices := make(chan int, c.Count)
	for i := range samples {
		indices <- i
	}
	close(indices)
	var workers sync.WaitGroup
	for worker := 0; worker < c.Concurrency; worker++ {
		workers.Add(1)
		go func() {
			defer workers.Done()
			for i := range indices {
				started := time.Now()
				var value string
				var err error
				switch c.Op {
				case "load":
					value, err = client.LoadBG(context.Background())
				case "refresh":
					_, err = client.Refresh(context.Background())
				case "solve":
					var result anchorpass.SolveResult
					result, err = client.Solve(context.Background(), j.Fixture.AnchorURL)
					value = result.Token
				}
				samples[i] = sample{ElapsedNS: time.Since(started).Nanoseconds(), Value: value}
				if err != nil {
					var public *anchorpass.Error
					code := "Unclassified"
					if errors.As(err, &public) {
						code = string(public.Code)
					}
					samples[i].Error = &code
				}
			}
		}()
	}
	workers.Wait()
	return samples, nil
}

func run() error {
	if len(os.Args) != 2 {
		return errors.New("expected job path")
	}
	raw, err := os.ReadFile(os.Args[1])
	if err != nil || len(raw) > 1048576 {
		return errors.New("invalid job file")
	}
	var j job
	if err = json.Unmarshal(raw, &j); err != nil {
		return errors.New("invalid job JSON")
	}
	var client *anchorpass.Client
	defer func() {
		if client != nil {
			client.Close()
		}
	}()
	scanner := bufio.NewScanner(os.Stdin)
	scanner.Buffer(make([]byte, 4096), 131072)
	output := json.NewEncoder(os.Stdout)
	for scanner.Scan() {
		var c command
		if err = json.Unmarshal(scanner.Bytes(), &c); err != nil {
			return errors.New("invalid command JSON")
		}
		reply := map[string]any{"ok": true}
		switch c.Op {
		case "create":
			if client != nil {
				client.Close()
				client = nil
			}
			client, err = create(j, c)
		case "close":
			if client != nil {
				client.Close()
				client = nil
			}
		case "settle":
			if c.Milliseconds < 0 || c.Milliseconds > 5000 {
				return errors.New("invalid settle duration")
			}
			time.Sleep(time.Duration(c.Milliseconds) * time.Millisecond)
		default:
			var samples []sample
			samples, err = batch(client, j, c)
			reply["samples"] = samples
		}
		if err != nil {
			return err
		}
		if err = output.Encode(reply); err != nil {
			return err
		}
	}
	return scanner.Err()
}

func main() {
	if err := run(); err != nil {
		_ = json.NewEncoder(os.Stdout).Encode(map[string]any{"ok": false, "detail": err.Error()})
		fmt.Fprintln(os.Stderr, "pressure adapter failed")
		os.Exit(1)
	}
}
