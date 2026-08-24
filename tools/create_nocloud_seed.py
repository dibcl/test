"""Create a minimal NoCloud seed ISO without requiring host ISO utilities."""

from __future__ import annotations

import argparse
import io
from pathlib import Path
import sys


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--user-data", required=True, type=Path)
    parser.add_argument("--meta-data", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--python-libs",
        type=Path,
        help="optional directory containing the pycdlib package",
    )
    args = parser.parse_args()

    if args.python_libs:
        sys.path.insert(0, str(args.python_libs))
    import pycdlib

    iso = pycdlib.PyCdlib()
    iso.new(interchange_level=3, joliet=3, vol_ident="CIDATA")
    for source, iso_path, joliet_path in (
        (args.user_data, "/USER_DAT.;1", "/user-data"),
        (args.meta_data, "/META_DAT.;1", "/meta-data"),
    ):
        payload = source.read_bytes()
        iso.add_fp(
            io.BytesIO(payload),
            len(payload),
            iso_path=iso_path,
            joliet_path=joliet_path,
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    iso.write(str(args.output))
    iso.close()


if __name__ == "__main__":
    main()
