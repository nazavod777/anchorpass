# Local TLS/proxy transport matrix

Original evidence: `/tmp/anchorpass-matrix-verification`. Reproduce with `make bench-matrix BENCH_OUTPUT=/tmp/anchorpass-matrix-new`, then `benchmarks/report_matrix.py`. Full per-job results are retained in `raw.json.gz`; synthetic proxy credentials are omitted from that distributable copy.

Selective remeasurement: 9 affected jobs from `/tmp/anchorpass-matrix-go-api-final/raw.json` replace their baseline observations. Reason: Go custom certificate pools now isolate update state; API preparation uses a verified persistent offline cache. Only affected Go API profiles were remeasured. The selected combined observations, both runs' source hashes and replaced identities are preserved in raw.json.gz. Replaced original observations are not included in that archive; their recorded source is `/tmp/anchorpass-matrix-full-20261116/raw.json`. Unaffected jobs retain their original observations.

Recorded jobs: 618; executed: 588; unsupported: 30; invalid harness jobs: 0. Measured observations: 66624; failed: 144. Recovery observations: 1440; failed: 0.

Each performance cell combines independently launched processes across the configured rounds. p50/p95/p99 use successful samples only; failures remain separately visible and disqualify capability controls. Quantiles are descriptive. Round median ranges expose variation; there is no confidence-interval or universal-winner claim. Peak RSS includes startup, warmup and cleanup and is externally sampled, not an exact high-water mark.

## Capability outcomes

`passed` means the expected negative result and successful recovery. `unsupported` is unverified in this exact adapter, not a pass. These are candidate-adapter qualifications, distinct from package conformance tests.

| Candidate | total-deadline | bad-proxy-auth | separate-target-trust | separate-proxy-trust | decompressed-body-limit | rate-limit | connection-reset |
| --- | --- | --- | --- | --- | --- | --- | --- |
| go-api | missing | missing | missing | missing | missing | missing | missing |
| go-net-http | passed | passed | passed | passed | passed | passed | passed |
| go-resty | passed | passed | passed | passed | passed | passed | passed |
| node-api-async | missing | missing | missing | missing | missing | missing | missing |
| node-api-sync | missing | missing | missing | missing | missing | missing | missing |
| node-axios | passed | passed | unsupported | unsupported | passed | passed | passed |
| node-fetch | passed | passed | unsupported | unsupported | passed | passed | passed |
| node-undici | passed | passed | unsupported | unsupported | passed | passed | passed |
| python-aiohttp | passed | passed | passed | passed | passed | passed | passed |
| python-api-async | missing | missing | missing | missing | missing | missing | missing |
| python-api-sync | missing | missing | missing | missing | missing | missing | missing |
| python-curl-async | passed | passed | passed | passed | passed | passed | passed |
| python-curl-sync | passed | passed | passed | passed | passed | passed | passed |
| python-httpx-async | passed | passed | failed | passed | passed | passed | passed |
| python-httpx-sync | failed | passed | failed | passed | passed | passed | passed |
| python-requests | failed | passed | passed | passed | passed | passed | failed |
| rust-api-async | missing | missing | missing | missing | missing | missing | missing |
| rust-api-blocking | missing | missing | missing | missing | missing | missing | missing |
| rust-isahc | passed | unsupported | unsupported | unsupported | passed | passed | passed |
| rust-reqwest-async | passed | passed | unsupported | unsupported | passed | passed | passed |
| rust-reqwest-blocking | passed | passed | unsupported | unsupported | passed | passed | passed |
| rust-ureq | passed | passed | unsupported | unsupported | passed | passed | passed |

## api-cold-c1

| Candidate | Samples/failures | p50 / p95 / p99 ms | Round p50 range ms | ops/s | CPU ms/op | Sampled peak MiB |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| go-api | 384/0 | 1.525 / 1.845 / 2.051 | 1.487–1.577 | 633.4 | 0.944 | 15.89 |
| node-api-async | 384/0 | 3.626 / 4.393 / 5.042 | 3.618–3.639 | 271.4 | 3.207 | 115.10 |
| node-api-sync | 384/0 | 69.431 / 71.790 / 73.959 | 69.314–69.542 | 14.3 | 88.456 | 118.00 |
| python-api-async | 384/0 | 2.080 / 2.383 / 2.555 | 2.049–2.095 | 469.1 | 1.315 | 44.00 |
| python-api-sync | 384/0 | 1.943 / 2.222 / 2.254 | 1.926–2.076 | 509.9 | 1.067 | 43.72 |
| rust-api-async | 384/0 | 1.419 / 1.628 / 1.833 | 1.403–1.450 | 692.2 | 0.535 | 12.81 |
| rust-api-blocking | 384/0 | 1.502 / 1.692 / 1.970 | 1.488–1.507 | 640.9 | 0.606 | 12.71 |

## api-warm-c1

| Candidate | Samples/failures | p50 / p95 / p99 ms | Round p50 range ms | ops/s | CPU ms/op | Sampled peak MiB |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| go-api | 384/0 | 0.434 / 0.559 / 0.799 | 0.426–0.439 | 2190.7 | 0.312 | 15.26 |
| node-api-async | 384/0 | 0.945 / 1.242 / 1.453 | 0.933–0.969 | 1037.6 | 0.959 | 92.20 |
| node-api-sync | 384/0 | 1.120 / 1.341 / 1.477 | 1.068–1.208 | 899.9 | 1.119 | 113.62 |
| python-api-async | 384/0 | 1.049 / 1.181 / 1.251 | 0.971–1.130 | 939.4 | 0.829 | 43.71 |
| python-api-sync | 384/0 | 0.870 / 1.041 / 1.222 | 0.858–0.878 | 1110.2 | 0.655 | 43.73 |
| rust-api-async | 384/0 | 0.405 / 0.538 / 0.570 | 0.393–0.525 | 2430.9 | 0.186 | 12.69 |
| rust-api-blocking | 384/0 | 0.406 / 0.483 / 0.543 | 0.381–0.425 | 2378.1 | 0.194 | 12.64 |

## api-warm-c8

| Candidate | Samples/failures | p50 / p95 / p99 ms | Round p50 range ms | ops/s | CPU ms/op | Sampled peak MiB |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| go-api | 384/0 | 1.629 / 2.130 / 2.287 | 1.548–1.864 | 4662.5 | 0.388 | 16.18 |
| node-api-async | 384/0 | 4.876 / 6.071 / 6.511 | 4.836–4.957 | 1637.1 | 0.882 | 100.05 |
| python-api-async | 384/0 | 5.075 / 5.630 / 5.880 | 5.048–5.178 | 1535.9 | 0.650 | 44.36 |
| python-api-sync | 384/0 | 6.438 / 8.149 / 8.853 | 6.321–6.568 | 1235.2 | 1.657 | 45.05 |
| rust-api-async | 384/0 | 1.641 / 1.907 / 2.426 | 1.597–1.746 | 4828.7 | 0.187 | 13.14 |
| rust-api-blocking | 384/0 | 1.581 / 1.885 / 2.292 | 1.527–1.763 | 5075.0 | 0.224 | 14.67 |

## delayed-c8

| Candidate | Samples/failures | p50 / p95 / p99 ms | Round p50 range ms | ops/s | CPU ms/op | Sampled peak MiB |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| go-net-http | 384/0 | 20.863 / 21.018 / 21.074 | 20.809–20.909 | 382.7 | 0.067 | 14.22 |
| go-resty | 384/0 | 20.872 / 21.029 / 21.257 | 20.858–20.883 | 382.8 | 0.071 | 14.73 |
| node-axios | 384/0 | 24.151 / 25.097 / 25.326 | 23.829–24.348 | 329.0 | 0.714 | 99.23 |
| node-fetch | 384/0 | 22.513 / 23.762 / 24.219 | 22.286–22.652 | 352.1 | 0.366 | 88.91 |
| node-undici | 384/0 | 21.772 / 22.798 / 23.086 | 21.728–21.922 | 364.2 | 0.253 | 88.43 |
| python-aiohttp | 384/0 | 21.202 / 21.709 / 22.282 | 21.144–21.273 | 375.8 | 0.093 | 43.11 |
| python-curl-async | 384/0 | 21.164 / 21.868 / 22.034 | 20.729–21.633 | 380.1 | 0.155 | 36.76 |
| python-curl-sync | 384/0 | 22.152 / 22.859 / 23.236 | 22.028–22.249 | 359.9 | 0.443 | 38.04 |
| python-httpx-async | 384/0 | 21.231 / 23.082 / 23.610 | 21.184–21.274 | 371.8 | 0.360 | 37.02 |
| python-httpx-sync | 384/0 | 21.698 / 22.995 / 23.675 | 21.425–22.118 | 367.5 | 0.331 | 35.83 |
| python-requests | 384/0 | 21.071 / 22.181 / 22.953 | 21.036–21.117 | 374.4 | 0.356 | 37.73 |
| rust-isahc | 384/0 | 20.788 / 20.932 / 21.233 | 20.741–20.846 | 384.4 | 0.105 | 12.89 |
| rust-reqwest-async | 384/0 | 20.810 / 21.047 / 22.220 | 20.784–20.863 | 383.3 | 0.022 | 11.54 |
| rust-reqwest-blocking | 384/0 | 20.879 / 21.029 / 21.287 | 20.849–20.900 | 382.4 | 0.083 | 12.66 |
| rust-ureq | 384/0 | 20.814 / 20.981 / 21.224 | 20.758–20.866 | 383.7 | 0.039 | 15.37 |

## direct-cold-c1

| Candidate | Samples/failures | p50 / p95 / p99 ms | Round p50 range ms | ops/s | CPU ms/op | Sampled peak MiB |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| go-net-http | 384/0 | 0.791 / 1.046 / 1.253 | 0.782–0.800 | 1216.4 | 0.572 | 15.52 |
| go-resty | 384/0 | 0.786 / 0.987 / 1.237 | 0.782–0.793 | 1235.9 | 0.568 | 15.77 |
| node-axios | 384/0 | 1.757 / 2.035 / 2.409 | 1.737–1.808 | 559.8 | 1.718 | 102.65 |
| node-fetch | 384/0 | 1.593 / 1.763 / 2.148 | 1.574–1.601 | 617.0 | 1.397 | 99.16 |
| node-undici | 384/0 | 1.464 / 1.625 / 1.791 | 1.463–1.466 | 674.6 | 1.230 | 91.34 |
| python-aiohttp | 384/0 | 1.437 / 1.577 / 1.650 | 1.429–1.443 | 682.8 | 1.091 | 41.23 |
| python-curl-async | 384/0 | 1.320 / 1.484 / 1.590 | 1.311–1.324 | 740.7 | 1.004 | 36.95 |
| python-curl-sync | 384/0 | 1.181 / 1.337 / 1.485 | 1.169–1.307 | 835.8 | 0.814 | 37.19 |
| python-httpx-async | 384/0 | 1.669 / 2.117 / 2.447 | 1.666–1.679 | 577.5 | 1.421 | 38.42 |
| python-httpx-sync | 384/0 | 1.302 / 1.474 / 1.895 | 1.301–1.305 | 744.4 | 0.990 | 35.59 |
| python-requests | 384/0 | 1.547 / 1.699 / 1.779 | 1.541–1.556 | 635.8 | 1.205 | 38.16 |
| rust-isahc | 384/0 | 1.255 / 1.488 / 1.679 | 1.244–1.261 | 779.7 | 0.903 | 11.45 |
| rust-reqwest-async | 384/0 | 0.676 / 0.818 / 0.895 | 0.673–0.682 | 1439.3 | 0.274 | 11.55 |
| rust-reqwest-blocking | 384/0 | 0.917 / 1.126 / 1.301 | 0.904–0.950 | 1040.7 | 0.493 | 11.51 |
| rust-ureq | 384/0 | 0.687 / 0.781 / 0.885 | 0.682–0.690 | 1421.7 | 0.290 | 11.41 |

## direct-warm-c1

| Candidate | Samples/failures | p50 / p95 / p99 ms | Round p50 range ms | ops/s | CPU ms/op | Sampled peak MiB |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| go-net-http | 384/0 | 0.059 / 0.072 / 0.099 | 0.058–0.060 | 16459.7 | 0.053 | 11.31 |
| go-resty | 384/0 | 0.064 / 0.086 / 0.108 | 0.062–0.066 | 15317.3 | 0.058 | 11.41 |
| node-axios | 384/0 | 0.494 / 0.605 / 1.017 | 0.485–0.511 | 1977.2 | 0.799 | 94.68 |
| node-fetch | 384/0 | 0.293 / 0.374 / 0.800 | 0.291–0.295 | 3144.6 | 0.523 | 91.28 |
| node-undici | 384/0 | 0.184 / 0.260 / 0.883 | 0.180–0.198 | 4838.3 | 0.465 | 90.78 |
| python-aiohttp | 384/0 | 0.100 / 0.132 / 0.169 | 0.098–0.102 | 9405.9 | 0.082 | 40.70 |
| python-curl-async | 384/0 | 0.163 / 0.188 / 0.215 | 0.163–0.164 | 5918.3 | 0.152 | 36.91 |
| python-curl-sync | 384/0 | 0.136 / 0.173 / 0.199 | 0.131–0.142 | 7134.9 | 0.114 | 37.09 |
| python-httpx-async | 384/0 | 0.283 / 0.345 / 0.472 | 0.280–0.284 | 3405.1 | 0.290 | 36.58 |
| python-httpx-sync | 384/0 | 0.192 / 0.248 / 0.460 | 0.179–0.232 | 4704.6 | 0.196 | 34.50 |
| python-requests | 384/0 | 0.232 / 0.293 / 0.356 | 0.229–0.235 | 4136.6 | 0.225 | 36.23 |
| rust-isahc | 384/0 | 0.083 / 0.099 / 0.132 | 0.082–0.084 | 11686.8 | 0.071 | 11.23 |
| rust-reqwest-async | 384/0 | 0.113 / 0.535 / 0.574 | 0.050–0.151 | 5248.9 | 0.088 | 11.50 |
| rust-reqwest-blocking | 384/0 | 0.059 / 0.071 / 0.105 | 0.056–0.063 | 16526.2 | 0.041 | 11.49 |
| rust-ureq | 384/0 | 0.100 / 0.144 / 0.163 | 0.099–0.101 | 9535.6 | 0.084 | 11.10 |

## direct-warm-c32

| Candidate | Samples/failures | p50 / p95 / p99 ms | Round p50 range ms | ops/s | CPU ms/op | Sampled peak MiB |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| go-net-http | 384/0 | 0.913 / 1.509 / 1.965 | 0.862–0.961 | 29233.8 | 0.120 | 17.39 |
| go-resty | 384/0 | 0.995 / 1.588 / 2.201 | 0.868–1.076 | 28355.8 | 0.123 | 17.02 |
| node-axios | 384/0 | 13.838 / 14.741 / 14.899 | 13.630–14.045 | 2330.5 | 0.801 | 101.56 |
| node-fetch | 384/0 | 7.252 / 8.073 / 8.567 | 7.168–7.398 | 4282.0 | 0.450 | 93.99 |
| node-undici | 384/0 | 3.824 / 6.028 / 6.144 | 3.774–3.903 | 7517.8 | 0.326 | 94.14 |
| python-aiohttp | 384/0 | 1.681 / 1.789 / 1.934 | 1.657–1.693 | 18678.7 | 0.054 | 50.68 |
| python-curl-async | 384/0 | 2.537 / 4.345 / 4.753 | 2.346–3.040 | 10548.5 | 0.095 | 37.32 |
| python-curl-sync | 384/0 | 5.898 / 10.382 / 12.079 | 5.771–6.018 | 4498.2 | 0.474 | 40.89 |
| python-httpx-async | 384/0 | 27.199 / 86.240 / 119.670 | 26.726–27.692 | 822.0 | 1.216 | 39.45 |
| python-httpx-sync | 384/0 | 6.338 / 12.446 / 16.173 | 5.994–6.525 | 3962.7 | 0.315 | 40.91 |
| python-requests | 384/0 | 7.572 / 17.083 / 24.338 | 6.935–7.904 | 3049.2 | 0.391 | 43.24 |
| rust-isahc | 384/0 | 1.011 / 1.369 / 1.477 | 0.969–1.198 | 28560.4 | 0.094 | 18.86 |
| rust-reqwest-async | 384/0 | 1.259 / 1.956 / 2.052 | 1.239–1.318 | 22056.9 | 0.028 | 12.68 |
| rust-reqwest-blocking | 384/0 | 0.957 / 1.219 / 1.347 | 0.949–1.002 | 29075.3 | 0.070 | 17.80 |
| rust-ureq | 384/0 | 0.973 / 1.788 / 2.303 | 0.918–1.208 | 26878.5 | 0.166 | 30.30 |

## direct-warm-c8

| Candidate | Samples/failures | p50 / p95 / p99 ms | Round p50 range ms | ops/s | CPU ms/op | Sampled peak MiB |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| go-net-http | 384/0 | 0.231 / 0.272 / 0.316 | 0.228–0.235 | 33713.8 | 0.044 | 14.11 |
| go-resty | 384/0 | 0.229 / 0.259 / 0.272 | 0.227–0.231 | 33887.6 | 0.044 | 14.03 |
| node-axios | 384/0 | 3.316 / 4.281 / 4.682 | 3.285–3.324 | 2301.3 | 0.761 | 98.43 |
| node-fetch | 384/0 | 1.993 / 2.765 / 3.325 | 1.938–2.053 | 3854.8 | 0.454 | 90.71 |
| node-undici | 384/0 | 1.050 / 2.601 / 2.905 | 1.008–1.071 | 6450.5 | 0.397 | 90.86 |
| python-aiohttp | 384/0 | 0.467 / 0.539 / 0.600 | 0.461–0.483 | 16724.4 | 0.060 | 42.83 |
| python-curl-async | 384/0 | 0.934 / 1.203 / 1.451 | 0.922–0.958 | 8674.9 | 0.115 | 36.83 |
| python-curl-sync | 384/0 | 1.462 / 2.442 / 3.059 | 1.389–1.510 | 4949.5 | 0.443 | 37.96 |
| python-httpx-async | 384/0 | 2.172 / 2.502 / 5.293 | 2.165–2.193 | 3086.7 | 0.324 | 37.38 |
| python-httpx-sync | 384/0 | 1.503 / 3.196 / 4.010 | 1.489–1.532 | 4550.9 | 0.278 | 35.87 |
| python-requests | 384/0 | 1.847 / 3.442 / 4.115 | 1.813–1.912 | 3909.6 | 0.314 | 37.64 |
| rust-isahc | 384/0 | 0.271 / 0.347 / 0.405 | 0.256–0.293 | 28115.8 | 0.077 | 12.81 |
| rust-reqwest-async | 384/0 | 0.249 / 0.610 / 0.686 | 0.245–0.250 | 25375.3 | 0.023 | 11.86 |
| rust-reqwest-blocking | 384/0 | 0.250 / 0.351 / 0.390 | 0.246–0.300 | 30691.9 | 0.051 | 13.12 |
| rust-ureq | 384/0 | 0.278 / 0.369 / 0.517 | 0.247–0.282 | 27326.7 | 0.093 | 15.53 |

## http-proxy-c8

| Candidate | Samples/failures | p50 / p95 / p99 ms | Round p50 range ms | ops/s | CPU ms/op | Sampled peak MiB |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| go-net-http | 384/0 | 0.501 / 0.819 / 0.958 | 0.481–0.533 | 14616.1 | 0.056 | 13.61 |
| go-resty | 384/0 | 0.473 / 0.586 / 0.880 | 0.472–0.475 | 16151.8 | 0.047 | 13.05 |
| node-axios | 384/0 | 3.423 / 4.253 / 4.908 | 3.242–3.495 | 2261.4 | 0.772 | 97.21 |
| node-fetch | 384/0 | 1.861 / 2.367 / 2.578 | 1.819–1.893 | 4228.1 | 0.456 | 93.11 |
| node-undici | 384/0 | 0.955 / 2.448 / 2.833 | 0.933–0.981 | 7108.4 | 0.371 | 90.19 |
| python-aiohttp | 384/0 | 0.485 / 0.640 / 0.711 | 0.484–0.487 | 15114.0 | 0.064 | 43.04 |
| python-curl-async | 384/0 | 0.929 / 1.372 / 1.615 | 0.910–1.336 | 8089.1 | 0.123 | 36.80 |
| python-curl-sync | 384/0 | 1.543 / 2.476 / 2.773 | 1.429–1.809 | 5149.0 | 0.423 | 37.96 |
| python-httpx-async | 384/0 | 2.092 / 2.639 / 45.063 | 2.052–2.153 | 2743.3 | 0.364 | 37.08 |
| python-httpx-sync | 384/0 | 1.703 / 3.746 / 4.515 | 1.529–1.900 | 3769.3 | 0.319 | 35.81 |
| python-requests | 384/0 | 2.092 / 4.570 / 6.272 | 1.883–2.219 | 3107.2 | 0.374 | 37.52 |
| rust-isahc | 384/0 | 0.503 / 0.681 / 0.893 | 0.481–0.586 | 14855.3 | 0.073 | 12.96 |
| rust-reqwest-async | 384/0 | 0.465 / 0.576 / 0.912 | 0.464–0.470 | 16561.1 | 0.016 | 11.35 |
| rust-reqwest-blocking | 384/0 | 0.486 / 0.621 / 0.856 | 0.476–0.507 | 16060.8 | 0.048 | 12.66 |
| rust-ureq | 384/0 | 0.470 / 0.575 / 0.657 | 0.460–0.481 | 16266.3 | 0.020 | 15.25 |

## https-proxy-c8

| Candidate | Samples/failures | p50 / p95 / p99 ms | Round p50 range ms | ops/s | CPU ms/op | Sampled peak MiB |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| go-net-http | 384/0 | 0.539 / 0.643 / 0.955 | 0.518–0.561 | 14256.3 | 0.060 | 15.10 |
| go-resty | 384/0 | 0.519 / 0.711 / 2.647 | 0.501–0.539 | 15101.5 | 0.060 | 15.48 |
| node-axios | 384/0 | 3.516 / 4.311 / 4.538 | 3.329–3.608 | 2192.5 | 0.783 | 100.67 |
| node-fetch | 384/0 | 1.984 / 2.633 / 2.841 | 1.964–2.024 | 3858.9 | 0.487 | 91.05 |
| node-undici | 384/0 | 0.992 / 1.531 / 1.642 | 0.975–1.014 | 7564.2 | 0.281 | 90.31 |
| python-aiohttp | 384/0 | 0.564 / 0.714 / 0.845 | 0.531–0.602 | 13547.4 | 0.071 | 45.91 |
| python-curl-async | 384/0 | 0.921 / 1.040 / 1.135 | 0.916–0.929 | 8580.5 | 0.116 | 36.88 |
| python-curl-sync | 384/0 | 1.404 / 2.289 / 2.633 | 1.293–1.491 | 5333.8 | 0.404 | 38.04 |
| python-httpx-async | 384/0 | 2.130 / 2.524 / 44.977 | 2.121–2.154 | 2739.1 | 0.365 | 37.57 |
| python-httpx-sync | 384/0 | 1.529 / 3.389 / 4.204 | 1.437–1.745 | 4113.1 | 0.307 | 36.54 |
| python-requests | 384/0 | 1.850 / 4.312 / 5.782 | 1.759–2.056 | 3560.1 | 0.341 | 37.93 |

## large-c8

| Candidate | Samples/failures | p50 / p95 / p99 ms | Round p50 range ms | ops/s | CPU ms/op | Sampled peak MiB |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| go-net-http | 384/0 | 0.883 / 1.167 / 1.362 | 0.818–0.930 | 8413.8 | 0.310 | 16.18 |
| go-resty | 384/0 | 0.950 / 1.313 / 1.720 | 0.896–0.981 | 8072.4 | 0.341 | 16.65 |
| node-axios | 384/0 | 6.996 / 8.581 / 9.218 | 6.729–7.167 | 1153.6 | 1.140 | 103.16 |
| node-fetch | 384/0 | 5.244 / 6.369 / 6.538 | 5.160–5.365 | 1527.7 | 0.848 | 98.52 |
| node-undici | 384/0 | 3.997 / 5.085 / 6.103 | 3.957–4.064 | 1949.6 | 0.794 | 98.53 |
| python-aiohttp | 384/0 | 0.816 / 0.920 / 1.014 | 0.806–0.822 | 9508.3 | 0.103 | 43.70 |
| python-curl-async | 384/0 | 1.115 / 1.458 / 1.841 | 1.103–1.123 | 6874.6 | 0.143 | 36.82 |
| python-curl-sync | 384/0 | 1.644 / 2.421 / 2.880 | 1.636–1.657 | 4547.8 | 0.477 | 38.55 |
| python-httpx-async | 384/0 | 2.761 / 4.100 / 12.070 | 2.730–2.795 | 2594.9 | 0.385 | 37.98 |
| python-httpx-sync | 384/0 | 2.000 / 3.371 / 4.358 | 1.936–2.048 | 3584.2 | 0.379 | 36.30 |
| python-requests | 384/0 | 2.464 / 3.870 / 4.850 | 2.228–2.653 | 3203.6 | 0.401 | 37.79 |
| rust-isahc | 384/0 | 0.874 / 1.219 / 1.405 | 0.820–0.890 | 8586.0 | 0.140 | 14.34 |
| rust-reqwest-async | 384/0 | 0.852 / 2.993 / 3.684 | 0.804–0.921 | 5770.9 | 0.087 | 13.62 |
| rust-reqwest-blocking | 384/0 | 0.846 / 0.967 / 1.226 | 0.806–0.858 | 8971.8 | 0.123 | 15.32 |
| rust-ureq | 384/0 | 0.807 / 0.927 / 1.556 | 0.772–0.822 | 9167.3 | 0.075 | 16.07 |

## socks5-local-c8

| Candidate | Samples/failures | p50 / p95 / p99 ms | Round p50 range ms | ops/s | CPU ms/op | Sampled peak MiB |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| go-net-http | 384/0 | 0.496 / 0.624 / 0.669 | 0.472–0.561 | 16112.9 | 0.045 | 13.61 |
| go-resty | 384/0 | 0.485 / 0.674 / 0.922 | 0.477–0.510 | 15786.2 | 0.062 | 13.72 |
| node-axios | 384/0 | 3.393 / 4.083 / 4.250 | 3.225–3.481 | 2354.6 | 0.761 | 95.80 |
| node-fetch | 384/0 | 1.911 / 2.628 / 2.923 | 1.855–2.082 | 4085.4 | 0.460 | 91.32 |
| node-undici | 384/0 | 0.953 / 2.129 / 2.984 | 0.925–1.010 | 7101.7 | 0.365 | 91.20 |
| python-aiohttp | 384/0 | 0.500 / 0.714 / 0.936 | 0.487–0.511 | 14977.8 | 0.065 | 43.43 |
| python-curl-async | 384/0 | 0.937 / 1.116 / 1.263 | 0.916–0.944 | 8285.3 | 0.121 | 36.85 |
| python-curl-sync | 384/0 | 1.459 / 2.207 / 2.513 | 1.432–1.473 | 5217.7 | 0.416 | 37.96 |
| python-httpx-async | 384/0 | 2.109 / 2.831 / 7.539 | 2.072–2.150 | 3410.7 | 0.293 | 37.77 |
| python-httpx-sync | 384/0 | 1.473 / 2.497 / 3.320 | 1.376–1.523 | 4801.4 | 0.261 | 36.46 |
| python-requests | 384/0 | 1.803 / 3.665 / 4.156 | 1.772–1.876 | 3672.1 | 0.325 | 37.70 |
| rust-isahc | 384/0 | 0.519 / 0.585 / 0.629 | 0.510–0.535 | 15019.4 | 0.073 | 12.90 |
| rust-reqwest-async | 384/0 | 0.472 / 0.689 / 0.783 | 0.458–0.514 | 16166.8 | 0.018 | 11.70 |
| rust-reqwest-blocking | 384/0 | 0.524 / 0.639 / 0.861 | 0.513–0.531 | 14583.7 | 0.048 | 12.91 |
| rust-ureq | 384/0 | 0.521 / 0.673 / 0.744 | 0.507–0.533 | 14624.3 | 0.089 | 15.53 |

## socks5h-remote-c8

| Candidate | Samples/failures | p50 / p95 / p99 ms | Round p50 range ms | ops/s | CPU ms/op | Sampled peak MiB |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| go-net-http | 384/0 | 0.482 / 0.715 / 0.990 | 0.467–0.509 | 15110.4 | 0.049 | 13.34 |
| go-resty | 384/0 | 0.478 / 0.678 / 1.003 | 0.472–0.498 | 16351.1 | 0.060 | 12.85 |
| node-axios | 384/0 | 3.318 / 4.195 / 4.699 | 3.212–3.402 | 2346.4 | 0.754 | 96.82 |
| node-fetch | 384/0 | 1.913 / 2.523 / 3.436 | 1.878–1.920 | 4149.1 | 0.454 | 93.92 |
| node-undici | 384/0 | 0.977 / 2.386 / 2.701 | 0.970–0.984 | 6815.9 | 0.378 | 90.18 |
| python-aiohttp | 384/0 | 0.493 / 0.726 / 0.805 | 0.479–0.500 | 14927.0 | 0.066 | 43.06 |
| python-curl-async | 384/0 | 0.947 / 1.168 / 1.278 | 0.928–0.962 | 8271.4 | 0.121 | 36.85 |
| python-curl-sync | 384/0 | 1.418 / 2.307 / 2.869 | 1.326–1.458 | 5245.0 | 0.409 | 37.96 |
| python-httpx-async | 384/0 | 2.084 / 3.469 / 7.727 | 2.058–2.138 | 3400.1 | 0.294 | 37.46 |
| python-httpx-sync | 384/0 | 1.859 / 3.301 / 3.996 | 1.760–1.955 | 4019.5 | 0.304 | 36.43 |
| python-requests | 384/0 | 1.900 / 4.173 / 5.256 | 1.846–1.972 | 3546.9 | 0.335 | 37.66 |
| rust-isahc | 384/0 | 0.512 / 0.613 / 0.889 | 0.488–0.534 | 14808.0 | 0.076 | 12.89 |
| rust-reqwest-async | 384/0 | 0.466 / 0.513 / 0.551 | 0.463–0.471 | 17002.2 | 0.016 | 11.44 |
| rust-reqwest-blocking | 384/0 | 0.488 / 0.536 / 0.576 | 0.475–0.509 | 16252.1 | 0.041 | 12.61 |
| rust-ureq | 384/0 | 0.458 / 0.501 / 0.553 | 0.455–0.465 | 17399.2 | 0.018 | 15.33 |

## Interpretation limits

- All traffic is loopback and uses verified TLS. The fixture blocks external forwarding. No public service was load-tested.
- Node frontends share an Undici connector; Axios uses fetch. Resty shares net/http. These compare wrapper costs over shared backends.
- Sync transports use bounded workers; async uses native tasks. Node blocking concurrency one only. Runtime/framework costs and thread topology are part of those measured configurations.
- Warm means a reused client after warmup; cold includes construction and cleanup. Update preparation is outside solve timing. Package API settings and cache strategies are documented in each adapter README.
- The raw Rust gzip feature is isolated from its API binary. The measured snapshot predates independent Rust target/proxy trust; its recorded adapter limitations remain historical evidence. The current package uses independent policies, with subsequent qualification recorded in [transport decisions](../../../docs/transport-decisions.md). These timings were not remeasured for that change.
- Python requests/HTTPX sync idle timeouts can fail the total deadline. HTTPX may retain a failed nested-TLS connection slot; the repeat-failure control records the actual result. Aiohttp's no-retry adapter uses a version-pinned internal switch.
- Go operation timeout returns promptly; a detached net/http handshake can live until its bounded connect timeout or Client.Close. This is separately tested and documented.
- Fixture CPU fraction and loop lag are included in summary.json. High fixture load or overlapping round medians prevent attributing small differences solely to a client.
- These finite batches do not prove long-term leak freedom, live acceptance, platform portability or update-server resilience. Package conformance supplies separate evidence.

## Unsupported adapter explanations

- `node-api-sync/api-warm-c8`: SyncClient is blocking; this adapter supports concurrency 1 only, without adding an outer worker implementation
- `node-axios/separate-proxy-trust`: Shared production connector erases underlying TLS exception codes; this adapter cannot prove the expected tls error category
- `node-axios/separate-target-trust`: Shared production connector erases underlying TLS exception codes; this adapter cannot prove the expected tls error category
- `node-fetch/separate-proxy-trust`: Shared production connector erases underlying TLS exception codes; this adapter cannot prove the expected tls error category
- `node-fetch/separate-target-trust`: Shared production connector erases underlying TLS exception codes; this adapter cannot prove the expected tls error category
- `node-undici/separate-proxy-trust`: Shared production connector erases underlying TLS exception codes; this adapter cannot prove the expected tls error category
- `node-undici/separate-target-trust`: Shared production connector erases underlying TLS exception codes; this adapter cannot prove the expected tls error category
- `rust-isahc/bad-proxy-auth`: isahc 1.8.3 exposes CONNECT auth rejection as generic ConnectionFailed/curl code 7 in this adapter; it cannot establish the required proxy error category without guessing.
- `rust-isahc/https-proxy-c8`: This adapter does not expose independent target/proxy CA stores; nested TLS routes are not benchmarked with union trust.
- `rust-isahc/separate-proxy-trust`: This adapter does not expose independent target/proxy CA stores; nested TLS routes are not benchmarked with union trust.
- `rust-isahc/separate-target-trust`: This adapter does not expose independent target/proxy CA stores; nested TLS routes are not benchmarked with union trust.
- `rust-reqwest-async/https-proxy-c8`: This adapter does not expose independent target/proxy CA stores; nested TLS routes are not benchmarked with union trust.
- `rust-reqwest-async/separate-proxy-trust`: This adapter does not expose independent target/proxy CA stores; nested TLS routes are not benchmarked with union trust.
- `rust-reqwest-async/separate-target-trust`: This adapter does not expose independent target/proxy CA stores; nested TLS routes are not benchmarked with union trust.
- `rust-reqwest-blocking/https-proxy-c8`: This adapter does not expose independent target/proxy CA stores; nested TLS routes are not benchmarked with union trust.
- `rust-reqwest-blocking/separate-proxy-trust`: This adapter does not expose independent target/proxy CA stores; nested TLS routes are not benchmarked with union trust.
- `rust-reqwest-blocking/separate-target-trust`: This adapter does not expose independent target/proxy CA stores; nested TLS routes are not benchmarked with union trust.
- `rust-ureq/https-proxy-c8`: This adapter does not expose independent target/proxy CA stores; nested TLS routes are not benchmarked with union trust.
- `rust-ureq/separate-proxy-trust`: This adapter does not expose independent target/proxy CA stores; nested TLS routes are not benchmarked with union trust.
- `rust-ureq/separate-target-trust`: This adapter does not expose independent target/proxy CA stores; nested TLS routes are not benchmarked with union trust.

## Environment

```json
{
  "platform": "Linux-6.8.0-147-generic-x86_64-with-glibc2.39",
  "machine": "x86_64",
  "processor": "x86_64",
  "python": "3.13.15",
  "cpu_count": 32,
  "affinity_cpus": [
    0,
    1,
    2,
    3,
    4,
    5,
    6,
    7,
    8,
    9,
    10,
    11,
    12,
    13,
    14,
    15,
    16,
    17,
    18,
    19,
    20,
    21,
    22,
    23,
    24,
    25,
    26,
    27,
    28,
    29,
    30,
    31
  ],
  "cpu_model": "AMD Ryzen 9 7950X 16-Core Processor",
  "cpu_max": null,
  "memory_max": null,
  "nofile": [
    65535,
    65535
  ],
  "fixture_connections_limit": 512,
  "fixture_io_timeout_seconds": 5
}
```
