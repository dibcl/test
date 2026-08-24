# Runtime fidelity contract

This document defines the acceptance boundary for the generic telemetry runtime before Windows/Debian field testing.

## Confirmed design rules

- CPU, memory, disk and process data are synthetic/declarative.
- The runtime does not discover hostname, interface names, IP/MAC, route, DNS, packages or host processes.
- The only live host input is aggregate network byte counters used internally to calculate RX/TX rates.
- Aggregate byte totals are not emitted; only rates are exposed.
- Process telemetry uses a declared non-sensitive Windows/system/guest-tool process pool and generates bounded dynamic values.
- The runtime profile used by `config.hybrid-network.example.json` is `baseline.runtime.json`, not the legacy protocol-oriented `baseline.synthetic.json`.

## Shape coverage derived from captured Windows logs

The generic runtime now preserves the high-level data categories that were observed in the Windows corpus:

- aggregate CPU plus per-core values;
- memory utilisation plus paged/non-paged pool-like values;
- aggregate disk activity plus per-disk rows;
- process CPU/memory/handle/disk/network ranking groups and a key-process field;
- live aggregate RX/TX throughput.

These are generic runtime structures. They are not proprietary management-plane frame encodings.

## Reference-only artefacts

`fixtures/observed-software-baseline.json` remains an audit/reference file. It is intentionally not wired into a production/replay sender. The same rule applies to captured identity/network evidence: it may be used for offline comparison and documentation, but the generic runtime does not auto-discover host identity.

## Automated acceptance

`fidelity_contract.json` defines required and forbidden fields. `fidelity_audit.py` validates generated JSONL against that contract. CI runs the same audit in Debian 13.

The acceptance suite fails if the hybrid provider starts exposing host identity/state, if process category coverage regresses, or if aggregate network byte totals are emitted.

## Not proven by this runtime

The following remain separate integration questions and are not claimed by a green runtime test:

- real VirtIO host integration;
- real cloud management backend behaviour;
- proprietary GuestTools/control-plane compatibility;
- QGA/ICE behaviour;
- production session continuity.

A green runtime test means the host-isolated data-generation layer behaves as designed. It does not prove production backend equivalence.
