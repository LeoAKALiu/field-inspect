# LIRIS Synthetic Tunnel — data provenance

## Files

| Local path | Source URL |
|------------|------------|
| `tunnel_mesh.obj` | https://dataset-dl.liris.cnrs.fr/synthetic-cave-and-tunnel-systems/Tunnel/tunnel_sub_2_no_boundaries_triangulation.obj |
| `tunnel_pointcloud.ply` | https://dataset-dl.liris.cnrs.fr/synthetic-cave-and-tunnel-systems/Tunnel/tunnel_sub_2_no_boundaries_point_cloud.ply |
| `scene-metadata.json` | Generated packaging metadata (transform + camera + point-cloud-derived route) |

## Download

- **Date:** 2026-08-07
- **Dataset:** Synthetic Cave and Tunnel Systems (CNRS / LIRIS)

## SHA-256

```
183a058abba0ddb084752a6afd7a1c4f2c907ba9739244ce4f2388076cc3a186  tunnel_mesh.obj
910ddbcd1e9f16f9cc99db195de8c8f277938305d1d83da4f7626c0390a5c6bc  tunnel_pointcloud.ply
```

## License

**ETALAB Open License 2.0** (Licence Ouverte 2.0)

- https://www.etalab.gouv.fr/wp-content/uploads/2017/04/ETALAB-Licence-Ouverte-v2.0.pdf
- SPDX-style id used in metadata: `etalab-2.0`

### Attribution (required)

> © CNRS / LIRIS — Synthetic Cave and Tunnel Systems dataset.  
> Redistributed under the ETALAB Open License v2.0 with attribution.

## Notes

- Original OBJ/PLY bytes are **not modified**. Viewer alignment uses `scene-metadata.json` → `transformMatrix` only.
- Source frame: right-handed, **Z-up**, meters (Blender export).
- Viewer frame: right-handed, **Y-up**, meters.
- `recommendedRoute` is generated from the original PLY by
  `scripts/generate_pointcloud_route.py`: it automatically identifies a main-corridor
  candidate, estimates robust cross-section centers and checks point-cloud support along
  every segment; it has no manual start/end points.
- The route is an **algorithmic estimate, not a measured or certified navigable path**.
  Its `routeGeneration` block records the algorithm version, point count and source hash.
