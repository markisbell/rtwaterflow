"""CLI for the offline geodata bundle builder (M8).

    # ONLINE — fetch OSM + DEM, freeze a pinned snapshot (committed)
    python -m bundle_builder snapshot "Alpen, Nordrhein-Westfalen, Germany" \
        --out tools/bundle_builder/snapshots/alpen.json

    # OFFLINE + deterministic — snapshot → data/networks/<id>/
    python -m bundle_builder build tools/bundle_builder/snapshots/alpen.json \
        --id alpen --name "Alpen (Niederrhein)" --population 12800
"""
from __future__ import annotations

import argparse
from pathlib import Path

from .pipeline import build_from_snapshot, make_snapshot, write_bundle
from .synthesize import SynthConfig


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="bundle_builder")
    sub = p.add_subparsers(dest="cmd", required=True)

    ps = sub.add_parser("snapshot", help="ONLINE: fetch OSM + DEM → snapshot")
    ps.add_argument("place")
    ps.add_argument("--out", required=True)
    ps.add_argument("--bbox", nargs=4, type=float, metavar=("N", "S", "E", "W"),
                    help="override the place name with a WGS84 bbox")

    pb = sub.add_parser("build", help="OFFLINE: snapshot → network bundle")
    pb.add_argument("snapshot")
    pb.add_argument("--id", required=True, help="network id (dir name)")
    pb.add_argument("--name", required=True, help="display name")
    pb.add_argument("--population", type=int, default=3000)
    pb.add_argument("--out-root", default="data/networks")

    args = p.parse_args(argv)
    if args.cmd == "snapshot":
        bbox = tuple(args.bbox) if args.bbox else None
        make_snapshot(args.place, args.out, bbox=bbox)
        print(f"wrote snapshot {args.out}")
    elif args.cmd == "build":
        cfg = SynthConfig(network_id=args.id, display_name=args.name,
                          population=args.population)
        bundle = build_from_snapshot(args.snapshot, cfg)
        out_dir = Path(args.out_root) / args.id
        write_bundle(bundle, out_dir)
        print(f"built {len(bundle.consumers['consumers'])} consumers / "
              f"{len(bundle.pipes['pipes'])} pipes → {out_dir}")


if __name__ == "__main__":
    main()
