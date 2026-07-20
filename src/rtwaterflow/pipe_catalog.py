"""Pipe catalog: DN/material → inner diameter and default integral roughness.

German practice (TECHNICAL_FOUNDATIONS.md §4):

* **Materials** in the network stock: ``PE`` (PE 100, the modern default),
  ``PVC`` (PVC-U), ``GGG`` (duktiles Gusseisen / ductile iron), ``GG``
  (Grauguss / legacy grey cast iron), ``St`` (steel), ``AZ`` (Faserzement /
  asbestos cement, legacy).
* **Roughness** is the *integrale Rauheit* k per DVGW GW 303-1 — an
  operational value lumping joints/fittings, NOT the catalogue wall
  roughness: 0.1 mm (transport mains / new plastics), 0.4 mm
  (Haupt-/Versorgungsleitungen, metallic), 1.0 mm (old unprotected GG/St,
  heavily meshed legacy nets). Defaults below follow the material; an
  explicit per-pipe ``k_mm`` in the bundle always wins (and calibration
  against measurements is the GW 303-1 way — an M9 teaching exercise).
* **Inner diameters**: metallic pipes (GGG/GG/St/AZ) are specified by DN
  with ID ≈ DN. Plastics are specified by OUTER diameter d (PE d110 etc.);
  the catalog maps the d-series to inner diameters for PE 100 SDR 17
  (PN 10 — the standard water-main pressure class) and PVC-U PN 10.
"""
from __future__ import annotations

MATERIALS = ("PE", "PVC", "GGG", "GG", "St", "AZ")

#: material → default integral roughness k [mm] (GW 303-1 practice)
DEFAULT_K_MM: dict[str, float] = {
    "PE": 0.1,    # new plastics, few incrustations
    "PVC": 0.1,
    "GGG": 0.4,   # cement-lined ductile iron mains
    "St": 0.4,
    "AZ": 0.4,
    "GG": 1.0,    # old unprotected grey cast iron
}

#: PE 100 SDR 17 (PN 10): outer diameter d [mm] → inner diameter [mm]
_PE_SDR17_ID: dict[int, float] = {
    25: 21.0, 32: 28.0, 40: 35.2, 50: 44.0, 63: 55.4, 75: 66.0,
    90: 79.2, 110: 96.8, 125: 110.2, 140: 123.4, 160: 141.0,
    180: 158.6, 200: 176.2, 225: 198.2, 250: 220.4, 280: 246.8,
    315: 277.6,
}

#: PVC-U PN 10: outer diameter d [mm] → inner diameter [mm]
_PVC_PN10_ID: dict[int, float] = {
    63: 57.0, 75: 67.8, 90: 81.4, 110: 99.4, 125: 113.0,
    140: 126.6, 160: 144.6, 180: 162.8, 200: 180.8, 225: 203.4,
    250: 226.2, 280: 253.2, 315: 285.0,
}


def inner_diameter_mm(dn: int, material: str) -> float:
    """Resolve the hydraulic inner diameter for a (dn, material) pair.

    Metallic/cement pipes: ID ≈ DN (nominal bore). Plastics: *dn* is the
    d-series OUTER diameter; the SDR/PN tables above give the bore. Raises
    ``KeyError`` for unknown materials or plastic sizes outside the series
    (the loader turns that into a contract error).
    """
    material = str(material)
    if material not in MATERIALS:
        raise KeyError(f"unknown pipe material {material!r} "
                       f"(one of {MATERIALS})")
    if material == "PE":
        if int(dn) not in _PE_SDR17_ID:
            raise KeyError(
                f"PE d{dn} is not in the SDR 17 series "
                f"({sorted(_PE_SDR17_ID)})")
        return _PE_SDR17_ID[int(dn)]
    if material == "PVC":
        if int(dn) not in _PVC_PN10_ID:
            raise KeyError(
                f"PVC d{dn} is not in the PN 10 series "
                f"({sorted(_PVC_PN10_ID)})")
        return _PVC_PN10_ID[int(dn)]
    if dn <= 0:
        raise KeyError(f"DN must be positive, got {dn}")
    return float(dn)


def default_k_mm(material: str) -> float:
    """GW 303-1-style default integral roughness for *material*."""
    if material not in DEFAULT_K_MM:
        raise KeyError(f"unknown pipe material {material!r} "
                       f"(one of {MATERIALS})")
    return DEFAULT_K_MM[material]
