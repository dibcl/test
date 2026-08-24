# Generic telemetry runtime

This runtime keeps data collection, scheduling, and transport independent.
It is intentionally generic and does not encode or replay any proprietary
management-plane protocol.

## Architecture

```text
BaseMetricsProvider
  |- FrozenProfileProvider
  |- SyntheticMetricsProvider
  `- LiveSystemProvider
            |
            v
   TelemetrySnapshot
            |
            v
     TelemetryAgent <--- BaseClock
            |
            v
      BaseTransport
  |- MemoryTransport
  |- FileDumpTransport
  |- TcpTransport
  `- UdpTransport
```

`TelemetryAgent` depends only on the provider, clock, and transport contracts.
Provider-specific collection logic and transport-specific I/O therefore evolve
independently.

## Providers

- `FrozenProfileProvider`: backward-compatible profile playback.
- `SyntheticMetricsProvider`: dynamic metrics generated from `dynamics` in an existing profile.
- `LiveSystemProvider`: live CPU, memory, disk, network I/O, and bounded process snapshots via `psutil`.

The frozen provider preserves the major legacy profile sections (`environment`,
`software_batches`, `performance`, `process_snapshot`, `activity_events`,
`connectivity_rows`, and `ice_traces`) inside the generic `metrics` object. This
keeps the old fixtures usable without making the runtime understand proprietary
message framing.

## Clocks

- `RealClock`: wall-clock execution.
- `SimulatedClock`: deterministic fast-forward execution for tests.

## Transports

- `MemoryTransport`: in-process tests.
- `FileDumpTransport`: NDJSON file output.
- `TcpTransport`: generic NDJSON-over-TCP for loopback/LAN tests.
- `UdpTransport`: one JSON object per datagram.

TCP/UDP destinations are restricted to loopback/private/link-local addresses by
default. Public destinations require an explicit `allow_public` configuration
opt-in.

Example configurations are included for offline file output, LAN TCP, and LAN
UDP:

- `config.live.file.example.json`
- `config.live.tcp.example.json`
- `config.live.udp.example.json`

The LAN examples use RFC1918 addresses only and are inert until a test listener
is supplied at the configured address.

## Runtime envelope schema

Every provider produces `TelemetrySnapshot`; the agent converts it into the
same versioned envelope:

```json
{
  "schema_version": 1,
  "observed_at": "2030-01-01T00:00:00+00:00",
  "provider": "synthetic",
  "metrics": {},
  "metadata": {}
}
```

The machine-readable contract lives in `runtime-envelope.schema.json`.
`baseline.schema.json` remains evidence about the historical fixture/protocol
shape; it is not mutated into a runtime transport schema. Compatibility is
instead maintained by the frozen provider mapping the old profile sections into
the generic envelope.

## Registry-based extension

Provider, transport, and clock creation use registries rather than a growing
`if/elif` dispatch chain. Core aliases are registered at startup:

- providers: `frozen_profile` / `frozen`, `synthetic`, `live_system` / `live`
- transports: `memory`, `file_dump` / `file`, `tcp` / `loopback_tcp`, `udp`
- clocks: `real`, `simulated` / `fake`

A custom provider can be added without editing `TelemetryAgent` or `config.py`:

```python
from telemetry import BaseMetricsProvider, TelemetrySnapshot, register_provider

class ConstantProvider(BaseMetricsProvider):
    name = "constant"

    async def snapshot(self, clock):
        return TelemetrySnapshot(
            observed_at=clock.now().isoformat(),
            provider=self.name,
            metrics={"value": 42},
        )

register_provider("constant", lambda cfg: ConstantProvider())
```

The same extension mechanism is available through `register_transport()` and
`register_clock()`.

## Backward compatibility

Existing configuration of the form:

```json
{
  "profile": "lab/mock-telemetry/baseline.synthetic.json",
  "transport": {"type": "memory"}
}
```

continues to load as a `FrozenProfileProvider`. Existing `loopback_tcp`
transport names remain accepted.

The legacy `identity.test_mode` field may remain inside existing fixtures, but
the generic runtime does not use it as a startup gate. It is preserved only as
metadata for fixture auditing.

## Examples

Install dependencies:

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

## CI

`.github/workflows/telemetry-runtime.yml` runs compile checks and unit tests on
Ubuntu and Windows with Python 3.11 and 3.13. The live-provider test is bounded
to a small process snapshot and validates the output against
`runtime-envelope.schema.json`.

## Extension contract

New providers implement `BaseMetricsProvider.snapshot()` and return
`TelemetrySnapshot`. New transports implement `BaseTransport.send()` and
consume the same generic envelope. New clocks implement `BaseClock`.

Neither extension requires changes to `TelemetryAgent`.
