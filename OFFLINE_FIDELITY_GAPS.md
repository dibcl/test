# Offline telemetry fidelity gaps

This project models six offline Windows telemetry message types: `4002`, `4004`, `9050`, `9051`, `9052`, and `9054`. It generates local JSON evidence and mock Mswitch frames. It is **not** a complete Guest protocol emulator, does not replay private Host traffic, and must not be connected to a production Host.

The following additional message types were observed in the fresh official runtime window `2026-08-26 10:00:00–12:00:00 +08:00`. Counts and cadence are observations from that single window, not protocol specifications.

| Direction | msgtype | Count | Observed frequency | Available semantic evidence | Status |
|---|---:|---:|---|---|---|
| Guest → Host | 8007 | 24 | mean 300.52s (300–301s) | Payload exposes only `msgtype`; purpose unconfirmed | UNKNOWN / NOT IMPLEMENTED |
| Guest → Host | 9056 | 24 | mean 302.26s (300–305s) | Object contains `tablename`, `columnname`, and `datas[].row`; exact contract unknown | UNKNOWN / NOT IMPLEMENTED |
| Guest → Host | 8059 | 24 | mean 300.00s (299–301s) | Captured form is not sufficiently decoded to establish semantics | UNKNOWN / NOT IMPLEMENTED |
| Guest → Host | 9053 | 9 | 300s in the observed subset | Contains `source`, `uuid`, `hostid`, `time`, and `logdatas[].log`; appears log-related, exact contract unknown | UNKNOWN / NOT IMPLEMENTED |
| Guest → Host | 8047 | 1 | one occurrence | Contains `msgid` and `vmuuid`; request/notification role unconfirmed | UNKNOWN / NOT IMPLEMENTED |
| Guest → Host | 8063 | 1 | one occurrence | Captured form is not sufficiently decoded to establish semantics | UNKNOWN / NOT IMPLEMENTED |
| Guest → Host | 8454852 | 5 | mean 1448.50s (1282–1510s) | Captured form is not sufficiently decoded to establish semantics | UNKNOWN / NOT IMPLEMENTED |
| Host → Guest | 4100 | 240 | mean 30.008s (30–31s) | Observed as the Host response associated with 4002 heartbeat traffic | OBSERVED ACK / NOT EMITTED OFFLINE |
| Host → Guest | 8052 | 1 | one occurrence | Payload exposes only `msgtype`; purpose unconfirmed | UNKNOWN / NOT IMPLEMENTED |
| Host → Guest | 9502 | 1 | one occurrence | Payload exposes only `msgtype`; purpose unconfirmed | UNKNOWN / NOT IMPLEMENTED |
| Host → Guest | 8064 | 1 | one occurrence | Payload exposes only `msgtype`; purpose unconfirmed | UNKNOWN / NOT IMPLEMENTED |

These gaps are intentionally recorded rather than implemented. No identity simulation, private request replay, or Host-facing behavior should be inferred from the six-message offline model.

## Intentional 9054 modeling difference

The retained official boot reference has 13 entries and a 2354-byte payload in its second software batch because it includes Clash Verge. The modeled fixture intentionally omits Clash Verge, so the Synthetic second batch has 12 entries and a 2230-byte payload. Do not add filler data merely to equalize this length.

## Workload variance policy

Absolute CPU, memory, disk, and network distributions from one official two-hour window are workload observations, not fixed fitting targets. Protocol structure, cadence, field semantics, serialization, ranking behavior, and frame consistency remain the fidelity criteria; the accepted CPU/memory envelope is not recalibrated against one idle or active window.
