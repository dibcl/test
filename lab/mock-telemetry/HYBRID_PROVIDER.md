# Hybrid synthetic + live-network provider

`HybridSyntheticNetworkProvider` is the runtime candidate for privacy-isolated host testing.

Its contract is intentionally narrow:

- CPU: synthetic dynamic values from the profile `dynamics.cpu` block.
- Memory: synthetic dynamic values from `dynamics.memory`.
- Disk I/O: synthetic dynamic values from `dynamics.disk_io`.
- Process snapshot: profile-backed test fixture data.
- Software / KB inventory: declaration/fixture responsibility, not host discovery.
- Network throughput: the only live host input. It uses aggregate `psutil.net_io_counters(pernic=False)` counters and derives TX/RX bytes per second.

The provider must not discover or emit host identity/state such as hostname, interface names, IP addresses, MAC addresses, routes, DNS settings, CPU state, memory state, disk usage, package inventory, or the live process list.

`LiveSystemProvider` remains available only as a diagnostic/development provider. It intentionally inspects host state and is not the privacy-isolated runtime path.

## Example

```json
{
  "provider": {
    "type": "hybrid_network",
    "profile": "lab/mock-telemetry/baseline.synthetic.json"
  },
  "clock": {"type": "real"},
  "transport": {
    "type": "file_dump",
    "path": "lab/mock-telemetry/out/hybrid-network.jsonl"
  },
  "interval_seconds": 1,
  "duration_seconds": 30
}
```

Aliases: `hybrid` and `synthetic_live_network`.

The unit suite includes a guard test that replaces hostname, CPU, memory, disk and process discovery APIs with exceptions. A hybrid snapshot must still succeed, proving that only aggregate network counters are touched by this provider.
