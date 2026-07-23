"""Build the Kevelaer (Stadtkern) geodata bundle from its pinned snapshot (M9).

The city-scale PERFORMANCE bundle (roadmap M9): a ~540-junction real-town network
that warm-solves well under the 100 ms/tick bar. OFFLINE + deterministic: reads
the committed snapshot (real OSM streets + EU-DEM elevations for the compact,
flat centre of the Niederrhein pilgrimage town of Kevelaer) and synthesises a
single-zone gravity network with city-grade ductile-iron mains (GGG DN200/300 —
a village's PE DN110 branches could not carry a town's throughput in-band across
the longer tree). Writes data/networks/kevelaer/. No network access — the
snapshot was frozen at build time (tools/bundle_builder). The output is
committed; tests/test_bundle_builder.py asserts byte-stability.

To refresh the snapshot from live data (needs the geo stack, TF §11):
    pip install -r tools/bundle_builder/requirements.txt
    python -m bundle_builder snapshot "Kevelaer, Nordrhein-Westfalen, Germany" \
        --out tools/bundle_builder/snapshots/kevelaer.json \
        --bbox 51.5999 51.5599 6.2663 6.2263
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from bundle_builder.pipeline import build_from_snapshot, write_bundle  # noqa: E402
from bundle_builder.synthesize import SynthConfig                      # noqa: E402

SNAPSHOT = ROOT / "tools" / "bundle_builder" / "snapshots" / "kevelaer.json"
OUT = ROOT / "data" / "networks" / "kevelaer"

#: the pinned config — keep in sync with tests/test_bundle_builder.py::KEVELAER_CFG.
#: GGG DN200/300 mains + a 45 m head reserve keep the ~540-node single gravity
#: zone in-band (a flat town's low relief gives no gravity margin, so friction
#: over the longer tree must be met by bigger pipe + a taller Hochbehälter).
CFG = SynthConfig(network_id="kevelaer", display_name="Kevelaer (Stadtkern)",
                  population=10000, enable_prv_zoning=False,
                  material="GGG", dn_trunk=300, dn_branch=200,
                  head_reserve_m=45.0)


def main() -> None:
    bundle = build_from_snapshot(SNAPSHOT, CFG)
    write_bundle(bundle, OUT)
    print(f"built {len(bundle.network_structure['junctions'])} junctions / "
          f"{len(bundle.pipes['pipes'])} pipes / "
          f"{len(bundle.consumers['consumers'])} consumers -> "
          f"{OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
