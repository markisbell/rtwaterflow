"""Build the Neubeuern (Druckzonen) geodata bundle from its pinned snapshot (M8).

OFFLINE + deterministic: reads the committed snapshot (real OSM streets + EU-DEM
elevations for the hilly Upper-Bavarian market town of Neubeuern, which climbs
~100 m from the Inn valley floor up the Neubeuernberg) and synthesises a
**multi-zone** gravity water network — one gravity zone would drive the deep
streets far above PN 10, so the synthesiser inserts Druckminderer where the
downhill static pressure would exceed the band, splitting the town into real
German Druckzonen (TF §2). Writes data/networks/neubeuern/. No network access —
the snapshot was frozen at build time (tools/bundle_builder). The output is
committed; tests/test_bundle_builder.py asserts byte-stability.

To refresh the snapshot from live data (needs the geo stack, TF §11):
    pip install -r tools/bundle_builder/requirements.txt
    python -m bundle_builder snapshot "Neubeuern, Bayern, Germany" \
        --out tools/bundle_builder/snapshots/neubeuern.json \
        --bbox 47.773 47.759 12.152 12.132
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from bundle_builder.pipeline import build_from_snapshot, write_bundle  # noqa: E402
from bundle_builder.synthesize import SynthConfig                      # noqa: E402

SNAPSHOT = ROOT / "tools" / "bundle_builder" / "snapshots" / "neubeuern.json"
OUT = ROOT / "data" / "networks" / "neubeuern"

#: the pinned config — keep in sync with tests/test_bundle_builder.py::NEUBEUERN_CFG.
#: max_elevation_m=530 drops the 5 forested-summit / Schloss nodes the Gemeinde
#: boundary sweeps in above the settled area (563 m castle down to a 527 m top),
#: leaving a continuous ~78 m hillside town the descent tree zones cleanly.
CFG = SynthConfig(network_id="neubeuern", display_name="Neubeuern (Druckzonen)",
                  population=3000, enable_prv_zoning=True, max_elevation_m=530.0)


def main() -> None:
    bundle = build_from_snapshot(SNAPSHOT, CFG)
    write_bundle(bundle, OUT)
    print(f"built {len(bundle.consumers['consumers'])} consumers / "
          f"{len(bundle.pipes['pipes'])} pipes / "
          f"{len(bundle.supply['prvs'])} Druckminderer -> {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
