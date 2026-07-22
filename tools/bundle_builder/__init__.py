"""Offline geodata bundle builder (M8, roadmap §6, TF §11).

Turns a real German town into an rtwaterflow five-file network bundle:

    OSM street graph (osmnx)  ─┐
                               ├─►  synthesise a gravity water network  ─►  bundle
    DEM elevation per node    ─┘         (tank on the high point,
    (OpenTopoData / DGM tile)            mains along streets, sinks)

The pipeline splits into an ONLINE snapshot step (fetch OSM + sample
elevations, freeze to a pinned JSON) and an OFFLINE, deterministic build step
(snapshot → bundle) so a committed snapshot rebuilds byte-for-byte without any
network access (roadmap M8 acceptance).
"""
