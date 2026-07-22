"""Build the Alpen (Ortskern) geodata bundle from its pinned snapshot (M8).

OFFLINE + deterministic: reads the committed snapshot (real OSM streets + EU-DEM
elevations for the flat Niederrhein town of Alpen), synthesises a branched
gravity water network and writes data/networks/alpen/. No network access — the
snapshot was frozen at build time (tools/bundle_builder). The output is
committed; tests/test_bundle_builder.py asserts byte-stability.

To refresh the snapshot from live data (needs the geo stack, TF §11):
    pip install -r tools/bundle_builder/requirements.txt
    python -m bundle_builder snapshot "Alpen, Nordrhein-Westfalen, Germany" \
        --out tools/bundle_builder/snapshots/alpen.json --bbox 51.586 51.567 6.526 6.500
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from bundle_builder.pipeline import build_from_snapshot, write_bundle  # noqa: E402
from bundle_builder.synthesize import SynthConfig                      # noqa: E402

SNAPSHOT = ROOT / "tools" / "bundle_builder" / "snapshots" / "alpen.json"
OUT = ROOT / "data" / "networks" / "alpen"

#: the pinned config — keep in sync with tests/test_bundle_builder.py::ALPEN_CFG
CFG = SynthConfig(network_id="alpen", display_name="Alpen (Ortskern)",
                  population=5200)


def main() -> None:
    bundle = build_from_snapshot(SNAPSHOT, CFG)
    write_bundle(bundle, OUT)
    print(f"built {len(bundle.consumers['consumers'])} consumers / "
          f"{len(bundle.pipes['pipes'])} pipes → {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
