#!/usr/bin/env python3
"""Disassemble an explicit PE virtual-address range without executing it."""

import argparse
import struct
import sys
from pathlib import Path

VENDOR = Path(__file__).resolve().parent / "vendor"
sys.path.insert(0, str(VENDOR))
from capstone import Cs, CS_ARCH_X86, CS_MODE_32, CS_MODE_64


def layout(data):
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    _, nsects, _, _, _, opt_size, _ = struct.unpack_from("<HHIIIHH", data, pe + 4)
    opt = pe + 24
    magic = struct.unpack_from("<H", data, opt)[0]
    if magic == 0x10B:
        bits, base = 32, struct.unpack_from("<I", data, opt + 28)[0]
    elif magic == 0x20B:
        bits, base = 64, struct.unpack_from("<Q", data, opt + 24)[0]
    else:
        raise ValueError("unsupported PE")
    sections = []
    sec_off = opt + opt_size
    for i in range(nsects):
        off = sec_off + 40 * i
        name = data[off:off + 8].rstrip(b"\0").decode("ascii", "replace")
        vsize, va, rsize, rptr = struct.unpack_from("<IIII", data, off + 8)
        sections.append((name, va, vsize, rptr, rsize))
    return bits, base, sections


def rva_to_off(rva, sections):
    for _, va, vs, rp, rs in sections:
        if va <= rva < va + max(vs, rs):
            return rp + rva - va
    raise ValueError(f"RVA 0x{rva:x} is not mapped")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pe", type=Path)
    ap.add_argument("start", help="start VA, or RVA with --rva")
    ap.add_argument("end", help="exclusive end VA, or RVA with --rva")
    ap.add_argument("--rva", action="store_true")
    args = ap.parse_args()
    data = args.pe.read_bytes()
    bits, base, sections = layout(data)
    start, end = int(args.start, 0), int(args.end, 0)
    if args.rva:
        start += base
        end += base
    off = rva_to_off(start - base, sections)
    md = Cs(CS_ARCH_X86, CS_MODE_32 if bits == 32 else CS_MODE_64)
    for ins in md.disasm(data[off:off + end - start], start):
        print(f"0x{ins.address:08x}: {ins.mnemonic:<8} {ins.op_str}")


if __name__ == "__main__":
    main()
