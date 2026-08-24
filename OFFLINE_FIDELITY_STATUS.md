# Offline fidelity acceptance status

This document defines what can be called complete in this repository without a live cloud Host/Backend test.

## Acceptance boundary

`100% offline fidelity` means every **observed, normalized behavior that is intentionally represented by the generic runtime** has an automated contract or regression test, and the runtime cannot silently discover host identity/state outside the approved live-network-rate boundary.

It does **not** mean byte-for-byte equivalence with a production Guest management implementation or compatibility with a real cloud Host/Backend.

## Accepted offline runtime contract

- [x] CPU is generated from a bounded dynamic model; no host CPU sampling in the hybrid path.
- [x] Eight per-core CPU values are emitted and range checked.
- [x] Memory percentage and paged/non-paged pool-like values are generated and range checked.
- [x] Disk activity is synthetic and includes declared C/D disk rows.
- [x] Five process ranking groups are generated from a declared non-sensitive Windows/guest-tool process pool.
- [x] End-user application names are excluded from the declared process pool.
- [x] The only live host input is aggregate network byte counters used internally to calculate RX/TX rate.
- [x] Aggregate byte totals are not emitted.
- [x] Hostname, interface name, IP, MAC, route, gateway, netmask, DNS, OS, kernel, machine-id and boot-id are forbidden by the fidelity audit.
- [x] Linux/Debian/systemd/proc-style host markers are rejected from runtime metric values.
- [x] Software/KB evidence is retained as a reference-only, batch-aware fixture with the user-requested sensitive software filter explicitly recorded.
- [x] Real identity values are not accepted by the payload-profile renderer; only validated TEST_* values can render test payload profiles.
- [x] Prototype TCP transports are loopback-only; the former external override is rejected.
- [x] QGA test behavior defaults to the observed time-query/sync surface; OS/file/exec/shutdown commands are not emulated, and fixture network interfaces are opt-in only.
- [x] Runtime tests and prototype offline tests are part of CI on Windows and Ubuntu, plus a Debian 13 smoke lane.

## Evidence-to-code separation

Observed Windows logs are used to determine normalized shapes and reasonable ranges. They are not automatically converted into live proprietary messages. In particular:

- `lab/mock-telemetry/fixtures/observed-software-baseline.json` is audit/reference data only.
- `lab/mock-telemetry/baseline.runtime.json` is the intended host-isolated runtime profile.
- `lab/mock-telemetry/baseline.synthetic.json` remains a legacy synthetic fixture for old protocol-lab tests and is not the intended hybrid runtime profile.
- The generic runtime does not contain a production cloud Backend connector.

## Not claimed / requires a real authorized platform test

The following cannot be established by offline fixtures or CI and therefore must remain **unverified**, not silently marked complete:

- real VirtIO Host behavior;
- real GuestTools installation/upgrade behavior;
- real hmbooster/HA behavior;
- real cloud Backend acceptance, persistence, alarms or UI visibility;
- full ICE/RAP session equivalence;
- production QGA call surface beyond the behavior actually observed;
- production timing/reconnect behavior under every failure mode.

A green CI run means the offline runtime and its isolation/fidelity contracts pass. It must never be described as proof of production Backend equivalence.
