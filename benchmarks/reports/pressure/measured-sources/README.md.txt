# Finite loader and pressure profiles

This independent runner reuses `../fixture.py` for bounded TLS, authenticated
CONNECT and SOCKS. Only loopback sockets are used. The fake Google protocol host
and proxy-only `pressure.invalid` name map to that local TLS origin inside the
fixture. No public site is contacted.

Build adapters before measuring (from repository root):

```sh
npm --prefix js run build
(cd benchmarks/pressure/go && go build -o pressure-go .)
cargo build --locked --release --manifest-path benchmarks/pressure/rust/Cargo.toml
.venv/bin/python -m unittest discover -s benchmarks -p 'test_*.py'
.venv/bin/python benchmarks/pressure/run.py --output /tmp/anchorpass-pressure-new
.venv/bin/python benchmarks/pressure/report.py /tmp/anchorpass-pressure-new/raw.json --output /tmp/anchorpass-pressure-new/summary.json
```

Output must be a new directory. `plan.json` records the computed workload budget,
source hashes, command and environment before measurements. Defaults are three
rotating rounds, four equal deadline/error batches, eight errors per batch,
two active calls, eight offered overload calls, and at least ten seconds of solve
soak per candidate per round. Soak has an independent finite operation ceiling;
exhausting it fails rather than shortening the minimum duration. CLI bounds and
the JSON-line adapter limits reject unbounded workloads. Each child request has
a finite response deadline and each owned process is reaped with its exit status.
RPC transcripts retain commands and raw samples. Builds must finish before this
runner starts; avoid other heavy work during measurement.

Profiles distinguish verified in-memory bg, constructor-excluded cold signed disk
cache selection, and forced signed network refresh. Warm and offline cache loads
must generate no fixture request. Invalid and expired explicit updates must fail;
automatic loading must retain an unexpired verified snapshot. All deadline batches
recover on the same client. A client with invalid proxy credentials must never
reach the directly reachable origin. Following a proxy configuration change,
recovery uses a new client, since public clients intentionally fix their proxy.
Overload offers a bounded simultaneous burst: exactly the configured capacity
must succeed and the rest must return `ResourceLimit` before those successes.
SOCKS DNS checks require the correct on-wire address kind and exercise both a
successful remote-only name and the corresponding failed local lookup.

Resource observations sample Linux `/proc` every twenty milliseconds (bounded
storage), plus snapshots after equal batches and a configured settling interval,
including after client close while the process is alive. Peaks can miss spikes;
RSS includes allocators, caches and the language runtime. A finite flat series
does not prove the absence of leaks, and ten seconds is not a production longevity
test. Increase `--soak-seconds` within its bounded range for longer local evidence.
There is no transport ranking or claim of live CAPTCHA acceptance.
