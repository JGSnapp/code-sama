"""CLI helpers for the VRoid adapter.

Examples::

    python tools/vroid/cli.py status
    python tools/vroid/cli.py launch
    python tools/vroid/cli.py install-desktop
    python tools/vroid/cli.py install path/to/model.vrm --name code-sama.vrm
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from server.vroid_adapter import get_vroid_adapter  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="VRoid ↔ code-sama-os adapter")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status", help="Show studio path, installed VRMs, Desktop hits")
    sub.add_parser("launch", help="Launch VRoid Studio")
    p_inst = sub.add_parser("install", help="Install a .vrm into uploads/vrm/")
    p_inst.add_argument("path")
    p_inst.add_argument("--name", default="code-sama.vrm")
    sub.add_parser("install-desktop", help="Install Desktop\\Code-sama.vrm")

    args = parser.parse_args()
    vroid = get_vroid_adapter()

    if args.cmd == "status":
        print(json.dumps(vroid.status(), ensure_ascii=False, indent=2))
        return 0
    if args.cmd == "launch":
        print(json.dumps(vroid.launch(), ensure_ascii=False, indent=2))
        return 0
    if args.cmd == "install":
        print(json.dumps(vroid.install(args.path, name=args.name), ensure_ascii=False, indent=2))
        return 0
    if args.cmd == "install-desktop":
        print(json.dumps(vroid.install_from_desktop(), ensure_ascii=False, indent=2))
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
