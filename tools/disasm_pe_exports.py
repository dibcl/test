#!/usr/bin/env python3
import argparse
import json
import struct
import sys
from pathlib import Path

VENDOR = Path(__file__).resolve().parent / "vendor"
sys.path.insert(0, str(VENDOR))
from capstone import Cs, CS_ARCH_X86, CS_MODE_32, CS_MODE_64
from capstone.x86 import X86_OP_IMM, X86_OP_MEM, X86_REG_INVALID


def read_cstr(data, off):
    if off is None or not (0 <= off < len(data)):
        return None
    end = data.find(b"\0", off)
    if end < 0:
        return None
    raw = data[off:end]
    if not raw or any(x < 0x20 or x > 0x7E for x in raw):
        return None
    return raw.decode("ascii", "replace")


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
        raise ValueError("unsupported PE")
    sections = []
    sec_off = opt + opt_size
    for i in range(nsects):
        off = sec_off + 40 * i
        name = data[off:off+8].rstrip(b"\0").decode("ascii", "replace")
        vsize, vaddr, rsize, rptr = struct.unpack_from("<IIII", data, off + 8)
        sections.append((name, vaddr, vsize, rptr, rsize))
    return bits, image_base, sections


def rva_to_off(rva, sections):
    for _, va, vs, rp, rs in sections:
        if va <= rva < va + max(vs, rs):
            return rp + (rva - va)
    return rva if rva >= 0 else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pe", type=Path)
    ap.add_argument("metadata", type=Path)
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args()
    data = args.pe.read_bytes()
    meta = json.loads(args.metadata.read_text(encoding="utf-8"))
    bits, base, sections = pe_layout(data)
    exports = [e for e in meta["exports"] if int(e["rva"], 16) < 0x10000]
    exports.sort(key=lambda e: int(e["rva"], 16))
    md = Cs(CS_ARCH_X86, CS_MODE_32 if bits == 32 else CS_MODE_64)
    md.detail = True
    lines = [f"file={args.pe}", f"bits={bits}", f"image_base=0x{base:x}", ""]
    for idx, exp in enumerate(exports):
        start = int(exp["rva"], 16)
        if idx + 1 < len(exports):
            end = int(exports[idx + 1]["rva"], 16)
        else:
            text = next(s for s in sections if s[0] == ".text")
            end = text[1] + text[2]
        off = rva_to_off(start, sections)
        blob = data[off:off + max(0, end - start)]
        lines.append(f"===== {exp['name']} ordinal={exp['ordinal']} RVA=0x{start:x} =====")
        for ins in md.disasm(blob, base + start):
            annotation = []
            for op in ins.operands:
                candidate = None
                if op.type == X86_OP_IMM:
                    candidate = op.imm
                elif op.type == X86_OP_MEM and op.mem.base == X86_REG_INVALID and op.mem.index == X86_REG_INVALID:
                    candidate = op.mem.disp
                if candidate is not None and base <= candidate < base + 0x200000:
                    soff = rva_to_off(candidate - base, sections)
                    s = read_cstr(data, soff)
                    if s:
                        annotation.append(repr(s))
            suffix = " ; " + ", ".join(annotation) if annotation else ""
            lines.append(f"0x{ins.address:08x}: {ins.mnemonic:<8} {ins.op_str}{suffix}")
        lines.append("")
    args.output.write_text("\n".join(lines), encoding="utf-8")
    print(args.output)


if __name__ == "__main__":
    main()
