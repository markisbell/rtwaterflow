"""Per-point elevation sampling (M8 geodata pipeline, TF §11).

Elevation is resolved ONCE at snapshot time and frozen into the pinned
snapshot; the tick loop never touches geodata (TF §11). Two providers:

* :class:`OpenTopoDataProvider` — the public OpenTopoData REST API (standard
  HTTPS, works anywhere; EU-DEM 25 m across Europe). The default here because
  hoehendaten.de's German DGM1 API sits on a non-standard port that is often
  firewalled; EU-DEM is coarser (25 m, ~±2 m) but plenty to show terrain and
  Druckzonen for a teaching bundle.
* :class:`RasterDGMProvider` — samples a local **DGM GeoTIFF** (DGM1 1 m /
  DGM200 200 m) with rasterio in its native CRS (EPSG:25832/25833, DHHN2016) —
  the roadmap's primary path when a real Länder DEM tile is on disk.

Both return ``(elevations, attribution)`` so the visible-credit array in the
bundle reflects what was actually sampled (TF §11).
"""
from __future__ import annotations

import json
import math
import time
import urllib.request
from dataclasses import dataclass


@dataclass
class ElevationResult:
    #: osmid → elevation [m a.s.l.]
    elevations: dict[int, float]
    #: visible-credit strings for the DEM source
    attribution: list[str]


class OpenTopoDataProvider:
    """EU-DEM 25 m via the public OpenTopoData API (batched, rate-limited)."""

    URL = "https://api.opentopodata.org/v1/{dataset}"
    ATTRIBUTION = ("Elevation: EU-DEM v1.1 — produced using Copernicus data "
                   "and information funded by the European Union")
    #: OpenTopoData caps a request at 100 locations; public tier ~1 call/s
    BATCH = 100
    PAUSE_S = 1.1

    def __init__(self, dataset: str = "eudem25m"):
        self.dataset = dataset

    def sample(self, points: list[tuple[int, float, float]]) -> ElevationResult:
        """*points* = [(osmid, lat, lon), ...]. Returns frozen elevations."""
        out: dict[int, float] = {}
        for start in range(0, len(points), self.BATCH):
            chunk = points[start:start + self.BATCH]
            locs = "|".join(f"{lat:.6f},{lon:.6f}" for _, lat, lon in chunk)
            url = (self.URL.format(dataset=self.dataset)
                   + "?locations=" + locs + "&interpolation=bilinear")
            if start:
                time.sleep(self.PAUSE_S)     # respect the public rate limit
            with urllib.request.urlopen(url, timeout=60) as resp:
                doc = json.loads(resp.read())
            if doc.get("status") != "OK":
                raise RuntimeError(f"OpenTopoData: {doc.get('error') or doc}")
            for (osmid, _, _), res in zip(chunk, doc["results"]):
                elev = res.get("elevation")
                if elev is None:
                    raise RuntimeError(f"OpenTopoData: null elevation @ {osmid}")
                out[int(osmid)] = float(elev)
        return ElevationResult(elevations=out, attribution=[self.ATTRIBUTION])


class RasterDGMProvider:
    """Sample a local DGM GeoTIFF (rasterio) in its native CRS — TF §11."""

    def __init__(self, geotiff_path: str, attribution: str):
        self.path = geotiff_path
        self.attribution = attribution

    def sample(self, points: list[tuple[int, float, float]]) -> ElevationResult:
        import rasterio
        from pyproj import Transformer

        out: dict[int, float] = {}
        with rasterio.open(self.path) as ds:
            to_dem = Transformer.from_crs("EPSG:4326", ds.crs, always_xy=True)
            xs, ys, ids = [], [], []
            for osmid, lat, lon in points:
                x, y = to_dem.transform(lon, lat)
                xs.append(x)
                ys.append(y)
                ids.append(int(osmid))
            for osmid, (val,) in zip(ids, ds.sample(list(zip(xs, ys)))):
                v = float(val)
                # a point outside the tile / on the DEM's no-data mask must
                # be a LOUD error — never a NaN or 0.0 elevation silently
                # frozen into the bundle. Guard the declared no-data value
                # (may be absent) AND any non-finite sample (M8 review).
                if not math.isfinite(v) or (
                        ds.nodata is not None and v == float(ds.nodata)):
                    raise RuntimeError(
                        f"DGM: no-data / out-of-tile @ osmid {osmid}")
                out[osmid] = v
        return ElevationResult(elevations=out, attribution=[self.attribution])
