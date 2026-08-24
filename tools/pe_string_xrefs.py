#!/usr/bin/env python3
"""Print x86/x64 PE code references to selected ASCII strings.

This is a deliberately small static-analysis helper.  It does not load or run
the target binary.
"""

import argparse
import struct
import sys
from pathlib import Path

VENDOR = Path(__file__).resolve().parent / "vendor"
sys.path.insert(0, str(VENDOR))

from capstone import Cs, CS_ARCH_X86, CS_MODE_32, CS_MODE_64
from capstone.x86 import X86_OP_IMM, X86_OP_MEM, X86_REG_INVALID, X86_REG_RIP


def pe_layout(data):
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    _, nsects, _, _, _, opt_size, _ = struct.unpack_from("<HHIIIHH", data, pe + 4)
    opt = pe + 24
    magic = struct.unpack_from("<H", data, opt)[0]
    if magic == 0x10B:
        bits = 32
        image_base = struct.unpack_from("<I", data, opt + 28)[0]
    elif magic == 0x20B:
        bits = 64
        image_base = struct.unpack_from("<Q", data, opt + 24)[0]
    else:
        raise ValueError("unsupported PE optional header")
    sections = []
    sec_off = opt + opt_size
    for i in range(nsects):
        off = sec_off + 40 * i
        name = data[off:off + 8].rstrip(b"\0").decode("ascii", "replace")
        vsize, vaddr, rsize, rptr = struct.unpack_from("<IIII", data, off + 8)
        sections.append((name, vaddr, vsize, rptr, rsize))
    return bits, image_base, sections


def off_to_rva(off, sections):
    for _, va, _vs, rp, rs in sections:
        if rp <= off < rp + rs:
            return va + (off - rp)
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pe", type=Path)
    ap.add_argument("strings", nargs="+")
    ap.add_argument("--va", action="append", default=[],
                    help="also find references to this virtual address (hex or decimal)")
    ap.add_argument("--disp", action="append", default=[],
                    help="also print instructions using this memory displacement")
    ap.add_argument("--imm", action="append", default=[],
                    help="also print instructions using this immediate value")
    ap.add_argument("--before", type=int, default=12)
    ap.add_argument("--after", type=int, default=28)
    args = ap.parse_args()

    data = args.pe.read_bytes()
    bits, base, sections = pe_layout(data)
    text = next(s for s in sections if s[0] == ".text")
    _, text_rva, _text_vs, text_ptr, text_raw = text
    md = Cs(CS_ARCH_X86, CS_MODE_32 if bits == 32 else CS_MODE_64)
    md.detail = True
    # PE .text sections can contain alignment/data bytes.  Without skipdata,
    # Capstone stops at the first undecodable byte and silently misses later
    # functions and their xrefs.
    md.skipdata = True
    insns = list(md.disasm(data[text_ptr:text_ptr + text_raw], base + text_rva))

    targets = {}
    for wanted in args.strings:
        needle = wanted.encode("ascii")
        start = 0
        while True:
            off = data.find(needle, start)
            if off < 0:
                break
            rva = off_to_rva(off, sections)
            if rva is not None:
                targets[base + rva] = wanted
            start = off + 1
    for raw in args.va:
        value = int(raw, 0)
        targets[value] = f"VA:{raw}"

    for raw in args.disp:
        value = int(raw, 0)
        print(f"\n===== memory displacement 0x{value:x} =====")
        for ins in insns:
            if ins.id == 0:
                continue
            if any(op.type == X86_OP_MEM and op.mem.disp == value
                   for op in ins.operands):
                print(f" 0x{ins.address:08x}: {ins.mnemonic:<8} {ins.op_str}")

    for raw in args.imm:
        value = int(raw, 0)
        print(f"\n===== immediate 0x{value:x} =====")
        for ins in insns:
            if ins.id == 0:
                continue
            if any(op.type == X86_OP_IMM and op.imm == value
                   for op in ins.operands):
                print(f" 0x{ins.address:08x}: {ins.mnemonic:<8} {ins.op_str}")

    print(f"file={args.pe}")
    print(f"bits={bits} image_base=0x{base:x}")
    for target, value in sorted(targets.items()):
        hits = []
        for i, ins in enumerate(insns):
            if ins.id == 0:
                continue
            for op in ins.operands:
                candidate = None
                if op.type == X86_OP_IMM:
                    candidate = op.imm
                elif op.type == X86_OP_MEM and op.mem.base == X86_REG_RIP:
                    # x86-64 normally addresses constants and strings relative
                    # to the end of the current instruction.
                    candidate = ins.address + ins.size + op.mem.disp
                elif (op.type == X86_OP_MEM and op.mem.base == X86_REG_INVALID
                      and op.mem.index == X86_REG_INVALID):
                    candidate = op.mem.disp
                if candidate == target:
                    hits.append(i)
                    break
        print(f"\n===== {value!r} VA=0x{target:x} xrefs={len(hits)} =====")
        for hit_no, idx in enumerate(hits, 1):
            print(f"--- xref {hit_no} at 0x{insns[idx].address:x} ---")
            lo = max(0, idx - args.before)
            hi = min(len(insns), idx + args.after + 1)
            for j in range(lo, hi):
                mark = ">" if j == idx else " "
                ins = insns[j]
                print(f"{mark} 0x{ins.address:08x}: {ins.mnemonic:<8} {ins.op_str}")


if __name__ == "__main__":
    main()
