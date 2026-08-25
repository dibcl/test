# Windows current-machine validation

This is the next validation stage for the cross-platform telemetry runtime. The goal is to run the current branch on the real Windows cloud VM, capture the runtime's own JSONL output, and compare its behavior with the previously captured Guest logs before any Debian packaging work begins.

## Authoritative current-machine baseline

`lab/mock-telemetry/local_env.json` is tracked intentionally and contains the current Windows VM baseline used by the validation provider:

- VMID
- UUID
- HOSTID
- COMPUTERNAME
- MAC
- IP
- CPU
- OS
- MEM
- DISK

The loader validates the file strictly and the Windows validation provider emits those values exactly under `metrics.local_environment`. It does not call hostname/IP/MAC/OS discovery APIs to replace them.

## Runtime data policy

`windows_validation` composes the existing host-isolated hybrid provider with `local_env.json`:

- local identity/environment: exact values from `local_env.json`
- CPU: bounded dynamic model
- memory: bounded dynamic model
- disk/per-disk activity: bounded dynamic model
- process rankings: declared Windows/GuestTools process pool
- network: aggregate RX/TX rate is the only live host metric

This preserves the intended cross-platform architecture: the runtime can be validated on Windows without coupling the core agent to Windows-only discovery code.

## Run on the Windows validation VM

From the repository checkout:

```powershell
cd lab\mock-telemetry
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe run_agent.py --config config.windows-validation.json
```

The 30-second run writes:

```text
out/windows-validation.jsonl
```

Validate every emitted row against the tracked environment and the hybrid fidelity contract:

```powershell
.\.venv\Scripts\python.exe windows_validation_audit.py out\windows-validation.jsonl --local-env local_env.json
```

Expected result:

```text
windows validation audit: OK (... rows)
```

## What the audit proves

For every runtime row it verifies:

1. provider is `windows-validation`;
2. `metrics.local_environment` matches `local_env.json` exactly;
3. the environment source marker is present;
4. all synthetic CPU/memory/disk/process fidelity rules still pass;
5. the only live host data path remains aggregate network throughput;
6. no accidental host-discovery fields are introduced by the dynamic part of the provider.

## Next comparison step

After the Windows run is stable, compare the generated timeline and data shapes against the captured real Guest logs. That comparison must distinguish:

- exact/static values such as identity and environment;
- message/event ordering and cadence;
- structural fields and batch grouping;
- dynamic ranges/distributions, which are not expected to match sample-for-sample.

Do not begin Debian packaging until the Windows validation report is accepted.
