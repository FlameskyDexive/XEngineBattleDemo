## 0.1.0 — 2026-08-21 (M0–M12 complete)

- M0 package scaffold + `com.xengine.unityimporter` extraction.
- M1 FBX-first asset import (native mat/controller/prefab generators, event sidecars).
- M2/M3 `Default/Toon` + `Default/ToonOutline` engine shaders (ToonShadingMaterial cbuffer
  registered on VK/D3D12/WebGPU; YSA property mapping; NormalsFix menu).
- M4 `com.xengine.inputsystem`: Unity .inputactions JSON import, PlayerInput,
  `.triggered` compat, interactive rebinding.
- M5 `com.xengine.probuilder`: parametric shapes + ZZZ arena rebuild with static colliders.
- M6 `com.xengine.cinemachine`: Brain/vcam/Transposer/Composer/FreeLook/Collider/Blend.
- M7/M8 player FSM + combat (run gait, turn, dodge, tag switch, 4-hit combo, BigSkill chain).
- M9 particle engine parity (TrailRenderer, stretched billboards, additive pass,
  soft-particle depth fade, SortingFudge, SizeBySpeed, Noise, StartDelay, Emit(n)).
- M10 skill effects (8 authored effects wired to combat trigger points).
- M11 `com.xengine.magicacloth2` P0 bone cloth (verlet chains + wind + sphere colliders).

# Changelog

All notable changes to this package are documented here.

## [0.1.0] - 2026-08-21

### Added

- M0: package scaffold — `package.json` (official optional package, depends on
  `com.xengine.unityimporter`), Runtime/Editor assembly definitions,
  `Documentation~/index.md`, `Samples~/CombatDemo` placeholder, and
  `VerifyBuiltInPackages` editor-build checks.
- M1 (FBX-first): the copier brings in RAW assets only (FBX with `unitScale` from
  the Unity meta + textures with aligned import settings + `unity-guid-map.json` /
  `unity-anim-events.json` sidecars, including Unity 6 nested
  `animations.clipAnimations`); Unity `.mat`/`.controller`/`.prefab` files are never
  imported — `ZonezeroNativeAssets`/`ZonezeroControllerGenerator` read them in place
  as reference and generate NATIVE Echo assets: materials (YSA/Lit →
  Default/Standard, Corin URP Unlit → Default/Unlit, `_MainTex` wired),
  transition-less AnimatorControllers (motions = FBX AnimationClip sub-assets, loop
  flags from the sidecar, code-driven CrossFade), and prefabs built by instantiating
  the engine FBX Model (axis/skinning ground truth) with material slots + Animator
  bindings taken from the Unity reference. FBX meshes are named from scene nodes so
  multi-part characters keep per-part meshes. Runtime `ZonezeroAnimEvents` injects
  baked clip events from the sidecar. *Build Combat Demo Scene* instantiates the four
  generated native prefabs (3 characters + 3 Claymore dummies, all facing the camera).
- Planned (M2-M12, see `docs/superpowers/specs/2026-08-21-zonezero-combat-demo.md`):
  YsaToonLit + YsaOutline shaders, inputsystem/cinemachine/probuilder packages,
  movement + combat demo, particle engine fills and skill VFX, MagicaCloth2 bone cloth.
