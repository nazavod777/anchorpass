# 📊 Finite loader and pressure profiles

> **Independent pressure runner** — reuses `../fixture.py` for bounded TLS, authenticated CONNECT and SOCKS.

Only loopback sockets are used. The fake Google protocol host
and proxy-only `pressure.invalid` name map to that local TLS origin inside the
fixture. No public site is contacted.

## 🔧 Run

Build adapters before measuring (from repository root):

```sh
npm --prefix js run build
(cd benchmarks/pressure/go && go build -o pressure-go .)
cargo build --locked --release --manifest-path benchmarks/pressure/rust/Cargo.toml
.venv/bin/python -m unittest discover -s benchmarks -p 'test_*.py'
.venv/bin/python benchmarks/pressure/run.py --output /tmp/anchorpass-pressure-new
.venv/bin/python benchmarks/pressure/report.py /tmp/anchorpass-pressure-new/raw.json --exit-record /tmp/anchorpass-pressure-new/exit-record.json --output /tmp/anchorpass-pressure-new/summary.json
.venv/bin/python benchmarks/pressure/verify_expiry.py --output /tmp/anchorpass-expiry-new
```

Output must be a new directory. `plan.json` records the computed workload budget,
source hashes, command and environment before measurements.

## 📋 Defaults and bounds

- **Defaults:** three rotating rounds, four equal deadline/error batches, eight errors per batch, two active calls, eight offered overload calls, and at least ten seconds of solve soak per candidate per round.
- **Soak:** has an independent finite operation ceiling; exhausting it fails rather than shortening the minimum duration.
- **Bounds:** CLI bounds and the JSON-line adapter limits reject unbounded workloads.
- **Processes:** each child request has a finite response deadline and each owned process is reaped with its exit status.
- **Transcripts:** RPC transcripts retain commands and raw samples.
- **Builds:** must finish before this runner starts; avoid other heavy work during measurement.

## 🧪 Profiles

Profiles distinguish reused-client verified bg (including signed disk cache
checks), constructor-excluded cold signed disk cache selection, and forced signed
network refresh. Warm and offline cache loads
must generate no fixture request. Invalid and expired explicit updates must fail.
An invalid signature preserves the current valid snapshot for automatic loading.
A signed higher expired revision establishes a floor: automatic loading must fail
with `ConfigExpired`, then recover on the same client after a newer valid revision.
The fixture advances its signed revisions between candidates so every expired
update remains newer than the corresponding seed. All deadline batches
recover on the same client. A client with invalid proxy credentials must never
reach the directly reachable origin. Following a proxy configuration change,
recovery uses a new client, since public clients intentionally fix their proxy.
Overload offers a bounded simultaneous burst: exactly the configured capacity
must succeed and the rest must return `ResourceLimit` before those successes.
SOCKS DNS checks require the correct on-wire address kind and exercise both a
successful remote-only name and the corresponding failed local lookup.

## 📊 Resource observations

Resource observations sample Linux `/proc` every twenty milliseconds (bounded
storage), plus snapshots after equal batches and a configured settling interval,
including after client close while the process is alive. Peaks can miss spikes;
RSS includes allocators, caches and the language runtime. A finite flat series
does not prove the absence of leaks, and ten seconds is not a production longevity
test. Increase `--soak-seconds` within its bounded range for longer local evidence.

> [!NOTE]
> There is no transport ranking or claim of live CAPTCHA acceptance.

### 🔧 Node memory diagnosis

For Node memory diagnosis, ordinary `--soak-seconds 60 --candidates node` records
native heap/external-memory counters after equal batches without forcing GC.
`verify_node_memory.py NEW_OUTPUT_DIRECTORY` separately enables `--expose-gc` and
collects fixed equal solve batches before/after explicit diagnostic GC. That
control changes collection behavior and is not a production performance profile.

## ✅ Exit record and validation

The runner writes `exit-record.json`: successful records bind the canonical raw
JSON digest only after all rounds and fixture teardown complete; failed records
cannot be reported as success. The reporter revalidates stage counts, values,
errors, deadlines, proxy/DNS controls, overload and minimum soak duration.
Both runner and reporter reject credential leaks, blocked external targets,
protocol errors and shed connections, as well as malformed counter values.
The runner checks cumulative fixture state before and after each operation and
before fixture teardown, so failures between stages cannot disappear in a delta.
An omitted entry in the fixture's sparse counters means zero, including in the
historical raw archive. SOCKS policy controls require the expected address kind
and forbid the opposite local/remote kind.

## 🗂️ Historical raw data

Current plans record `expiry_semantics: monotonic-floor-v1`. The reporter requires
the matching floor and recovery stages; it does not accept historical fallback
outcomes under that marker. Plans without this marker are validated with the
historical workload rules and explicitly marked `historical-fallback` in
`coverage.expiry_semantics`, with `current_expiry_floor_qualified: false`.
Their immutable raw data and numerical summaries remain evidence of the measured
snapshot, not qualification of the current expiry-floor behavior. The focused
`verify_expiry.py` command exercises the current floor and recovery contract too.
