# com.xengine.zonezero — Documentation

Zenless-Zone-Zero-style anime (NPR) character rendering and a basic tag-team combat demo
for XEngine. Ported from the private reference project `F:\Git\ZZZ` (Unity 6.7 alpha, URP
17.7, YSA Toon shader family, Cinemachine FreeLook, ~1,850 lines of gameplay). Full plan:
`docs/superpowers/specs/2026-08-21-zonezero-combat-demo.md` (repository root).

## Package layout (as it fills in per milestone)

| Path | Contents |
|---|---|
| `Runtime/Shaders` | `YsaToonLit.shader`, `YsaOutline.shader`, shared include `YsaToonCore` (GLSL+Slang dual source) |
| `Runtime/Combat` | PlayerController / PlayerModel / 16-state FSM / WeaponController / CameraManager / enemy dummies |
| `Runtime/Vfx` | Skill effects: hit sparks, weapon trails, slash arcs, big-skill burst, switch flash, dodge dust |
| `Editor/ZonezeroMenu.cs` | *Zonezero → Copy ZZZ Assets* (raw FBX/textures only) → *Generate Native Assets* → *Build Combat Demo Scene* |
| `Editor/` | FBX-first generators: `ZonezeroAssetCopier`, `ZonezeroNativeAssets`, `ZonezeroControllerGenerator` (Unity YAML is reference-only; native Echo `.mat`/`.controller`/`.prefab`) |
| `Samples~/CombatDemo` | The runnable demo scene |

## Dependencies

- `com.xengine.unityimporter` — shared Unity YAML/GUID-sidecar import layer (M0).
- `com.xengine.inputsystem` — Unity InputSystem-compatible action maps/PlayerInput (M4).
- `com.xengine.cinemachine` — Brain/FreeLook/vcam + blends (M6).
- `com.xengine.probuilder` — arena geometry (M5).
- `com.xengine.magicacloth2` — bone cloth for character skirts (M11).

## Install & run

1. **Window → Package Manager** → *Available* → **Zonezero Toon Combat Demo** → **Install**
   (official but optional; not mounted until installed).
2. Copy the source assets via *Zonezero → Copy ZZZ Assets Into Project* (they are for
   internal testing only and never ship inside the package).
3. *Zonezero → Build Combat Demo Scene*, open `Scenes/ZonezeroCombat.scene`, press
   **Play**: WASD move (FreeLook camera), LMB 4-hit combo, E dodge, Q big skill,
   tab-style character switch.

## Rendering architecture (summary)

YSA Toon is fully analytic (no ramp/SDF textures): banded diffuse
`smoothstep(mid, mid+smooth, NdotL)` for main + additional lights, received-shadow
smoothstep with a floor, half-vector-offset specular (pseudo-anisotropic hair shine),
light-remapped rim, object-space position-based face normals, and a separate
inverted-hull outline material whose width scales with `saturate(|viewZ|)·fov·1e-4`
while the body shrinks by `_Shrink_Size`. See the plan doc §3.1 for the exact math.
