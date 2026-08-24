#!/usr/bin/env python3
import argparse
import hashlib
import json
import re
import struct
from pathlib import Path


def cstr(data, offset):
    if offset is None or offset < 0 or offset >= len(data):
        return ""
    end = data.find(b"\0", offset)
    if end < 0:
        end = len(data)
    return data[offset:end].decode("ascii", "replace")


def parse_pe(path):
    data = path.read_bytes()
    if data[:2] != b"MZ":
        raise ValueError("not an MZ executable")
    peoff = struct.unpack_from("<I", data, 0x3C)[0]
    if data[peoff:peoff + 4] != b"PE\0\0":
        raise ValueError("missing PE signature")
    machine, nsects, timestamp, _, _, opt_size, chars = struct.unpack_from(
        "<HHIIIHH", data, peoff + 4
    )
    opt = peoff + 24
    magic = struct.unpack_from("<H", data, opt)[0]
    if magic == 0x10B:
        bitness, dd_off, thunk_size, ordinal_mask = 32, opt + 96, 4, 0x80000000
    elif magic == 0x20B:
        bitness, dd_off, thunk_size, ordinal_mask = 64, opt + 112, 8, 0x8000000000000000
    else:
        raise ValueError(f"unknown optional header magic {magic:#x}")
    section_off = opt + opt_size
    sections = []
    for i in range(nsects):
        off = section_off + i * 40
        name = data[off:off + 8].rstrip(b"\0").decode("ascii", "replace")
        vsize, vaddr, raw_size, raw_ptr = struct.unpack_from("<IIII", data, off + 8)
        sections.append({
            "name": name, "vsize": vsize, "vaddr": vaddr,
            "raw_size": raw_size, "raw_ptr": raw_ptr,
        })

    def rva_to_offset(rva):
        for s in sections:
            span = max(s["vsize"], s["raw_size"])
            if s["vaddr"] <= rva < s["vaddr"] + span:
                return s["raw_ptr"] + (rva - s["vaddr"])
        if 0 <= rva < len(data):
            return rva
        return None

    export_rva, export_size = struct.unpack_from("<II", data, dd_off)
    import_rva, import_size = struct.unpack_from("<II", data, dd_off + 8)
    exports = []
    export_off = rva_to_offset(export_rva) if export_rva else None
    if export_off is not None and export_off + 40 <= len(data):
        fields = struct.unpack_from("<IIHHIIIIIII", data, export_off)
        _, _, _, _, _, ordinal_base, nfunc, nname, funcs_rva, names_rva, ords_rva = fields
        names_off = rva_to_offset(names_rva)
        ords_off = rva_to_offset(ords_rva)
        funcs_off = rva_to_offset(funcs_rva)
        if names_off is not None and ords_off is not None and funcs_off is not None:
            for i in range(min(nname, 65536)):
                name_ptr = struct.unpack_from("<I", data, names_off + i * 4)[0]
                name = cstr(data, rva_to_offset(name_ptr))
                ordinal_index = struct.unpack_from("<H", data, ords_off + i * 2)[0]
                func_rva = struct.unpack_from("<I", data, funcs_off + ordinal_index * 4)[0]
                exports.append({
                    "name": name,
                    "ordinal": ordinal_base + ordinal_index,
                    "rva": hex(func_rva),
                })
    imports = []
    desc_off = rva_to_offset(import_rva) if import_rva else None
    if desc_off is not None:
        cursor = desc_off
        for _ in range(4096):
            if cursor + 20 > len(data):
                break
            oft, tstamp, fchain, name_rva, ft = struct.unpack_from("<IIIII", data, cursor)
            cursor += 20
            if not any((oft, tstamp, fchain, name_rva, ft)):
                break
            dll = cstr(data, rva_to_offset(name_rva)).lower()
            thunk_rva = oft or ft
            thunk_off = rva_to_offset(thunk_rva)
            funcs = []
            if thunk_off is not None:
                for idx in range(65536):
                    pos = thunk_off + idx * thunk_size
                    if pos + thunk_size > len(data):
                        break
                    val = struct.unpack_from("<I" if thunk_size == 4 else "<Q", data, pos)[0]
                    if val == 0:
                        break
                    if val & ordinal_mask:
                        funcs.append(f"ordinal:{val & 0xffff}")
                    else:
                        ibn = rva_to_offset(val)
                        funcs.append(cstr(data, ibn + 2 if ibn is not None else None))
            imports.append({"dll": dll, "functions": sorted(set(funcs))})

    ascii_strings = [m.group().decode("ascii", "replace") for m in re.finditer(rb"[\x20-\x7e]{4,}", data)]
    utf16_strings = []
    for m in re.finditer(rb"(?:[\x20-\x7e]\x00){4,}", data):
        utf16_strings.append(m.group().decode("utf-16le", "replace"))
    strings = []
    seen = set()
    for encoding, values in (("ascii", ascii_strings), ("utf16le", utf16_strings)):
        for value in values:
            key = (encoding, value)
            if key not in seen:
                seen.add(key)
                strings.append({"encoding": encoding, "value": value})

    machine_names = {0x14C: "x86", 0x8664: "x86-64", 0xAA64: "arm64"}
    return {
        "path": str(path),
        "sha256": hashlib.sha256(data).hexdigest(),
        "size": len(data),
        "machine": machine_names.get(machine, hex(machine)),
        "bitness": bitness,
        "timestamp": timestamp,
        "characteristics": hex(chars),
        "sections": sections,
        "imports": imports,
        "exports": exports,
        "strings": strings,
        "import_directory_size": import_size,
        "export_directory_size": export_size,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("paths", nargs="+", type=Path)
    ap.add_argument("--out-dir", type=Path, required=True)
    args = ap.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    index = []
    for path in args.paths:
        result = parse_pe(path)
        stem = path.stem
        json_path = args.out_dir / f"{stem}.pe.json"
        strings_path = args.out_dir / f"{stem}.strings.txt"
        json_copy = dict(result)
        json_copy.pop("strings")
        json_path.write_text(json.dumps(json_copy, ensure_ascii=False, indent=2), encoding="utf-8")
        with strings_path.open("w", encoding="utf-8", newline="\n") as fh:
            for idx, item in enumerate(result["strings"], 1):
                value = item["value"].replace("\r", "\\r").replace("\n", "\\n")
                fh.write(f"{idx}\t{item['encoding']}\t{value}\n")
        index.append({
            "path": str(path), "json": str(json_path), "strings": str(strings_path),
            "sha256": result["sha256"], "machine": result["machine"],
            "bitness": result["bitness"], "string_count": len(result["strings"]),
        })
    (args.out_dir / "index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(index, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
