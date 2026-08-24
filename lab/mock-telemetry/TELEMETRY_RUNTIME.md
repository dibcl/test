# Generic telemetry runtime

This runtime keeps data collection, scheduling, and transport independent.
It is intentionally generic and does not encode or replay any proprietary
management-plane protocol.

## Providers

- `FrozenProfileProvider`: backward-compatible profile playback.
- `SyntheticMetricsProvider`: dynamic metrics generated from `dynamics` in an existing profile.
- `LiveSystemProvider`: live CPU, memory, disk, network I/O, and bounded process snapshots via `psutil`.

## Clocks

- `RealClock`: wall-clock execution.
- `SimulatedClock`: deterministic fast-forward execution for tests.

## Transports

- `MemoryTransport`: in-process tests.
- `FileDumpTransport`: NDJSON file output.
- `TcpTransport`: generic NDJSON-over-TCP for loopback/LAN tests.
- `UdpTransport`: one JSON object per datagram.

TCP/UDP destinations are restricted to loopback/private/link-local addresses by default. Public destinations require an explicit `allow_public` configuration opt-in.

## Backward compatibility

Existing configuration of the form:

```json
{
  "profile": "lab/mock-telemetry/baseline.synthetic.json",
  "transport": {"type": "memory"}
}
```

continues to load as a `FrozenProfileProvider`. Existing `loopback_tcp` transport names remain accepted.

The legacy `identity.test_mode` field may remain inside existing fixtures, but the generic runtime does not use it as a startup gate.

## Examples

Install the optional live-system dependency:

```bash
python -m pip install -r lab/mock-telemetry/requirements.txt
```

From the repository root, run a live file dump:

```bash
python lab/mock-telemetry/run_agent.py --config lab/mock-telemetry/config.live.file.example.json
```

Run deterministic synthetic telemetry:

```bash
python lab/mock-telemetry/run_agent.py --config lab/mock-telemetry/config.synthetic.file.example.json
```

Run unit tests from the runtime directory:

```bash
cd lab/mock-telemetry
python -m unittest discover -s tests -v
```

## Extension contract

New providers implement `BaseMetricsProvider.snapshot()` and return `TelemetrySnapshot`.
New transports implement `BaseTransport.send()` and consume the same generic envelope.
Neither extension requires changes to `TelemetryAgent`.
