# Contract Change Request — TwinViewer (post v2 sync)

**From:** packages/twin-viewer  
**Date:** 2026-08-07  
**Status:** residual notes only — **contracts v2 is the source of truth**

## Sync status (this package)

| Item | Status |
|------|--------|
| `@digital-twin/contracts` import | **Done** — workspace dependency |
| `contractsMirror.ts` | **Removed** |
| `SourceType` / `live_pending` | Aligned |
| `DeviceType` (delamination…) | Aligned |
| `source_type` on domain objects | Aligned (passed through; engine does not invent) |
| `TwinViewerProps` / callbacks | Aligned |
| `TwinTrajectoryPoint.time` | Aligned (engine playback clock) |
| `VehiclePose` | Aligned (not full `VehicleState` for pose prop) |

## Residual (engine-only, not requesting freeze break)

### 1. Asset packaging matrix (`transformMatrix`)

- **What:** LIRIS raw OBJ/PLY are Blender Z-up; contracts `SceneMetadata` is already `scene_local_yup` without a transform field.
- **Engine approach:** `TwinViewerOptions.transformMatrix` (optional, identity default). Demo maps packaging JSON → contracts `SceneMetadata` + options matrix.
- **Ask?** Optional future field on a packaging resource — **not** required to change domain `SceneMetadata` freeze.

### 2. Richer pick payload

- **Contracts:** `TwinCoordinatePickEvent = { position }`; `TwinObjectSelectEvent = { id, kind, position }`.
- **Engine:** Internal hit carries `kind` for mesh/point; coordinate callback stays contracts-shaped (position only).
- **Ask?** Only if product needs mesh vs point distinction on coordinate pick — optional additive field later.

### 3. `TwinCameraState` without up/fov

- Contracts expose `position` + `target` only. Engine matches that; FOV is local option.

## Do not change

- Do not reintroduce hand-written domain mirrors in twin-viewer.
- Do not change contracts to restore legacy `live` / `strain` / `vibration` enums.
