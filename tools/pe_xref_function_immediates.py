#!/usr/bin/env python3
"""Report immediate values used in functions that reference an ASCII string.

This helper performs static PE parsing only. It is useful for associating a
local IPC endpoint string with message IDs constructed in the same function.
"""

import argparse
import struct
import sys
from pathlib import Path

VENDOR = Path(__file__).resolve().parent / "vendor"
sys.path.insert(0, str(VENDOR))

from capstone import Cs, CS_ARCH_X86, CS_MODE_32, CS_MODE_64
from capstone.x86 import X86_OP_IMM, X86_OP_MEM, X86_REG_INVALID, X86_REG_RIP


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


def offset_to_va(offset, base, sections):
    for _, va, _vsize, raw, raw_size in sections:
        if raw <= offset < raw + raw_size:
            return base + va + offset - raw
    raise ValueError("string is not in a mapped section")


def references(insn, target):
    if insn.id == 0:
        return False
    for operand in insn.operands:
        if operand.type == X86_OP_IMM and operand.imm == target:
            return True
        if operand.type != X86_OP_MEM:
            continue
        mem = operand.mem
        if mem.base == X86_REG_RIP and insn.address + insn.size + mem.disp == target:
            return True
        if mem.base == X86_REG_INVALID and mem.index == X86_REG_INVALID and mem.disp == target:
            return True
    return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("pe", type=Path)
    parser.add_argument("string")
    parser.add_argument("--immediate", default="0x22")
    args = parser.parse_args()

    data = args.pe.read_bytes()
    bits, base, sections = layout(data)
    offset = data.find(args.string.encode("ascii"))
    if offset < 0:
        raise SystemExit("string not found")
    target = offset_to_va(offset, base, sections)
    text = next(section for section in sections if section[0] == ".text")
    _, text_va, _, text_raw, text_size = text
    md = Cs(CS_ARCH_X86, CS_MODE_32 if bits == 32 else CS_MODE_64)
    md.detail = True
    md.skipdata = True
    insns = list(md.disasm(data[text_raw:text_raw + text_size], base + text_va))
    wanted = int(args.immediate, 0)

    for index, insn in enumerate(insns):
        if not references(insn, target):
            continue
        start = index
        while start > 1:
            if (insns[start].mnemonic == "push" and insns[start].op_str == "ebp"
                    and insns[start + 1].mnemonic == "mov"
                    and insns[start + 1].op_str == "ebp, esp"):
                break
            start -= 1
        end = index
        while end < len(insns) and insns[end].mnemonic not in ("ret", "retf"):
            end += 1
        hits = []
        for candidate in insns[start:min(end + 1, len(insns))]:
            if candidate.id == 0:
                continue
            if any(op.type == X86_OP_IMM and op.imm == wanted for op in candidate.operands):
                hits.append(candidate)
        print(f"xref=0x{insn.address:x} function=0x{insns[start].address:x} immediate=0x{wanted:x} hits={len(hits)}")
        for hit in hits:
            print(f"  0x{hit.address:08x}: {hit.mnemonic:<8} {hit.op_str}")


if __name__ == "__main__":
    main()
