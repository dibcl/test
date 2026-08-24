# Managed runtime lifecycle and hot reload

The generic telemetry runtime supports provider health checks, transactional provider switching, local JSON config hot reload, and restart-required tracking for changes that are not safe to mutate in place.

## Lifecycle

`TelemetryRuntime` reports one of these states:

```text
ready -> starting -> running -> stopping -> stopped
                 \-> switching -> running
                 \-> degraded
                 \-> failed
```

`degraded` means a live reconfiguration or cleanup failed while the existing runtime may still be usable. `failed` means the main runtime task itself failed.

`runtime.status.to_dict()` exposes:

- active provider
- provider generation number
- successful and failed reload counters
- `restart_required`
- last error

## Provider health contract

Every `BaseMetricsProvider` has an asynchronous `health_check()` method returning `ProviderHealth`.

Health checks are intended to be side-effect-free and must not consume a telemetry sample. The built-in providers verify their local dependencies:

- frozen profile: source file and loaded object
- synthetic provider: profile and dynamics shape
- live system provider: `psutil`, disk path, and basic host availability

## Transactional switching

`TelemetryAgent.set_provider()` follows this order:

```text
build candidate
  -> start candidate
  -> health check candidate
  -> activate candidate
  -> stop previous provider
```

If start or health validation fails, the candidate is stopped and the previous provider remains active. A failed candidate never replaces the current provider.

When the agent is idle, a candidate is started only for its readiness probe, then stopped and retained as the provider for the next `run()`.

## Local config hot reload

Hot reload is opt-in and only works when the runtime is created with `TelemetryRuntime.from_file()` or through `run_agent.py --config ...`.

Example:

```json
{
  "provider": {
    "type": "synthetic",
    "profile": "lab/mock-telemetry/baseline.synthetic.json"
  },
  "clock": {"type": "real"},
  "transport": {
    "type": "file_dump",
    "path": "lab/mock-telemetry/out/hot-reload.jsonl"
  },
  "interval_seconds": 1,
  "provider_health_timeout": 5,
  "reload": {
    "enabled": true,
    "poll_seconds": 1
  }
}
```

The checked-in example is `config.hot-reload.example.json`.

The watcher hashes the local JSON file and reacts only after content changes. Malformed partial writes are retried. A complete but invalid provider configuration is rejected once for that content and leaves the old provider active.

## Live-safe versus restart-required settings

Only provider replacement is applied live in this phase. Changes to these settings are recorded in `desired_config` and set `restart_required=true`:

- transport
- clock
- interval
- duration
- schema version
- provider health timeout

The currently active settings stay unchanged until restart. This avoids partially mutating scheduling or I/O while an agent is running.

## Safety boundary

This runtime remains protocol-agnostic. TCP and UDP transports carry the generic telemetry envelope only. They do not implement proprietary message framing or replay management-plane messages. Public destinations remain blocked by default by the transport network policy.
