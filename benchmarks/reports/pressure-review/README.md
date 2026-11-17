# Current local pressure qualification

This separate audit run exercises the current `monotonic-floor-v1` expiry contract
across Python, Node, Go and Rust. It uses one round, two equal error batches and a
minimum five-second solve soak per candidate. It is a bounded local correctness
check, not a transport ranking or a production longevity measurement.

`raw.json.gz` decompresses to the exact runner JSON. `exit-record.json` binds its
canonical JSON digest after fixture teardown; `runner-exit.json` retains the
original outer process status. `summary.json` includes the current expiry coverage.
`expiry.json.gz` separately records the focused expired-higher-revision rejection
and newer-valid-revision recovery checks, including exact seed and stage samples;
`expiry-exit.json` retains its original process status.

`applicability.json` records source applicability. The main runner and its runtime
inputs are unchanged since that run; only the separately run expiry helper and
its tests changed afterwards to reject incomplete adapter results. The historical
files in `../pressure/` are unchanged and keep their original workload semantics.
