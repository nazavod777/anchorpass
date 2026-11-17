// Command benchmarks measures a small local HTTP fixture. It is not a claim of
// production or proxy performance. Resty is isolated from the library module.
package main

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"runtime"
	"sort"
	"strings"
	"sync"
	"syscall"
	"time"

	"github.com/go-resty/resty/v2"
)

type sample struct {
	Candidate   string `json:"candidate"`
	Profile     string `json:"profile"`
	Concurrency int    `json:"concurrency"`
	Round       int    `json:"round"`
	Nanoseconds int64  `json:"nanoseconds"`
	Error       string `json:"error,omitempty"`
}
type summary struct {
	Candidate   string  `json:"candidate"`
	Profile     string  `json:"profile"`
	Concurrency int     `json:"concurrency"`
	Round       int     `json:"round"`
	Operations  int     `json:"operations"`
	Errors      int     `json:"errors"`
	P50NS       int64   `json:"p50_ns"`
	P95NS       int64   `json:"p95_ns"`
	P99NS       int64   `json:"p99_ns"`
	Throughput  float64 `json:"operations_per_second"`
	CPUSeconds  float64 `json:"cpu_seconds"`
	HeapBytes   uint64  `json:"heap_bytes"`
	Goroutines  int     `json:"goroutines"`
	FDs         int     `json:"file_descriptors"`
}
type requester struct {
	http      *http.Client
	resty     *resty.Client
	transport *http.Transport
}

func newRequester(candidate string, concurrency int) *requester {
	tr := &http.Transport{Proxy: nil, MaxConnsPerHost: concurrency, MaxIdleConns: concurrency, MaxIdleConnsPerHost: concurrency, ResponseHeaderTimeout: time.Second}
	client := &http.Client{Transport: tr, CheckRedirect: func(_ *http.Request, _ []*http.Request) error { return http.ErrUseLastResponse }}
	r := &requester{http: client, transport: tr}
	if candidate == "resty" {
		r.resty = resty.NewWithClient(client).SetRetryCount(0).SetRedirectPolicy(resty.NoRedirectPolicy()).SetHeader("User-Agent", "anchorpass-benchmark")
	}
	return r
}
func (r *requester) close() { r.transport.CloseIdleConnections() }
func (r *requester) get(address string) error {
	ctx, cancel := context.WithTimeout(context.Background(), time.Second)
	defer cancel()
	var body io.ReadCloser
	if r.resty != nil {
		response, e := r.resty.R().SetContext(ctx).SetDoNotParseResponse(true).Get(address)
		if e != nil {
			return e
		}
		if response.StatusCode() != 200 {
			response.RawBody().Close()
			return fmt.Errorf("status %d", response.StatusCode())
		}
		body = response.RawBody()
	} else {
		request, e := http.NewRequestWithContext(ctx, "GET", address, nil)
		if e != nil {
			return e
		}
		request.Header.Set("User-Agent", "anchorpass-benchmark")
		response, e := r.http.Do(request)
		if e != nil {
			return e
		}
		if response.StatusCode != 200 {
			response.Body.Close()
			return fmt.Errorf("status %d", response.StatusCode)
		}
		body = response.Body
	}
	defer body.Close()
	raw, e := io.ReadAll(io.LimitReader(body, 1048577))
	if e != nil {
		return e
	}
	if len(raw) != 1024 {
		return fmt.Errorf("incorrect response size %d", len(raw))
	}
	return nil
}
func cpuTime() float64 {
	var usage syscall.Rusage
	if e := syscall.Getrusage(syscall.RUSAGE_SELF, &usage); e != nil {
		panic(e)
	}
	return float64(usage.Utime.Sec+usage.Stime.Sec) + float64(usage.Utime.Usec+usage.Stime.Usec)/1e6
}
func run(candidate, profile, address string, concurrency, round, operations int) ([]sample, summary) {
	requester := newRequester(candidate, concurrency)
	defer requester.close()
	for i := 0; i < 20; i++ {
		if e := requester.get(address); e != nil {
			panic(e)
		}
	}
	samples := make([]sample, operations)
	jobs := make(chan int)
	var workers sync.WaitGroup
	beforeCPU := cpuTime()
	start := time.Now()
	for i := 0; i < concurrency; i++ {
		workers.Add(1)
		go func() {
			defer workers.Done()
			for index := range jobs {
				client := requester
				if profile == "cold" {
					client = newRequester(candidate, concurrency)
				}
				started := time.Now()
				e := client.get(address)
				s := sample{Candidate: candidate, Profile: profile, Concurrency: concurrency, Round: round, Nanoseconds: time.Since(started).Nanoseconds()}
				if e != nil {
					s.Error = e.Error()
				}
				samples[index] = s
				if profile == "cold" {
					client.close()
				}
			}
		}()
	}
	for i := 0; i < operations; i++ {
		jobs <- i
	}
	close(jobs)
	workers.Wait()
	elapsed := time.Since(start)
	cpu := cpuTime() - beforeCPU
	durations := make([]int64, 0, operations)
	errors := 0
	for _, s := range samples {
		if s.Error != "" {
			errors++
		} else {
			durations = append(durations, s.Nanoseconds)
		}
	}
	sort.Slice(durations, func(i, j int) bool { return durations[i] < durations[j] })
	percentile := func(p int) int64 {
		if len(durations) == 0 {
			return 0
		}
		return durations[(len(durations)-1)*p/100]
	}
	var memory runtime.MemStats
	runtime.ReadMemStats(&memory)
	fds, e := os.ReadDir("/proc/self/fd")
	if e != nil {
		panic(e)
	}
	result := summary{Candidate: candidate, Profile: profile, Concurrency: concurrency, Round: round, Operations: operations, Errors: errors, P50NS: percentile(50), P95NS: percentile(95), P99NS: percentile(99), Throughput: float64(operations-errors) / elapsed.Seconds(), CPUSeconds: cpu, HeapBytes: memory.HeapAlloc, Goroutines: runtime.NumGoroutine(), FDs: len(fds)}
	return samples, result
}
func writeJSON(path string, value any) {
	raw, e := json.MarshalIndent(value, "", "  ")
	if e != nil {
		panic(e)
	}
	if e = os.WriteFile(path, append(raw, '\n'), 0644); e != nil {
		panic(e)
	}
}

func resourceMetadata(metadata map[string]any) {
	var limits syscall.Rlimit
	if e := syscall.Getrlimit(syscall.RLIMIT_NOFILE, &limits); e == nil {
		metadata["open_files_limit"] = []uint64{limits.Cur, limits.Max}
	}
	raw, e := os.ReadFile("/proc/self/cgroup")
	if e != nil {
		return
	}
	path := ""
	for _, line := range strings.Split(string(raw), "\n") {
		if strings.HasPrefix(line, "0::") {
			path = strings.TrimPrefix(line, "0::")
			break
		}
	}
	if path == "" {
		return
	}
	ancestors := []map[string]string{}
	for directory := filepath.Join("/sys/fs/cgroup", path); directory != "/sys/fs" && directory != "/"; directory = filepath.Dir(directory) {
		observed := map[string]string{"path": directory}
		for _, name := range []string{"cpu.max", "memory.max", "pids.max"} {
			value, e := os.ReadFile(filepath.Join(directory, name))
			if e == nil {
				observed[name] = strings.TrimSpace(string(value))
			}
		}
		if len(observed) > 1 {
			ancestors = append(ancestors, observed)
		}
	}
	metadata["cgroup_ancestors"] = ancestors
}

func main() {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/octet-stream")
		_, _ = io.WriteString(w, strings.Repeat("x", 1024))
	}))
	defer server.Close()
	if e := os.MkdirAll("reports", 0755); e != nil {
		panic(e)
	}
	all := []sample{}
	summaries := []summary{}
	for round := 0; round < 3; round++ {
		candidates := []string{"net/http", "resty"}
		if round%2 == 1 {
			candidates[0], candidates[1] = candidates[1], candidates[0]
		}
		for _, concurrency := range []int{1, 8, 32} {
			for _, profile := range []string{"warm", "cold"} {
				for _, candidate := range candidates {
					samples, result := run(candidate, profile, server.URL, concurrency, round, 200)
					all = append(all, samples...)
					summaries = append(summaries, result)
					fmt.Printf("%s %s c=%d round=%d n=%d errors=%d p50_ns=%d p95_ns=%d p99_ns=%d ops_s=%.2f\n", candidate, profile, concurrency, round, result.Operations, result.Errors, result.P50NS, result.P95NS, result.P99NS, result.Throughput)
				}
			}
		}
	}
	metadata := map[string]any{"go": runtime.Version(), "os": runtime.GOOS, "arch": runtime.GOARCH, "logical_cpus": runtime.NumCPU(), "gomaxprocs": runtime.GOMAXPROCS(0), "resty": "v2.16.5", "sample_count": len(all), "response_bytes": 1024, "tls": "none (HTTP fixture)", "measured_at": time.Now().UTC().Format(time.RFC3339)}
	for name, path := range map[string]string{"cpu_max": "/sys/fs/cgroup/cpu.max", "memory_max": "/sys/fs/cgroup/memory.max", "pids_max": "/sys/fs/cgroup/pids.max"} {
		raw, e := os.ReadFile(path)
		if e == nil {
			metadata[name] = strings.TrimSpace(string(raw))
		}
	}
	resourceMetadata(metadata)
	writeJSON("reports/samples.json", all)
	writeJSON("reports/summary.json", summaries)
	writeJSON("reports/environment.json", metadata)
	fmt.Printf("samples=%d profiles=%d response_bytes=1024\n", len(all), len(summaries))
	for _, result := range summaries {
		if result.Errors != 0 {
			os.Exit(1)
		}
	}
}
