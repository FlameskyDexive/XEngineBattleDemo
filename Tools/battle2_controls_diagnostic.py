#!/usr/bin/env python3
"""Deterministic Battle2 controls and combat-animation acceptance through editor MCP.

The probe launches an editor process that it owns, forces ``Scenes/ZonezeroBattle2.scene``,
and starts a fresh Play session for each direction and action.  Input is driven through the
engine's test-only ``InputInjector`` and no scene changes are persisted.  The report records both
controller/root motion and the actual skinned-renderer roots/bounds visible at action keyframes;
the three I-skill phases are paused and captured independently.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from battle2_skinning_diagnostic import (
    CLOSEUP_CAMERA_TEMPLATE,
    EditorMcp as BaseEditorMcp,
    RpcResponse,
    capture,
    fatal_runtime_logs,
    git_sha,
    response_value,
    runtime_log_entries,
    wait_until_ready,
)


def _decode_mcp_response_line(text: str) -> dict[str, Any] | None:
    """A complete MCP JSON object can share its trailing newline with editor log output."""
    text = text.lstrip()
    if not text.startswith("{"):
        return None
    try:
        message, end = json.JSONDecoder().raw_decode(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid/truncated MCP JSON at character {exc.pos}: {exc.msg}") from exc
    if not isinstance(message, dict) or "jsonrpc" not in message:
        return None
    if message["jsonrpc"] != "2.0":
        raise ValueError("unsupported JSON-RPC version in MCP output")
    if "id" not in message:
        if "result" in message or "error" in message:
            raise ValueError("MCP response is missing its request ID")
        return None  # Server notifications are not responses to a pending request.
    if type(message["id"]) is not int or (("result" in message) == ("error" in message)):
        raise ValueError("invalid MCP response ID or result/error envelope")
    suffix = text[end:].strip()
    if suffix and not re.fullmatch(r"VAO: \[ID -?\d+\] Mesh uploaded successfully to VRAM \(GPU\)", suffix):
        raise ValueError(f"unexpected trailing data after MCP JSON: {suffix[:120]!r}")
    return message


class EditorMcp(BaseEditorMcp):
    """Keep interleaved trailing editor logs from discarding a completed RPC response."""

    def __init__(self, *args: Any, **kwargs: Any):
        self._protocol_error: str | None = None
        super().__init__(*args, **kwargs)

    def _drain(self) -> None:
        assert self._process.stdout is not None
        for raw in self._process.stdout:
            self._log.write(raw)
            self._log.flush()
            try:
                message = _decode_mcp_response_line(raw.decode("utf-8", errors="replace"))
            except ValueError as exc:
                with self._lock:
                    self._protocol_error = str(exc)
                continue
            if message is not None:
                with self._lock:
                    self._responses[message["id"]] = message

    def request(self, method: str, params: dict[str, Any], timeout: float = 300.0) -> RpcResponse:
        request_id = self._send(method, params)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._process.poll() is not None:
                raise RuntimeError(f"editor exited with code {self._process.returncode}")
            with self._lock:
                protocol_error = self._protocol_error
                response = self._responses.pop(request_id, None)
            if protocol_error:
                raise RuntimeError(f"MCP transport corruption: {protocol_error}")
            if response is not None:
                if "error" in response:
                    raise RuntimeError(json.dumps(response["error"], ensure_ascii=False))
                return RpcResponse(response)
            time.sleep(0.05)
        raise TimeoutError(f"MCP request {request_id} timed out: {method}/{params.get('name', '')}")


SCENE_PATH = "Scenes/ZonezeroBattle2.scene"
EXPECTED_HELPER_SIGNATURE = (
    "internal static Float3 ResolveMoveDirection(Float2 input, float cameraYawDeg)"
)
KEY_INPUTS: dict[str, tuple[float, float]] = {
    "W": (0.0, 1.0),
    "S": (0.0, -1.0),
    "A": (-1.0, 0.0),
    "D": (1.0, 0.0),
}
ACTION_STATES: dict[str, tuple[str, ...]] = {
    "J": ("Attack_Normal_1",),
    "K": ("Attack_Normal_4",),
    "L": ("Evade_Front",),
    "I": ("BigSkill_Start", "BigSkill", "BigSkill_End"),
}
I_ACTION_PHASES: tuple[tuple[str, str], ...] = (
    ("Start", "BigSkill_Start"),
    ("Body", "BigSkill"),
    ("End", "BigSkill_End"),
)
ISOLATED_EVALUATION_MODE = "isolatedControlledAnimatorEvaluation"
ISOLATED_NORMALIZED_STEP = 0.025
ISOLATED_NORMALIZED_TOLERANCE = 0.0001

# The scene deliberately has one player controller and two AI-controlled allies.  Runtime
# acceptance must address the three actor roots directly; looking up HeroCombatController only
# ever exercises Anbi and silently omits the two rigs that originally reproduced T-pose/fly-off.
ACTOR_STATES: dict[str, dict[str, Any]] = {
    "Anbi": {
        "root": "Battle_Hero",
        "states": (
            "Idle",
            "Run",
            "Attack_Normal_1",
            "Attack_Normal_1_End",
            "Attack_Normal_2",
            "Attack_Normal_2_End",
            "Attack_Normal_3",
            "Attack_Normal_3_End",
            "Attack_Normal_4",
            "Attack_Normal_4_End",
            "Evade_Front",
            "Evade_Front_End",
            "BigSkill_Start",
            "BigSkill",
            "BigSkill_End",
        ),
    },
    "Corin": {
        "root": "Battle_Ally_Corin",
        "states": (
            "Idle",
            "Run",
            "Attack_Normal_1",
            "Attack_Normal_1_End",
            "Attack_Normal_2",
            "Attack_Normal_2_End",
            "Attack_Normal_3",
            "Attack_Normal_3_End",
            "Attack_Normal_4",
            "Attack_Normal_4_End",
            "Attack_Normal_5",
            "Attack_Normal_5_End",
            "Evade_Front",
            "Evade_Front_End",
            "BigSkill_Start",
            "BigSkill",
            "BigSkill_End",
        ),
    },
    # The authored controller is named Nike while the model/root is the Nicole/Nostradamus actor.
    "Nicole": {
        "root": "Battle_Ally_Nike",
        "states": (
            "Idle",
            "Run",
            "Attack_Normal_1",
            "Attack_Normal_1_End",
            "Attack_Normal_2",
            "Attack_Normal_2_End",
            "Attack_Normal_3",
            "Attack_Normal_3_End",
            "Evade_Front",
            "Evade_Front_End",
            "BigSkill_Start",
            "BigSkill",
            "BigSkill_End",
        ),
    },
}

CORE_POSE_BONES: tuple[str, ...] = (
    "Bip001 Pelvis",
    "Bip001 Spine",
    "Bip001 L UpperArm",
    "Bip001 R UpperArm",
    "Bip001 L Forearm",
    "Bip001 R Forearm",
    "Bip001 L Thigh",
    "Bip001 R Thigh",
    "Bip001 L Calf",
    "Bip001 R Calf",
)

EXPECTED_LUNGE_SPEED = 7.5
EXPECTED_LUNGE_DURATION = 0.38
EXPECTED_LUNGE_DISTANCE = EXPECTED_LUNGE_SPEED * EXPECTED_LUNGE_DURATION

THRESHOLDS = {
    "movementDirectionDotMin": 0.98,
    "secondaryAxisRatioMax": 0.05,
    # Turning is intentionally smoothed.  This gate rejects backwards-facing movement without
    # pretending a short probe must have converged to the final heading already.
    "locomotionHeroForwardDotMin": 0.75,
    # Once the gameplay root has completed its turn, allow up to 60 degrees of run-pose
    # pelvis/torso twist, while rejecting a model that anatomically faces backward.
    "locomotionAnatomicalForwardDotMin": 0.5,
    "locomotionFacingRootSettledDotMin": 0.98,
    "locomotionFacingStableFrameCount": 3,
    "lungeHeroForwardDotMin": 0.98,
    "cameraYawErrorDegMaxExclusive": 0.5,
    "cameraYawDriftDegMaxExclusive": 0.5,
    "minimumHorizontalTravel": 0.05,
    "minimumLateralStressTravel": 0.05,
    "nonLungeRootTravelMax": 0.03,
    "nonLungeFrameTeleportMax": 0.02,
    "lungeTravelAllowance": 0.05,
    "lungeFrameAllowance": 0.02,
    "travelBoneLocalExcursionMax": 0.5,
    "primaryBodyBoundsHorizontalOffsetMax": 1.5,
    "rendererBoundsHorizontalOffsetMax": 3.0,
    "visualMovementDirectionDotMin": 0.75,
    "visualRelativeOffsetExcursionMax": 0.75,
    "keyBoneWorldDistanceFromActorMax": 3.0,
    "keyBoneLocalReferenceExcursionMax": 1.5,
    "primaryBodyBoundsWorldOffsetMax": 3.0,
    "primaryBodyBoundsExtentMax": 6.0,
    "nonLungeRequestedPathMax": 0.0001,
    "lungeRequestedDistanceTolerance": 0.0001,
    "lungeActualPathAllowance": 0.03,
    "lungeActualDistanceTolerance": 0.08,
    "cameraPitchDriftDegMaxExclusive": 0.5,
    "cameraUpDriftDegMaxExclusive": 0.5,
    "poseNormalisedAdvanceMin": 0.45,
    "poseReferenceAngleMinDeg": 2.0,
    "poseDynamicAngleMinDeg": 0.5,
}


READY_CODE = r'''
int heroes = 0, rigs = 0, heroControllers = 0;
foreach (var root in Scene.Current.RootObjects)
{
    foreach (var component in root.GetComponentsInChildren<XEngine.Runtime.MonoBehaviour>(true, true))
    {
        if (!component.EnabledInHierarchy) continue;
        string fullName = component.GetType().FullName ?? component.GetType().Name;
        if (fullName == "XEngine.Zonezero.Combat.HeroCombatController")
        {
            heroControllers++;
            if (component.GameObject.GetComponent<XEngine.Runtime.CharacterController>() != null)
                heroes++;
        }
        else if (fullName == "XEngine.Zonezero.Combat.BattleFollowCamera") rigs++;
    }
}
return "READY|" + heroes + "|" + rigs + "|" + heroControllers;
'''


PREFLIGHT_CODE = r'''
var sb = new System.Text.StringBuilder();
int heroes = 0, rigs = 0, heroCharacterControllers = 0, forbidden = 0;
bool helperAvailable = false;
string helperDescription = "<missing>";
System.Type? heroControllerType = null;
var flags = System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic
    | System.Reflection.BindingFlags.Static;
foreach (var root in Scene.Current.RootObjects)
{
    foreach (var component in root.GetComponentsInChildren<XEngine.Runtime.MonoBehaviour>(true, true))
    {
        if (!component.EnabledInHierarchy) continue;
        var type = component.GetType();
        string fullName = type.FullName ?? type.Name;
        string name = type.Name;
        if (fullName == "XEngine.Zonezero.Combat.HeroCombatController")
        {
            heroControllerType = type;
            heroes++;
            if (component.GameObject.GetComponent<XEngine.Runtime.CharacterController>() != null)
                heroCharacterControllers++;
        }
        if (fullName == "XEngine.Zonezero.Combat.BattleFollowCamera") rigs++;
        bool prohibited = fullName.Contains("Cinemachine", System.StringComparison.OrdinalIgnoreCase)
            || fullName.Contains("FreeLook", System.StringComparison.OrdinalIgnoreCase)
            || name.Equals("PlayerController", System.StringComparison.OrdinalIgnoreCase)
            || name.Equals("ZonezeroFreeLookDriver", System.StringComparison.OrdinalIgnoreCase);
        if (prohibited)
        {
            forbidden++;
            sb.Append("FORBIDDEN|").Append(root.Name).Append('|')
              .Append(component.GameObject.Name).Append('|').Append(fullName).Append('\n');
        }
    }
}
if (heroControllerType != null)
foreach (var method in heroControllerType.GetMethods(flags))
{
    if (method.Name != "ResolveMoveDirection" || method.ReturnType != typeof(XEngine.Vector.Float3))
        continue;
    var parameters = method.GetParameters();
    if (parameters.Length == 2 && parameters[0].ParameterType == typeof(XEngine.Vector.Float2)
        && parameters[1].ParameterType == typeof(float))
    {
        helperAvailable = true;
        helperDescription = method.ToString();
        break;
    }
}
sb.Insert(0, "PREFLIGHT|" + heroes + "|" + rigs + "|" + heroCharacterControllers + "|"
    + forbidden + "|" + (helperAvailable ? "1" : "0") + "|" + helperDescription + "\n");
return sb.ToString();
'''


PRODUCTION_STATE_CODE = r'''
XEngine.Runtime.MonoBehaviour? controller = null;
XEngine.Runtime.MonoBehaviour? rig = null;
foreach (var root in Scene.Current.RootObjects)
{
    foreach (var candidate in root.GetComponentsInChildren<XEngine.Runtime.MonoBehaviour>(true, true))
    {
        string fullName = candidate.GetType().FullName ?? candidate.GetType().Name;
        if (fullName == "XEngine.Zonezero.Combat.HeroCombatController" && candidate.EnabledInHierarchy)
            controller = candidate;
        else if (fullName == "XEngine.Zonezero.Combat.BattleFollowCamera" && candidate.EnabledInHierarchy)
            rig = candidate;
    }
    if (controller != null && rig != null) break;
}
if (controller == null || rig == null) return "ERROR|missing hero controller or camera rig";
var hero = controller.GameObject;
var cc = hero.GetComponent<XEngine.Runtime.CharacterController>();
if (cc == null) return "ERROR|hero CharacterController missing";

var instanceNonPublic = System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.NonPublic;
var instancePublic = System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.Public;
var moveAction = controller.GetType().GetField("_move", instanceNonPublic)?.GetValue(controller);
var currentValue = moveAction?.GetType().GetField("_currentValue", instanceNonPublic)?.GetValue(moveAction);
var input = currentValue is XEngine.Vector.Float2 inputValue ? inputValue : default;
float yawDeg = (float)(rig.GetType().GetField("YawDeg", instancePublic)?.GetValue(rig) ?? 0f);
var wish = default(XEngine.Vector.Float3);
bool helperAvailable = false;
var staticFlags = System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic
    | System.Reflection.BindingFlags.Static;
foreach (var method in controller.GetType().GetMethods(staticFlags))
{
    if (method.Name != "ResolveMoveDirection" || method.ReturnType != typeof(XEngine.Vector.Float3))
        continue;
    var parameters = method.GetParameters();
    if (parameters.Length != 2 || parameters[0].ParameterType != typeof(XEngine.Vector.Float2)
        || parameters[1].ParameterType != typeof(float)) continue;
    var reflected = method.Invoke(null, new object[] { input, yawDeg });
    if (reflected is XEngine.Vector.Float3 direction)
    {
        wish = direction;
        helperAvailable = true;
    }
    break;
}
if (!helperAvailable)
{
    float yawRad = yawDeg * System.MathF.PI / 180f;
    var cameraForward = new XEngine.Vector.Float3(System.MathF.Sin(yawRad), 0f, System.MathF.Cos(yawRad));
    var cameraRight = new XEngine.Vector.Float3(System.MathF.Cos(yawRad), 0f, -System.MathF.Sin(yawRad));
    wish = cameraForward * input.Y + cameraRight * input.X;
}

var requestedMotion = (XEngine.Vector.Float3)(typeof(XEngine.Runtime.CharacterController)
    .GetField("lastVelocity", instanceNonPublic)?.GetValue(cc) ?? default(XEngine.Vector.Float3));
var animator = hero.GetComponent<XEngine.Runtime.Animator>();
XEngine.Vector.Transform? travelBone = null;
string travelBonePath = "<missing>";
if (animator?.Skeleton != null)
{
    var skeleton = animator.Skeleton;
    for (int i = 0; i < skeleton.Bones.Length; i++)
    {
        var bone = skeleton.Bones[i];
        string path = i < skeleton.Paths.Length ? skeleton.Paths[i] : "";
        if (bone == null) continue;
        if (bone.GameObject.Name.Equals("Bip001", System.StringComparison.OrdinalIgnoreCase)
            || path.EndsWith("/Bip001", System.StringComparison.OrdinalIgnoreCase))
        {
            travelBone = bone;
            travelBonePath = path;
            break;
        }
    }
    if (travelBone == null)
    {
        var resolveRoot = typeof(XEngine.Runtime.Animator).GetMethod(
            "ResolveRootBone", instanceNonPublic);
        travelBone = resolveRoot?.Invoke(animator, null) as XEngine.Vector.Transform;
        if (travelBone != null) travelBonePath = "<resolved-root>";
    }
}
var boneWorld = travelBone?.Position ?? default;
var boneLocal = travelBone?.LocalPosition ?? default;
string action = controller.GetType().GetProperty("ActiveAction", instancePublic)?.GetValue(controller)?.ToString()
    ?? "<missing>";
string clip = animator?.CurrentClip?.Name ?? "<null>";
var rootPosition = hero.Transform.Position;
var heroForward = hero.Transform.Forward;
var cameraForward = rig.Transform.Forward;
var inv = System.Globalization.CultureInfo.InvariantCulture;
return "STATE|" + input.X.ToString("R", inv) + "|" + input.Y.ToString("R", inv) + "|"
    + wish.X.ToString("R", inv) + "|" + wish.Z.ToString("R", inv) + "|"
    + rootPosition.X.ToString("R", inv) + "|" + rootPosition.Y.ToString("R", inv) + "|"
    + rootPosition.Z.ToString("R", inv) + "|" + heroForward.X.ToString("R", inv) + "|"
    + heroForward.Z.ToString("R", inv) + "|" + requestedMotion.X.ToString("R", inv) + "|"
    + requestedMotion.Y.ToString("R", inv) + "|" + requestedMotion.Z.ToString("R", inv) + "|"
    + ((int)cc.collisionFlags).ToString(inv) + "|" + travelBonePath + "|"
    + boneWorld.X.ToString("R", inv) + "|" + boneWorld.Y.ToString("R", inv) + "|"
    + boneWorld.Z.ToString("R", inv) + "|" + boneLocal.X.ToString("R", inv) + "|"
    + boneLocal.Y.ToString("R", inv) + "|" + boneLocal.Z.ToString("R", inv) + "|"
    + yawDeg.ToString("R", inv) + "|" + cameraForward.X.ToString("R", inv) + "|"
    + cameraForward.Y.ToString("R", inv) + "|" + cameraForward.Z.ToString("R", inv) + "|"
    + action + "|" + clip;
'''


PRESS_KEY_TEMPLATE = r'''
XEngine.Runtime.InputInjector.Press(XEngine.Runtime.KeyCode.__KEY__);
return "pressed:__KEY__";
'''


RELEASE_KEY_TEMPLATE = r'''
XEngine.Runtime.InputInjector.Release(XEngine.Runtime.KeyCode.__KEY__);
return "released:__KEY__";
'''


PRODUCTION_COROUTINE_TEMPLATE = r'''
string storageKey = "__STORAGE_KEY__";
XEngine.Runtime.MonoBehaviour? controller = null;
XEngine.Runtime.MonoBehaviour? rig = null;
foreach (var root in Scene.Current.RootObjects)
{
    foreach (var candidate in root.GetComponentsInChildren<XEngine.Runtime.MonoBehaviour>(true, true))
    {
        string fullName = candidate.GetType().FullName ?? candidate.GetType().Name;
        if (fullName == "XEngine.Zonezero.Combat.HeroCombatController" && candidate.EnabledInHierarchy)
            controller = candidate;
        else if (fullName == "XEngine.Zonezero.Combat.BattleFollowCamera" && candidate.EnabledInHierarchy)
            rig = candidate;
    }
    if (controller != null && rig != null) break;
}
if (controller == null || rig == null) return "ERROR|missing hero controller or camera rig";
var hero = controller.GameObject;
var cc = hero.GetComponent<XEngine.Runtime.CharacterController>();
if (cc == null) return "ERROR|hero CharacterController missing";
var animator = hero.GetComponent<XEngine.Runtime.Animator>();
if (animator?.Skeleton == null) return "ERROR|hero Animator/Skeleton missing";
var instanceNonPublic = System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.NonPublic;
var instancePublic = System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.Public;
var staticFlags = System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic
    | System.Reflection.BindingFlags.Static;
var moveField = controller.GetType().GetField("_move", instanceNonPublic);
var yawField = rig.GetType().GetField("YawDeg", instancePublic);
var pitchField = rig.GetType().GetField("PitchDeg", instancePublic);
var lastVelocityField = typeof(XEngine.Runtime.CharacterController).GetField("lastVelocity", instanceNonPublic);
if (moveField == null || yawField == null || pitchField == null || lastVelocityField == null)
    return "ERROR|required locomotion/camera reflection contract missing";
System.Reflection.MethodInfo? directionHelper = null;
foreach (var method in controller.GetType().GetMethods(staticFlags))
{
    if (method.Name != "ResolveMoveDirection" || method.ReturnType != typeof(XEngine.Vector.Float3))
        continue;
    var parameters = method.GetParameters();
    if (parameters.Length == 2 && parameters[0].ParameterType == typeof(XEngine.Vector.Float2)
        && parameters[1].ParameterType == typeof(float))
    {
        directionHelper = method;
        break;
    }
}
XEngine.Vector.Transform? travelBone = null;
XEngine.Vector.Transform? pelvisBone = null;
string travelBonePath = "<missing>";
string pelvisBonePath = "<missing>";
var skeleton = animator.Skeleton;
for (int i = 0; i < skeleton.Bones.Length; i++)
{
    var bone = skeleton.Bones[i];
    string path = i < skeleton.Paths.Length ? skeleton.Paths[i] : "";
    if (bone == null) continue;
    if (bone.GameObject.Name.Equals("Bip001", System.StringComparison.OrdinalIgnoreCase)
        || path.EndsWith("/Bip001", System.StringComparison.OrdinalIgnoreCase))
    {
        travelBone = bone;
        travelBonePath = path;
    }
    else if (bone.GameObject.Name.Equals("Bip001 Pelvis", System.StringComparison.OrdinalIgnoreCase)
        || path.EndsWith("/Bip001 Pelvis", System.StringComparison.OrdinalIgnoreCase))
    {
        pelvisBone = bone;
        pelvisBonePath = path;
    }
}
if (travelBone == null || pelvisBone == null) return "ERROR|hero Bip001/Pelvis missing";
var primaryRenderers = new System.Collections.Generic.List<XEngine.Runtime.SkinnedMeshRenderer>();
foreach (var renderer in hero.GetComponentsInChildren<XEngine.Runtime.SkinnedMeshRenderer>(true, true))
{
    if (!renderer.EnabledInHierarchy) continue;
    string rootPath = (renderer.RootBonePath ?? "").Replace('\\', '/');
    if (rootPath.EndsWith("/Bip001 Pelvis", System.StringComparison.OrdinalIgnoreCase))
        primaryRenderers.Add(renderer);
}
if (primaryRenderers.Count == 0) return "ERROR|hero primary body renderer missing";
var boundsField = typeof(XEngine.Runtime.SkinnedMeshRenderer).GetField(
    "_cachedBounds", instanceNonPublic);
if (boundsField == null) return "ERROR|SkinnedMeshRenderer._cachedBounds reflection contract missing";
var samples = new System.Collections.Concurrent.ConcurrentQueue<string>();
System.AppDomain.CurrentDomain.SetData(storageKey, samples);
__VISUAL_OBSERVER_FACTORY__
var observeVisual = CreateVisualObserver(hero, samples);

void CombinedPrimaryBounds(out XEngine.Vector.Float3 center, out XEngine.Vector.Float3 size)
{
    float minX = float.MaxValue, minY = float.MaxValue, minZ = float.MaxValue;
    float maxX = float.MinValue, maxY = float.MinValue, maxZ = float.MinValue;
    bool any = false;
    for (int rendererIndex = 0; rendererIndex < primaryRenderers.Count; rendererIndex++)
    {
        object? raw = boundsField.GetValue(primaryRenderers[rendererIndex]);
        if (raw is not XEngine.Vector.AABB bounds) continue;
        var boundsCenter = bounds.Center;
        var half = bounds.Size * 0.5f;
        var minimum = boundsCenter - half;
        var maximum = boundsCenter + half;
        minX = System.MathF.Min(minX, minimum.X);
        minY = System.MathF.Min(minY, minimum.Y);
        minZ = System.MathF.Min(minZ, minimum.Z);
        maxX = System.MathF.Max(maxX, maximum.X);
        maxY = System.MathF.Max(maxY, maximum.Y);
        maxZ = System.MathF.Max(maxZ, maximum.Z);
        any = true;
    }
    if (!any)
    {
        center = new XEngine.Vector.Float3(float.NaN);
        size = new XEngine.Vector.Float3(float.NaN);
        return;
    }
    center = new XEngine.Vector.Float3(
        (minX + maxX) * 0.5f, (minY + maxY) * 0.5f, (minZ + maxZ) * 0.5f);
    size = new XEngine.Vector.Float3(maxX - minX, maxY - minY, maxZ - minZ);
}

string Snapshot(int frame)
{
    var moveAction = moveField?.GetValue(controller);
    var currentValue = moveAction?.GetType().GetField("_currentValue", instanceNonPublic)?.GetValue(moveAction);
    var input = currentValue is XEngine.Vector.Float2 inputValue ? inputValue : default;
    float yawDeg = (float)(yawField.GetValue(rig) ?? 0f);
    float pitchDeg = (float)(pitchField.GetValue(rig) ?? 0f);
    var wish = default(XEngine.Vector.Float3);
    if (directionHelper != null)
    {
        var reflected = directionHelper.Invoke(null, new object[] { input, yawDeg });
        if (reflected is XEngine.Vector.Float3 direction) wish = direction;
    }
    else
    {
        float yawRad = yawDeg * System.MathF.PI / 180f;
        var cameraForward = new XEngine.Vector.Float3(System.MathF.Sin(yawRad), 0f, System.MathF.Cos(yawRad));
        var cameraRight = new XEngine.Vector.Float3(System.MathF.Cos(yawRad), 0f, -System.MathF.Sin(yawRad));
        wish = cameraForward * input.Y + cameraRight * input.X;
    }
    var requestedMotion = (XEngine.Vector.Float3)(lastVelocityField.GetValue(cc)
        ?? default(XEngine.Vector.Float3));
    var rootPosition = hero.Transform.Position;
    var heroForward = hero.Transform.Forward;
    var boneWorld = travelBone?.Position ?? default;
    var boneLocal = travelBone?.LocalPosition ?? default;
    var pelvisWorld = pelvisBone.Position;
    var pelvisLocal = pelvisBone.LocalPosition;
    var cameraForwardNow = rig.Transform.Forward;
    var cameraUpNow = rig.Transform.Up;
    CombinedPrimaryBounds(out var boundsCenter, out var boundsSize);
    string action = controller.GetType().GetProperty("ActiveAction", instancePublic)?.GetValue(controller)?.ToString()
        ?? "<missing>";
    string clip = animator?.CurrentClip?.Name ?? "<null>";
    var inv = System.Globalization.CultureInfo.InvariantCulture;
    return "FRAME|" + frame + "|" + input.X.ToString("R", inv) + "|" + input.Y.ToString("R", inv) + "|"
        + wish.X.ToString("R", inv) + "|" + wish.Z.ToString("R", inv) + "|"
        + rootPosition.X.ToString("R", inv) + "|" + rootPosition.Y.ToString("R", inv) + "|"
        + rootPosition.Z.ToString("R", inv) + "|" + heroForward.X.ToString("R", inv) + "|"
        + heroForward.Z.ToString("R", inv) + "|" + requestedMotion.X.ToString("R", inv) + "|"
        + requestedMotion.Y.ToString("R", inv) + "|" + requestedMotion.Z.ToString("R", inv) + "|"
        + ((int)cc.collisionFlags).ToString(inv) + "|" + travelBonePath + "|"
        + boneWorld.X.ToString("R", inv) + "|" + boneWorld.Y.ToString("R", inv) + "|"
        + boneWorld.Z.ToString("R", inv) + "|" + boneLocal.X.ToString("R", inv) + "|"
        + boneLocal.Y.ToString("R", inv) + "|" + boneLocal.Z.ToString("R", inv) + "|"
        + yawDeg.ToString("R", inv) + "|" + cameraForwardNow.X.ToString("R", inv) + "|"
        + cameraForwardNow.Y.ToString("R", inv) + "|" + cameraForwardNow.Z.ToString("R", inv) + "|"
        + action + "|" + clip + "|" + pelvisBonePath + "|"
        + pelvisWorld.X.ToString("R", inv) + "|" + pelvisWorld.Y.ToString("R", inv) + "|"
        + pelvisWorld.Z.ToString("R", inv) + "|" + pelvisLocal.X.ToString("R", inv) + "|"
        + pelvisLocal.Y.ToString("R", inv) + "|" + pelvisLocal.Z.ToString("R", inv) + "|"
        + primaryRenderers.Count + "|" + boundsCenter.X.ToString("R", inv) + "|"
        + boundsCenter.Y.ToString("R", inv) + "|" + boundsCenter.Z.ToString("R", inv) + "|"
        + boundsSize.X.ToString("R", inv) + "|" + boundsSize.Y.ToString("R", inv) + "|"
        + boundsSize.Z.ToString("R", inv) + "|" + pitchDeg.ToString("R", inv) + "|"
        + cameraUpNow.X.ToString("R", inv) + "|" + cameraUpNow.Y.ToString("R", inv) + "|"
        + cameraUpNow.Z.ToString("R", inv);
}

async XEngine.Async.XTaskVoid RunProbe()
{
    samples.Enqueue(Snapshot(0));
    observeVisual(0);
    XEngine.Runtime.InputInjector.Press(XEngine.Runtime.KeyCode.__KEY__);
    try
    {
        for (int frame = 1; frame <= __FRAME_COUNT__; frame++)
        {
            do { await XEngine.Async.XTask.NextFrame(XEngine.Async.FrameTiming.EndOfFrame); } while (XEngine.Runtime.Application.IsPaused);
            samples.Enqueue(Snapshot(frame));
            observeVisual(frame);
        }
    }
    catch (System.Exception ex)
    {
        samples.Enqueue("ERROR|" + ex.GetType().Name + ":" + ex.Message);
    }
    finally
    {
        XEngine.Runtime.InputInjector.Release(XEngine.Runtime.KeyCode.__KEY__);
        XEngine.Runtime.Application.IsPaused = true;
        samples.Enqueue("DONE");
    }
}
RunProbe().Forget();
return "STARTED|__KEY__|" + storageKey;
'''


READ_PRODUCTION_SAMPLES_TEMPLATE = r'''
var samples = System.AppDomain.CurrentDomain.GetData("__STORAGE_KEY__")
    as System.Collections.Concurrent.ConcurrentQueue<string>;
if (samples == null) return "MISSING";
var page = new System.Text.StringBuilder(32768);
int count = 0;
while (count < 256 && page.Length < 32768 && samples.TryDequeue(out var line))
{
    page.Append(line).Append('\n');
    count++;
}
return page.ToString();
'''


ACTION_READY_TEMPLATE = r'''
var states = new[] { __STATES__ };
XEngine.Runtime.Animator? animator = null;
foreach (var root in Scene.Current.RootObjects)
{
    foreach (var candidate in root.GetComponentsInChildren<XEngine.Runtime.MonoBehaviour>(true, true))
    {
        string fullName = candidate.GetType().FullName ?? candidate.GetType().Name;
        if (fullName == "XEngine.Zonezero.Combat.HeroCombatController" && candidate.EnabledInHierarchy)
        {
            animator = candidate.GameObject.GetComponent<XEngine.Runtime.Animator>();
            break;
        }
    }
    if (animator != null) break;
}
if (animator == null) return "READY|0|no-animator";
foreach (string state in states)
    if (!animator.HasState(state)) return "READY|0|" + state;
return "READY|1|all";
'''


ACTION_COROUTINE_TEMPLATE = r'''
string storageKey = "__STORAGE_KEY__";
XEngine.Runtime.MonoBehaviour? controller = null;
XEngine.Runtime.MonoBehaviour? rig = null;
foreach (var root in Scene.Current.RootObjects)
{
    foreach (var candidate in root.GetComponentsInChildren<XEngine.Runtime.MonoBehaviour>(true, true))
    {
        string fullName = candidate.GetType().FullName ?? candidate.GetType().Name;
        if (fullName == "XEngine.Zonezero.Combat.HeroCombatController" && candidate.EnabledInHierarchy)
            controller = candidate;
        else if (fullName == "XEngine.Zonezero.Combat.BattleFollowCamera" && candidate.EnabledInHierarchy)
            rig = candidate;
    }
    if (controller != null && rig != null) break;
}
if (controller == null || rig == null) return "ERROR|missing hero controller or camera rig";
var hero = controller.GameObject;
var cc = hero.GetComponent<XEngine.Runtime.CharacterController>();
var animator = hero.GetComponent<XEngine.Runtime.Animator>();
if (animator?.Skeleton == null) return "ERROR|hero Animator/Skeleton missing";
if (cc == null || animator == null) return "ERROR|hero CharacterController or Animator missing";
var instanceNonPublic = System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.NonPublic;
var instancePublic = System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.Public;
var lastVelocityField = typeof(XEngine.Runtime.CharacterController).GetField("lastVelocity", instanceNonPublic);
var actionProperty = controller.GetType().GetProperty("ActiveAction", instancePublic);
var lockedTargetField = controller.GetType().GetField("_lockedTarget", instanceNonPublic);
var lungeRemainingField = controller.GetType().GetField("_lungeRemaining", instanceNonPublic);
var lungeDirectionField = controller.GetType().GetField("_lungeDirection", instanceNonPublic);
var lungeSpeedField = controller.GetType().GetField("SkillLungeSpeed", instancePublic);
var lungeDurationField = controller.GetType().GetField("SkillLungeDuration", instancePublic);
if (lastVelocityField == null || actionProperty == null || lockedTargetField == null
    || lungeRemainingField == null || lungeDirectionField == null || lungeSpeedField == null
    || lungeDurationField == null)
    return "ERROR|required action reflection contract missing";
XEngine.Vector.Transform? travelBone = null;
string travelBonePath = "<missing>";
if (animator.Skeleton != null)
{
    var skeleton = animator.Skeleton;
    for (int i = 0; i < skeleton.Bones.Length; i++)
    {
        var bone = skeleton.Bones[i];
        string path = i < skeleton.Paths.Length ? skeleton.Paths[i] : "";
        if (bone == null) continue;
        if (bone.GameObject.Name.Equals("Bip001", System.StringComparison.OrdinalIgnoreCase)
            || path.EndsWith("/Bip001", System.StringComparison.OrdinalIgnoreCase))
        {
            travelBone = bone;
            travelBonePath = path;
            break;
        }
    }
    if (travelBone == null)
    {
        var resolveRoot = typeof(XEngine.Runtime.Animator).GetMethod("ResolveRootBone", instanceNonPublic);
        travelBone = resolveRoot?.Invoke(animator, null) as XEngine.Vector.Transform;
        if (travelBone != null) travelBonePath = "<resolved-root>";
    }
}
var samples = new System.Collections.Concurrent.ConcurrentQueue<string>();
System.AppDomain.CurrentDomain.SetData(storageKey, samples);
__VISUAL_OBSERVER_FACTORY__
var observeVisual = CreateVisualObserver(hero, samples);
var knownStateNames = new[]
{
    "Idle", "Run", "Attack_Normal_1", "Attack_Normal_4", "Evade_Front",
    "BigSkill_Start", "BigSkill", "BigSkill_End"
};
var phaseCaptureStates = new string[] { __PHASE_CAPTURE_STATES__ };
var capturedPhaseStates = new System.Collections.Generic.HashSet<string>(
    System.StringComparer.Ordinal);
bool captureEachPhase = __CAPTURE_EACH_PHASE__;

string CurrentStateName()
{
    var stateInfo = animator.GetCurrentAnimatorStateInfo();
    for (int i = 0; i < knownStateNames.Length; i++)
        if (stateInfo.IsName(knownStateNames[i])) return knownStateNames[i];
    return animator.CurrentClip?.Name ?? "<null>";
}

string Snapshot(int frame)
{
    var requestedMotion = (XEngine.Vector.Float3)(lastVelocityField?.GetValue(cc)
        ?? default(XEngine.Vector.Float3));
    var rootPosition = hero.Transform.Position;
    var heroForward = hero.Transform.Forward;
    var boneWorld = travelBone?.Position ?? default;
    var boneLocal = travelBone?.LocalPosition ?? default;
    var boneForward = travelBone?.Forward ?? default;
    string action = actionProperty?.GetValue(controller)?.ToString() ?? "<missing>";
    string clip = CurrentStateName();
    float normalizedTime = animator.GetCurrentAnimatorStateInfo().normalizedTime;
    var target = lockedTargetField?.GetValue(controller) as XEngine.Runtime.GameObject;
    string targetName = target?.Name ?? "<null>";
    var targetPosition = target?.Transform.Position ?? default;
    float lungeRemaining = (float)(lungeRemainingField?.GetValue(controller) ?? 0f);
    var lungeDirection = (XEngine.Vector.Float3)(lungeDirectionField?.GetValue(controller)
        ?? default(XEngine.Vector.Float3));
    float lungeSpeed = (float)(lungeSpeedField?.GetValue(controller) ?? 0f);
    float lungeDuration = (float)(lungeDurationField?.GetValue(controller) ?? 0f);
    var cameraPosition = rig.Transform.Position;
    var cameraForward = rig.Transform.Forward;
    var cameraRight = rig.Transform.Right;
    var cameraUp = rig.Transform.Up;
    var visualPoint = travelBone != null ? boneWorld : rootPosition + new XEngine.Vector.Float3(0f, 1f, 0f);
    var relative = visualPoint - cameraPosition;
    float depth = XEngine.Vector.Float3.Dot(relative, cameraForward);
    float screenX = depth > 0.0001f ? XEngine.Vector.Float3.Dot(relative, cameraRight) / depth : float.NaN;
    float screenY = depth > 0.0001f ? XEngine.Vector.Float3.Dot(relative, cameraUp) / depth : float.NaN;
    var inv = System.Globalization.CultureInfo.InvariantCulture;
    return "ACTIONFRAME|" + frame + "|"
        + rootPosition.X.ToString("R", inv) + "|" + rootPosition.Y.ToString("R", inv) + "|"
        + rootPosition.Z.ToString("R", inv) + "|" + heroForward.X.ToString("R", inv) + "|"
        + heroForward.Z.ToString("R", inv) + "|" + requestedMotion.X.ToString("R", inv) + "|"
        + requestedMotion.Y.ToString("R", inv) + "|" + requestedMotion.Z.ToString("R", inv) + "|"
        + travelBonePath + "|" + boneWorld.X.ToString("R", inv) + "|"
        + boneWorld.Y.ToString("R", inv) + "|" + boneWorld.Z.ToString("R", inv) + "|"
        + boneLocal.X.ToString("R", inv) + "|" + boneLocal.Y.ToString("R", inv) + "|"
        + boneLocal.Z.ToString("R", inv) + "|" + boneForward.X.ToString("R", inv) + "|"
        + boneForward.Z.ToString("R", inv) + "|" + action + "|" + clip + "|"
        + normalizedTime.ToString("R", inv) + "|" + targetName + "|"
        + targetPosition.X.ToString("R", inv) + "|" + targetPosition.Y.ToString("R", inv) + "|"
        + targetPosition.Z.ToString("R", inv) + "|" + lungeRemaining.ToString("R", inv) + "|"
        + lungeDirection.X.ToString("R", inv) + "|" + lungeDirection.Z.ToString("R", inv) + "|"
        + lungeSpeed.ToString("R", inv) + "|" + lungeDuration.ToString("R", inv) + "|"
        + (animator.ApplyRootMotion ? "1" : "0") + "|" + screenX.ToString("R", inv) + "|"
        + screenY.ToString("R", inv) + "|" + depth.ToString("R", inv) + "|"
        + cameraPosition.X.ToString("R", inv) + "|" + cameraPosition.Y.ToString("R", inv) + "|"
        + cameraPosition.Z.ToString("R", inv);
}

async XEngine.Async.XTaskVoid RunActionProbe()
{
    bool actionSeen = false;
    int idleAfterAction = 0;
    string trackedState = "<none>";
    int trackedStateFrame = 0;
    samples.Enqueue(Snapshot(0));
    observeVisual(0);
    try
    {
        // runtime_eval can run after this frame's Input.UpdateActions but before its
        // EndOfFrame pump.  Pressing synchronously here and releasing on the first
        // EndOfFrame can therefore make the entire pulse invisible to the action map.
        // Align the press to an EndOfFrame boundary, then keep it held across the next
        // Input.UpdateActions + HeroCombatController.Update pair.
        do { await XEngine.Async.XTask.NextFrame(XEngine.Async.FrameTiming.EndOfFrame); } while (XEngine.Runtime.Application.IsPaused);
        XEngine.Runtime.InputInjector.Press(XEngine.Runtime.KeyCode.__KEY__);
        for (int frame = 1; frame <= __MAX_FRAMES__; frame++)
        {
            do { await XEngine.Async.XTask.NextFrame(XEngine.Async.FrameTiming.EndOfFrame); } while (XEngine.Runtime.Application.IsPaused);
            string snapshot = Snapshot(frame);
            samples.Enqueue(snapshot);
            observeVisual(frame);
            if (frame == 1) XEngine.Runtime.InputInjector.Release(XEngine.Runtime.KeyCode.__KEY__);
            string action = actionProperty?.GetValue(controller)?.ToString() ?? "None";
            if (action != "None")
            {
                actionSeen = true;
                idleAfterAction = 0;
            }
            else if (actionSeen)
            {
                idleAfterAction++;
            }
            string state = CurrentStateName();
            if (!state.Equals(trackedState, System.StringComparison.Ordinal))
            {
                trackedState = state;
                trackedStateFrame = 1;
            }
            else
            {
                trackedStateFrame++;
            }
            bool captureReady = false;
            if (!captureEachPhase && trackedStateFrame == __CAPTURE_FRAME__
                && phaseCaptureStates.Length == 1
                && state.Equals(phaseCaptureStates[0], System.StringComparison.Ordinal))
            {
                captureReady = capturedPhaseStates.Add(state);
            }
            if (captureEachPhase && trackedStateFrame == __CAPTURE_FRAME__)
            {
                for (int captureIndex = 0; captureIndex < phaseCaptureStates.Length; captureIndex++)
                {
                    if (state.Equals(phaseCaptureStates[captureIndex], System.StringComparison.Ordinal))
                    {
                        captureReady = capturedPhaseStates.Add(state);
                        break;
                    }
                }
            }
            if (captureReady)
            {
                samples.Enqueue("CAPTURE_READY|" + frame + "|" + trackedStateFrame + "|"
                    + action + "|" + state);
                XEngine.Runtime.Application.IsPaused = true;
            }
            if (actionSeen && idleAfterAction >= 5) break;
        }
        if (!actionSeen) samples.Enqueue("ERROR|action never became active");
        if (captureEachPhase)
        {
            for (int captureIndex = 0; captureIndex < phaseCaptureStates.Length; captureIndex++)
            {
                if (!capturedPhaseStates.Contains(phaseCaptureStates[captureIndex]))
                    samples.Enqueue("ERROR|capture state never reached:" + phaseCaptureStates[captureIndex]);
            }
        }
    }
    catch (System.Exception ex)
    {
        samples.Enqueue("ERROR|" + ex.GetType().Name + ":" + ex.Message);
    }
    finally
    {
        XEngine.Runtime.InputInjector.Release(XEngine.Runtime.KeyCode.__KEY__);
        XEngine.Runtime.Application.IsPaused = false;
        samples.Enqueue("DONE");
    }
}
RunActionProbe().Forget();
return "STARTED|__KEY__|" + storageKey;
'''


ACTION_RENDERER_EVIDENCE_CODE = r'''
XEngine.Runtime.MonoBehaviour? controller = null;
XEngine.Runtime.MonoBehaviour? rig = null;
foreach (var root in Scene.Current.RootObjects)
{
    foreach (var candidate in root.GetComponentsInChildren<XEngine.Runtime.MonoBehaviour>(true, true))
    {
        string fullName = candidate.GetType().FullName ?? candidate.GetType().Name;
        if (fullName == "XEngine.Zonezero.Combat.HeroCombatController" && candidate.EnabledInHierarchy)
            controller = candidate;
        else if (fullName == "XEngine.Zonezero.Combat.BattleFollowCamera" && candidate.EnabledInHierarchy)
            rig = candidate;
    }
    if (controller != null && rig != null) break;
}
if (controller == null || rig == null) return "ERROR|missing hero controller or camera rig";
var hero = controller.GameObject;
var animator = hero.GetComponent<XEngine.Runtime.Animator>();
if (animator == null) return "ERROR|hero Animator missing";
var renderers = new System.Collections.Generic.List<XEngine.Runtime.SkinnedMeshRenderer>(
    hero.GetComponentsInChildren<XEngine.Runtime.SkinnedMeshRenderer>(true, true));
var flags = System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.NonPublic;
var cachedBoundsField = typeof(XEngine.Runtime.SkinnedMeshRenderer).GetField("_cachedBounds", flags);
if (cachedBoundsField == null) return "ERROR|SkinnedMeshRenderer._cachedBounds reflection contract missing";
var cameraPosition = rig.Transform.Position;
var cameraForward = rig.Transform.Forward;
var cameraRight = rig.Transform.Right;
var cameraUp = rig.Transform.Up;
var inv = System.Globalization.CultureInfo.InvariantCulture;
var sb = new System.Text.StringBuilder();

string Safe(string? value)
    => (value ?? "<null>").Replace("|", "/").Replace("\r", " ").Replace("\n", " ");

string RelativePath(XEngine.Vector.Transform? transform)
{
    if (transform == null) return "<null>";
    return Safe(XEngine.Vector.Transform.GetRelativePath(transform, hero.Transform));
}

XEngine.Vector.Float3 ScreenProxy(XEngine.Vector.Float3 point)
{
    var relative = point - cameraPosition;
    float depth = XEngine.Vector.Float3.Dot(relative, cameraForward);
    float x = depth > 0.0001f ? XEngine.Vector.Float3.Dot(relative, cameraRight) / depth : float.NaN;
    float y = depth > 0.0001f ? XEngine.Vector.Float3.Dot(relative, cameraUp) / depth : float.NaN;
    return new XEngine.Vector.Float3(x, y, depth);
}

var heroPosition = hero.Transform.Position;
sb.Append("HERO|").Append(heroPosition.X.ToString("R", inv)).Append('|')
  .Append(heroPosition.Y.ToString("R", inv)).Append('|')
  .Append(heroPosition.Z.ToString("R", inv)).Append('|').Append(renderers.Count).Append('\n');

for (int rendererIndex = 0; rendererIndex < renderers.Count; rendererIndex++)
{
    var renderer = renderers[rendererIndex];
    var rootBone = renderer.RootBone;
    var rootPosition = rootBone?.Position ?? default;
    XEngine.Vector.AABB bounds = default;
    object? rawBounds = cachedBoundsField?.GetValue(renderer);
    if (rawBounds is XEngine.Vector.AABB value) bounds = value;
    var boundsCenter = bounds.Center;
    var boundsSize = bounds.Size;
    var screen = ScreenProxy(boundsCenter);
    var bones = renderer.Bones;
    int boneCount = bones?.Length ?? 0;
    int unresolvedCount = 0;
    int bip001Count = 0;
    if (bones != null)
    {
        for (int boneIndex = 0; boneIndex < bones.Length; boneIndex++)
        {
            var bone = bones[boneIndex];
            if (bone == null) unresolvedCount++;
            else if (bone.GameObject.Name.Equals("Bip001", System.StringComparison.OrdinalIgnoreCase))
                bip001Count++;
        }
    }
    sb.Append("RENDERER|").Append(rendererIndex).Append('|')
      .Append(Safe(renderer.GameObject.Name)).Append('|')
      .Append(renderer.EnabledInHierarchy ? "1" : "0").Append('|')
      .Append(Safe(renderer.RootBonePath)).Append('|').Append(RelativePath(rootBone)).Append('|')
      .Append(rootPosition.X.ToString("R", inv)).Append('|')
      .Append(rootPosition.Y.ToString("R", inv)).Append('|')
      .Append(rootPosition.Z.ToString("R", inv)).Append('|')
      .Append(boundsCenter.X.ToString("R", inv)).Append('|')
      .Append(boundsCenter.Y.ToString("R", inv)).Append('|')
      .Append(boundsCenter.Z.ToString("R", inv)).Append('|')
      .Append(boundsSize.X.ToString("R", inv)).Append('|')
      .Append(boundsSize.Y.ToString("R", inv)).Append('|')
      .Append(boundsSize.Z.ToString("R", inv)).Append('|')
      .Append(screen.X.ToString("R", inv)).Append('|')
      .Append(screen.Y.ToString("R", inv)).Append('|')
      .Append(screen.Z.ToString("R", inv)).Append('|')
      .Append(boneCount).Append('|').Append(unresolvedCount).Append('|').Append(bip001Count)
      .Append('\n');
}

if (animator.Skeleton != null)
{
    var skeleton = animator.Skeleton;
    for (int boneIndex = 0; boneIndex < skeleton.Bones.Length; boneIndex++)
    {
        var bone = skeleton.Bones[boneIndex];
        if (bone == null) continue;
        string path = boneIndex < skeleton.Paths.Length ? skeleton.Paths[boneIndex] : RelativePath(bone);
        var world = bone.Position;
        var local = bone.LocalPosition;
        bool isTop = bone.GameObject.Name.Equals("Bip001", System.StringComparison.OrdinalIgnoreCase);
        bool isPelvis = bone.GameObject.Name.Equals("Bip001 Pelvis", System.StringComparison.OrdinalIgnoreCase);
        if (isTop || isPelvis)
        {
            var worldRotation = bone.Rotation;
            var localRotation = bone.LocalRotation;
            var reference = skeleton.ReferencePose[boneIndex];
            sb.Append("KEYBONE|").Append(boneIndex).Append('|').Append(Safe(path)).Append('|')
              .Append(Safe(bone.GameObject.Name)).Append('|')
              .Append(world.X.ToString("R", inv)).Append('|').Append(world.Y.ToString("R", inv)).Append('|')
              .Append(world.Z.ToString("R", inv)).Append('|').Append(local.X.ToString("R", inv)).Append('|')
              .Append(local.Y.ToString("R", inv)).Append('|').Append(local.Z.ToString("R", inv)).Append('|')
              .Append(worldRotation.X.ToString("R", inv)).Append('|').Append(worldRotation.Y.ToString("R", inv)).Append('|')
              .Append(worldRotation.Z.ToString("R", inv)).Append('|').Append(worldRotation.W.ToString("R", inv)).Append('|')
              .Append(localRotation.X.ToString("R", inv)).Append('|').Append(localRotation.Y.ToString("R", inv)).Append('|')
              .Append(localRotation.Z.ToString("R", inv)).Append('|').Append(localRotation.W.ToString("R", inv)).Append('|')
              .Append(reference.LocalPosition.X.ToString("R", inv)).Append('|')
              .Append(reference.LocalPosition.Y.ToString("R", inv)).Append('|')
              .Append(reference.LocalPosition.Z.ToString("R", inv)).Append('|')
              .Append(reference.LocalRotation.X.ToString("R", inv)).Append('|')
              .Append(reference.LocalRotation.Y.ToString("R", inv)).Append('|')
              .Append(reference.LocalRotation.Z.ToString("R", inv)).Append('|')
              .Append(reference.LocalRotation.W.ToString("R", inv)).Append('\n');
        }
        if (isTop)
        {
            sb.Append("BIP001|AnimatorSkeleton|").Append(boneIndex).Append('|').Append(Safe(path)).Append('|')
              .Append(world.X.ToString("R", inv)).Append('|').Append(world.Y.ToString("R", inv)).Append('|')
              .Append(world.Z.ToString("R", inv)).Append('|').Append(local.X.ToString("R", inv)).Append('|')
              .Append(local.Y.ToString("R", inv)).Append('|').Append(local.Z.ToString("R", inv)).Append('\n');
        }
    }
}

for (int rendererIndex = 0; rendererIndex < renderers.Count; rendererIndex++)
{
    var renderer = renderers[rendererIndex];
    var bones = renderer.Bones;
    if (bones == null) continue;
    for (int boneIndex = 0; boneIndex < bones.Length; boneIndex++)
    {
        var bone = bones[boneIndex];
        if (bone == null || !bone.GameObject.Name.Equals("Bip001", System.StringComparison.OrdinalIgnoreCase))
            continue;
        string path = renderer.BonePaths != null && boneIndex < renderer.BonePaths.Length
            ? renderer.BonePaths[boneIndex] : RelativePath(bone);
        var world = bone.Position;
        var local = bone.LocalPosition;
        sb.Append("BIP001|Renderer:").Append(rendererIndex).Append('|').Append(boneIndex).Append('|')
          .Append(Safe(path)).Append('|').Append(world.X.ToString("R", inv)).Append('|')
          .Append(world.Y.ToString("R", inv)).Append('|').Append(world.Z.ToString("R", inv)).Append('|')
          .Append(local.X.ToString("R", inv)).Append('|').Append(local.Y.ToString("R", inv)).Append('|')
          .Append(local.Z.ToString("R", inv)).Append('\n');
    }
}
return sb.ToString();
'''


ACTION_CURVE_EVIDENCE_CODE = r'''
XEngine.Runtime.Animator? animator = null;
foreach (var root in Scene.Current.RootObjects)
{
    foreach (var candidate in root.GetComponentsInChildren<XEngine.Runtime.MonoBehaviour>(true, true))
    {
        string fullName = candidate.GetType().FullName ?? candidate.GetType().Name;
        if (fullName == "XEngine.Zonezero.Combat.HeroCombatController" && candidate.EnabledInHierarchy)
        {
            animator = candidate.GameObject.GetComponent<XEngine.Runtime.Animator>();
            break;
        }
    }
    if (animator != null) break;
}
if (animator?.Runtime == null) return "ERROR|hero animator runtime missing";
var constant = animator.Runtime.Constant;
var states = new[]
{
    "Attack_Normal_1", "Attack_Normal_4", "Evade_Front",
    "BigSkill_Start", "BigSkill", "BigSkill_End"
};
var inv = System.Globalization.CultureInfo.InvariantCulture;
var sb = new System.Text.StringBuilder();

string Safe(string? value)
    => (value ?? "<null>").Replace("|", "/").Replace("\r", " ").Replace("\n", " ");

void CurveStats(XEngine.Runtime.AnimationCurve? curve, out float minimum, out float maximum, out float range)
{
    minimum = 0f;
    maximum = 0f;
    range = 0f;
    if (curve == null || curve.Keys.Count == 0) return;
    minimum = float.MaxValue;
    maximum = float.MinValue;
    for (int keyIndex = 0; keyIndex < curve.Keys.Count; keyIndex++)
    {
        float value = curve.Keys[keyIndex].Value;
        if (value < minimum) minimum = value;
        if (value > maximum) maximum = value;
    }
    range = maximum - minimum;
}

for (int requestedIndex = 0; requestedIndex < states.Length; requestedIndex++)
{
    string stateName = states[requestedIndex];
    int stateHash = XEngine.Animation.AnimationNameHash.Hash(stateName);
    int stateIndex = -1;
    for (int i = 0; i < constant.States.Length; i++)
    {
        if (constant.States[i].NameHash == stateHash)
        {
            stateIndex = i;
            break;
        }
    }
    if (stateIndex < 0)
    {
        sb.Append("STATECURVES|").Append(stateName).Append("|<missing>|<missing>|0|0\n");
        continue;
    }
    int motionIndex = constant.States[stateIndex].MotionIndex;
    XEngine.Runtime.AnimationClip? clip = motionIndex >= 0 && motionIndex < constant.MotionClips.Length
        ? constant.MotionClips[motionIndex] : null;
    if (clip == null)
    {
        sb.Append("STATECURVES|").Append(stateName).Append("|<null>|<null>|0|0\n");
        continue;
    }
    int largeCount = 0;
    for (int boneIndex = 0; boneIndex < clip.Bones.Count; boneIndex++)
    {
        var bone = clip.Bones[boneIndex];
        CurveStats(bone.PosX, out float minX, out float maxX, out float rangeX);
        CurveStats(bone.PosY, out float minY, out float maxY, out float rangeY);
        CurveStats(bone.PosZ, out float minZ, out float maxZ, out float rangeZ);
        bool aboveThreshold = rangeX > 0.5f || rangeY > 0.5f || rangeZ > 0.5f;
        int leafStart = bone.BoneName.LastIndexOf('/') + 1;
        string leafName = bone.BoneName.Substring(leafStart);
        bool diagnosticCarrier = leafName.Equals("Root", System.StringComparison.OrdinalIgnoreCase)
            || leafName.Equals("Bip001", System.StringComparison.OrdinalIgnoreCase)
            || leafName.Equals("Bip001 Pelvis", System.StringComparison.OrdinalIgnoreCase)
            || leafName.Contains("Weapon", System.StringComparison.OrdinalIgnoreCase);
        if (!aboveThreshold && !diagnosticCarrier) continue;
        if (aboveThreshold) largeCount++;
        int depth = 0;
        for (int c = 0; c < bone.BoneName.Length; c++)
            if (bone.BoneName[c] == '/') depth++;
        sb.Append("CURVE|").Append(stateName).Append('|').Append(Safe(clip.Name)).Append('|')
          .Append(Safe(clip.RootBonePath)).Append('|').Append(clip.Duration.ToString("R", inv)).Append('|')
          .Append(boneIndex).Append('|').Append(Safe(bone.BoneName)).Append('|').Append(depth).Append('|')
          .Append(minX.ToString("R", inv)).Append('|').Append(maxX.ToString("R", inv)).Append('|')
          .Append(rangeX.ToString("R", inv)).Append('|').Append(minY.ToString("R", inv)).Append('|')
          .Append(maxY.ToString("R", inv)).Append('|').Append(rangeY.ToString("R", inv)).Append('|')
          .Append(minZ.ToString("R", inv)).Append('|').Append(maxZ.ToString("R", inv)).Append('|')
          .Append(rangeZ.ToString("R", inv)).Append('|').Append(bone.HasSourceReferencePose ? "1" : "0").Append('|')
          .Append(bone.SourcePosition.X.ToString("R", inv)).Append('|')
          .Append(bone.SourcePosition.Y.ToString("R", inv)).Append('|')
          .Append(bone.SourcePosition.Z.ToString("R", inv)).Append('\n');
    }
    sb.Append("STATECURVES|").Append(stateName).Append('|').Append(Safe(clip.Name)).Append('|')
      .Append(Safe(clip.RootBonePath)).Append('|').Append(largeCount).Append('|').Append(clip.Bones.Count)
      .Append('\n');
}
return sb.ToString();
'''


ACTOR_MATRIX_PREFLIGHT_CODE = r'''
var requiredRoots = new[] { __ACTOR_ROOTS__ };
var found = new System.Collections.Generic.HashSet<string>(System.StringComparer.Ordinal);
foreach (var root in Scene.Current.RootObjects)
{
    for (int i = 0; i < requiredRoots.Length; i++)
    {
        if (!root.Name.Equals(requiredRoots[i], System.StringComparison.Ordinal)) continue;
        if (!found.Add(root.Name)) return "ERROR|duplicate actor root:" + root.Name;
        var animator = root.GetComponent<XEngine.Runtime.Animator>();
        if (animator == null) return "ERROR|actor Animator missing:" + root.Name;
        foreach (var component in root.GetComponentsInChildren<XEngine.Runtime.MonoBehaviour>(true, true))
        {
            string fullName = component.GetType().FullName ?? component.GetType().Name;
            if (fullName == "XEngine.Zonezero.Combat.HeroCombatController"
                || fullName == "XEngine.Zonezero.Combat.AllyCombatAI")
                component.Enabled = false;
        }
    }
}
if (found.Count != requiredRoots.Length)
{
    var missing = new System.Collections.Generic.List<string>();
    for (int i = 0; i < requiredRoots.Length; i++)
        if (!found.Contains(requiredRoots[i])) missing.Add(requiredRoots[i]);
    return "ERROR|actor roots missing:" + string.Join(",", missing);
}
return "READY|" + string.Join(",", found);
'''


ACTOR_STATE_COROUTINE_TEMPLATE = r'''
string storageKey = "__STORAGE_KEY__";
string actorLabel = __ACTOR_LABEL__;
string actorRootName = __ACTOR_ROOT__;
string stateName = __STATE_NAME__;
bool driveState = __DRIVE_STATE__;
XEngine.Runtime.GameObject? actor = null;
foreach (var root in Scene.Current.RootObjects)
{
    if (root.Name.Equals(actorRootName, System.StringComparison.Ordinal))
    {
        actor = root;
        break;
    }
}
if (actor == null) return "ERROR|actor root missing:" + actorRootName;
var animator = actor.GetComponent<XEngine.Runtime.Animator>();
if (animator?.Skeleton == null) return "ERROR|actor Animator/Skeleton missing:" + actorRootName;
if (!animator.HasState(stateName)) return "ERROR|state missing:" + actorLabel + "/" + stateName;
foreach (var component in actor.GetComponentsInChildren<XEngine.Runtime.MonoBehaviour>(true, true))
{
    string fullName = component.GetType().FullName ?? component.GetType().Name;
    if (driveState && (fullName == "XEngine.Zonezero.Combat.HeroCombatController"
        || fullName == "XEngine.Zonezero.Combat.AllyCombatAI"))
        component.Enabled = false;
}
var skeleton = animator.Skeleton;
XEngine.Vector.Transform? bip001 = null;
XEngine.Vector.Transform? pelvis = null;
int bip001Index = -1;
int pelvisIndex = -1;
var coreNames = new[] { __CORE_BONE_NAMES__ };
var coreIndices = new int[coreNames.Length];
for (int i = 0; i < coreIndices.Length; i++) coreIndices[i] = -1;
for (int boneIndex = 0; boneIndex < skeleton.Bones.Length; boneIndex++)
{
    var bone = skeleton.Bones[boneIndex];
    if (bone == null) continue;
    string name = bone.GameObject.Name;
    if (name.Equals("Bip001", System.StringComparison.OrdinalIgnoreCase))
    {
        bip001 = bone;
        bip001Index = boneIndex;
    }
    else if (name.Equals("Bip001 Pelvis", System.StringComparison.OrdinalIgnoreCase))
    {
        pelvis = bone;
        pelvisIndex = boneIndex;
    }
    for (int coreIndex = 0; coreIndex < coreNames.Length; coreIndex++)
        if (name.Equals(coreNames[coreIndex], System.StringComparison.OrdinalIgnoreCase))
            coreIndices[coreIndex] = boneIndex;
}
if (bip001 == null || pelvis == null || bip001Index < 0 || pelvisIndex < 0)
    return "ERROR|Bip001/Pelvis missing:" + actorLabel;
var missingCore = new System.Collections.Generic.List<string>();
for (int i = 0; i < coreIndices.Length; i++)
    if (coreIndices[i] < 0) missingCore.Add(coreNames[i]);
if (missingCore.Count != 0)
    return "ERROR|core pose bones missing:" + actorLabel + ":" + string.Join(",", missingCore);
var facingLeftThigh = skeleton.Bones[coreIndices[System.Array.IndexOf(coreNames, "Bip001 L Thigh")]]!;
var facingRightThigh = skeleton.Bones[coreIndices[System.Array.IndexOf(coreNames, "Bip001 R Thigh")]]!;
var facingSpine = skeleton.Bones[coreIndices[System.Array.IndexOf(coreNames, "Bip001 Spine")]]!;

var renderers = new System.Collections.Generic.List<XEngine.Runtime.SkinnedMeshRenderer>(
    actor.GetComponentsInChildren<XEngine.Runtime.SkinnedMeshRenderer>(true, true));
var primaryRenderers = new System.Collections.Generic.List<XEngine.Runtime.SkinnedMeshRenderer>();
int enabledRendererCount = 0;
int unresolvedBoneCount = 0;
for (int rendererIndex = 0; rendererIndex < renderers.Count; rendererIndex++)
{
    var renderer = renderers[rendererIndex];
    if (!renderer.EnabledInHierarchy) continue;
    enabledRendererCount++;
    string rootPath = (renderer.RootBonePath ?? "").Replace('\\', '/');
    if (rootPath.EndsWith("/Bip001 Pelvis", System.StringComparison.OrdinalIgnoreCase))
        primaryRenderers.Add(renderer);
    var bones = renderer.Bones;
    if (bones == null) continue;
    for (int boneIndex = 0; boneIndex < bones.Length; boneIndex++)
        if (bones[boneIndex] == null) unresolvedBoneCount++;
}
if (primaryRenderers.Count == 0) return "ERROR|primary body renderer missing:" + actorLabel;
var boundsField = typeof(XEngine.Runtime.SkinnedMeshRenderer).GetField(
    "_cachedBounds", System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.NonPublic);
if (boundsField == null) return "ERROR|SkinnedMeshRenderer._cachedBounds reflection contract missing";

var samples = new System.Collections.Concurrent.ConcurrentQueue<string>();
System.AppDomain.CurrentDomain.SetData(storageKey, samples);
var inv = System.Globalization.CultureInfo.InvariantCulture;

string Safe(string? value)
    => (value ?? "<null>").Replace("|", "/").Replace("\r", " ").Replace("\n", " ");

void CombinedPrimaryBounds(out XEngine.Vector.Float3 center, out XEngine.Vector.Float3 size)
{
    float minX = float.MaxValue, minY = float.MaxValue, minZ = float.MaxValue;
    float maxX = float.MinValue, maxY = float.MinValue, maxZ = float.MinValue;
    bool any = false;
    for (int rendererIndex = 0; rendererIndex < primaryRenderers.Count; rendererIndex++)
    {
        object? raw = boundsField.GetValue(primaryRenderers[rendererIndex]);
        if (raw is not XEngine.Vector.AABB bounds) continue;
        var boundsCenter = bounds.Center;
        var boundsSize = bounds.Size;
        var half = boundsSize * 0.5f;
        var minimum = boundsCenter - half;
        var maximum = boundsCenter + half;
        minX = System.MathF.Min(minX, minimum.X);
        minY = System.MathF.Min(minY, minimum.Y);
        minZ = System.MathF.Min(minZ, minimum.Z);
        maxX = System.MathF.Max(maxX, maximum.X);
        maxY = System.MathF.Max(maxY, maximum.Y);
        maxZ = System.MathF.Max(maxZ, maximum.Z);
        any = true;
    }
    if (!any)
    {
        center = new XEngine.Vector.Float3(float.NaN);
        size = new XEngine.Vector.Float3(float.NaN);
        return;
    }
    center = new XEngine.Vector.Float3(
        (minX + maxX) * 0.5f, (minY + maxY) * 0.5f, (minZ + maxZ) * 0.5f);
    size = new XEngine.Vector.Float3(maxX - minX, maxY - minY, maxZ - minZ);
}

void Snapshot(int frame)
{
    var stateInfo = animator.GetCurrentAnimatorStateInfo();
    bool observed = stateInfo.IsName(stateName);
    var rootPosition = actor.Transform.Position;
    var bipWorld = bip001.Position;
    var bipLocal = bip001.LocalPosition;
    var bipReference = skeleton.ReferencePose[bip001Index].LocalPosition;
    var pelvisWorld = pelvis.Position;
    var pelvisLocal = pelvis.LocalPosition;
    var pelvisReference = skeleton.ReferencePose[pelvisIndex].LocalPosition;
    CombinedPrimaryBounds(out var boundsCenter, out var boundsSize);
    string clip = animator.CurrentClip?.Name ?? "<null>";
    samples.Enqueue("ACTORFRAME|" + frame + "|" + (observed ? "1" : "0") + "|"
        + stateInfo.normalizedTime.ToString("R", inv) + "|"
        + (animator.ApplyRootMotion ? "1" : "0") + "|"
        + rootPosition.X.ToString("R", inv) + "|" + rootPosition.Y.ToString("R", inv) + "|"
        + rootPosition.Z.ToString("R", inv) + "|"
        + bipWorld.X.ToString("R", inv) + "|" + bipWorld.Y.ToString("R", inv) + "|"
        + bipWorld.Z.ToString("R", inv) + "|" + bipLocal.X.ToString("R", inv) + "|"
        + bipLocal.Y.ToString("R", inv) + "|" + bipLocal.Z.ToString("R", inv) + "|"
        + bipReference.X.ToString("R", inv) + "|" + bipReference.Y.ToString("R", inv) + "|"
        + bipReference.Z.ToString("R", inv) + "|"
        + pelvisWorld.X.ToString("R", inv) + "|" + pelvisWorld.Y.ToString("R", inv) + "|"
        + pelvisWorld.Z.ToString("R", inv) + "|" + pelvisLocal.X.ToString("R", inv) + "|"
        + pelvisLocal.Y.ToString("R", inv) + "|" + pelvisLocal.Z.ToString("R", inv) + "|"
        + pelvisReference.X.ToString("R", inv) + "|" + pelvisReference.Y.ToString("R", inv) + "|"
        + pelvisReference.Z.ToString("R", inv) + "|" + primaryRenderers.Count + "|"
        + boundsCenter.X.ToString("R", inv) + "|" + boundsCenter.Y.ToString("R", inv) + "|"
        + boundsCenter.Z.ToString("R", inv) + "|" + boundsSize.X.ToString("R", inv) + "|"
        + boundsSize.Y.ToString("R", inv) + "|" + boundsSize.Z.ToString("R", inv) + "|"
        + enabledRendererCount + "|" + unresolvedBoneCount + "|" + Safe(clip) + "|"
        + XEngine.Runtime.Time.FrameCount + "|" + (XEngine.Runtime.Application.IsPaused ? "1" : "0"));
    var leftThighWorld = facingLeftThigh.Position;
    var rightThighWorld = facingRightThigh.Position;
    var spineWorld = facingSpine.Position;
    samples.Enqueue("FACINGFRAME|" + frame + "|"
        + leftThighWorld.X.ToString("R", inv) + "|" + leftThighWorld.Y.ToString("R", inv) + "|" + leftThighWorld.Z.ToString("R", inv) + "|"
        + rightThighWorld.X.ToString("R", inv) + "|" + rightThighWorld.Y.ToString("R", inv) + "|" + rightThighWorld.Z.ToString("R", inv) + "|"
        + spineWorld.X.ToString("R", inv) + "|" + spineWorld.Y.ToString("R", inv) + "|" + spineWorld.Z.ToString("R", inv));
    for (int rendererIndex = 0; rendererIndex < renderers.Count; rendererIndex++)
    {
        var renderer = renderers[rendererIndex];
        if (!renderer.EnabledInHierarchy) continue;
        if (boundsField.GetValue(renderer) is not XEngine.Vector.AABB bounds)
            throw new System.InvalidOperationException("missing renderer bounds");
        var rootBone = renderer.RootBone;
        var position = rootBone?.Position ?? new XEngine.Vector.Float3(float.NaN);
        var center = bounds.Center;
        var size = bounds.Size;
        var bones = renderer.Bones;
        int unresolved = 0;
        if (bones != null) for (int i = 0; i < bones.Length; i++) if (bones[i] == null) unresolved++;
        samples.Enqueue("ACTORRENDERER|" + frame + "|" + rendererIndex + "|" + Safe(renderer.GameObject.Name) + "|"
            + (primaryRenderers.Contains(renderer) ? "1" : "0") + "|" + (bones?.Length ?? 0) + "|"
            + unresolved + "|" + (rootBone != null ? "1" : "0") + "|"
            + position.X.ToString("R", inv) + "|" + position.Y.ToString("R", inv) + "|" + position.Z.ToString("R", inv) + "|"
            + center.X.ToString("R", inv) + "|" + center.Y.ToString("R", inv) + "|" + center.Z.ToString("R", inv) + "|"
            + size.X.ToString("R", inv) + "|" + size.Y.ToString("R", inv) + "|" + size.Z.ToString("R", inv));
    }
    for (int coreIndex = 0; coreIndex < coreIndices.Length; coreIndex++)
    {
        int boneIndex = coreIndices[coreIndex];
        var current = skeleton.Bones[boneIndex]!.LocalRotation;
        var reference = skeleton.ReferencePose[boneIndex].LocalRotation;
        samples.Enqueue("ACTORPOSE|" + frame + "|" + Safe(coreNames[coreIndex]) + "|"
            + current.X.ToString("R", inv) + "|" + current.Y.ToString("R", inv) + "|"
            + current.Z.ToString("R", inv) + "|" + current.W.ToString("R", inv) + "|"
            + reference.X.ToString("R", inv) + "|" + reference.Y.ToString("R", inv) + "|"
            + reference.Z.ToString("R", inv) + "|" + reference.W.ToString("R", inv));
    }
}

async XEngine.Async.XTaskVoid RunActorStateProbe()
{
    async XEngine.Async.XTask WaitForRenderedPose(long previousPresentCount)
    {
        long started = System.Diagnostics.Stopwatch.GetTimestamp();
        do
        {
            await XEngine.Async.XTask.NextFrame(XEngine.Async.FrameTiming.EndOfFrame);
            if (!System.Object.ReferenceEquals(System.AppDomain.CurrentDomain.GetData(storageKey), samples))
                throw new System.OperationCanceledException("isolated probe was cleared");
            if (System.Diagnostics.Stopwatch.GetElapsedTime(started).TotalSeconds > 15d)
                throw new System.TimeoutException("isolated evaluated pose was not presented");
        }
        while (XEngine.Runtime.Window.PresentCount <= previousPresentCount);
    }
    try
    {
        if (driveState)
        {
            // Only this isolated asset matrix controls time. Production inputs and the natural
            // observer retain the engine's normal clock and reject paused-frame telemetry.
            XEngine.Runtime.Application.IsPaused = true;
            System.AppDomain.CurrentDomain.SetData(storageKey + "-captureAck", -1);
            if (!animator.HasState("Idle")) throw new System.InvalidOperationException("isolated fixture has no Idle state");
            animator.Play("Idle");
            long warmupPresent = XEngine.Runtime.Window.PresentCount;
            animator.EvaluateGraph(0f);
            await WaitForRenderedPose(warmupPresent);
            animator.Play(stateName);
            animator.EvaluateGraph(0f);
        }
        int captureIndex = 0;
        float[] captureTimes = { 0.2f, 0.5f, 0.8f };
        bool stateSeen = false;
        for (int frame = 1; frame <= __MAX_FRAMES__; frame++)
        {
            float evaluationDelta = 0f, normalizedBefore = 0f, playbackSpeed = 0f;
            double clipDuration = 0d;
            long presentationBefore = 0;
            if (driveState)
            {
                if (!System.Object.ReferenceEquals(System.AppDomain.CurrentDomain.GetData(storageKey), samples)) return;
                if (!XEngine.Runtime.Application.IsPaused)
                    throw new System.InvalidOperationException("isolated controlled evaluation unexpectedly resumed gameplay");
                var before = animator.GetCurrentAnimatorStateInfo();
                if (!before.IsName(stateName))
                    throw new System.InvalidOperationException("isolated state changed before evaluation:" + stateName);
                XEngine.Animation.AnimationClipPlayable? active = null;
                var layer = animator.Layer;
                if (layer != null)
                    for (int input = 0; input < layer.InputCount; input++)
                        if (layer.GetInput(input) is XEngine.Animation.AnimationClipPlayable candidate
                            && candidate.Weight > 0f && System.Object.ReferenceEquals(candidate.Clip, animator.CurrentClip))
                        {
                            if (active != null) throw new System.InvalidOperationException("ambiguous isolated clip playable");
                            active = candidate;
                        }
                if (active == null || !active.IsPlaying || !double.IsFinite(active.Duration)
                    || active.Duration <= 0d || !float.IsFinite(active.Speed) || active.Speed <= 0f)
                    throw new System.InvalidOperationException("isolated state requires an advancing clip playable");
                normalizedBefore = before.normalizedTime;
                clipDuration = active.Duration;
                playbackSpeed = active.Speed;
                float boundary = captureIndex < captureTimes.Length ? captureTimes[captureIndex] : 0.95f;
                float normalizedStep = System.MathF.Min(__ISOLATED_STEP__f, boundary - normalizedBefore);
                if (!float.IsFinite(normalizedStep) || normalizedStep <= 0f)
                    throw new System.InvalidOperationException("isolated normalized step did not advance");
                evaluationDelta = (float)(normalizedStep * clipDuration / playbackSpeed);
                presentationBefore = XEngine.Runtime.Window.PresentCount;
                animator.EvaluateGraph(evaluationDelta);
                // EndOfFrame pumps before rendering and can resume in the current update.
                // PresentCount is the completed-presentation fence for fresh skin/bounds data.
                await WaitForRenderedPose(presentationBefore);
            }
            else
            {
                do { await XEngine.Async.XTask.NextFrame(XEngine.Async.FrameTiming.EndOfFrame); }
                while (XEngine.Runtime.Application.IsPaused);
            }
            var info = animator.GetCurrentAnimatorStateInfo();
            // Passive observation waits for a fresh naturally-entered state.
            if (!driveState && !stateSeen && (!info.IsName(stateName) || info.normalizedTime > 0.15f)) continue;
            Snapshot(frame);
            if (driveState)
                samples.Enqueue("ACTOR_STEP|" + frame + "|" + evaluationDelta.ToString("R", inv) + "|"
                    + normalizedBefore.ToString("R", inv) + "|" + clipDuration.ToString("R", inv) + "|"
                    + playbackSpeed.ToString("R", inv) + "|" + presentationBefore + "|" + XEngine.Runtime.Window.PresentCount);
            if (info.IsName(stateName))
            {
                stateSeen = true;
                float captureTolerance = driveState ? __ISOLATED_TOLERANCE__f : 0f;
                if (captureIndex < captureTimes.Length && info.normalizedTime + captureTolerance >= captureTimes[captureIndex])
                {
                    samples.Enqueue("ACTOR_CAPTURE|" + frame + "|" + captureIndex + "|" + info.normalizedTime.ToString("R", inv));
                    captureIndex++;
                    XEngine.Runtime.Application.IsPaused = true;
                    // Keep the evaluated pose frozen until Python has captured it and restored
                    // the camera. Acknowledgment advances this probe without unpausing gameplay.
                    if (driveState)
                        while (System.AppDomain.CurrentDomain.GetData(storageKey + "-captureAck") is not int acknowledged
                            || acknowledged != captureIndex - 1)
                        {
                            if (!System.Object.ReferenceEquals(System.AppDomain.CurrentDomain.GetData(storageKey), samples)) return;
                            await XEngine.Async.XTask.NextFrame(XEngine.Async.FrameTiming.EndOfFrame);
                        }
                }
                if (info.normalizedTime + captureTolerance >= 0.95f && frame >= 3) break;
            }
            else if (stateSeen)
            {
                break;
            }
        }
        if (!stateSeen) samples.Enqueue("ERROR|state never became active:" + actorLabel + "/" + stateName);
    }
    catch (System.Exception ex)
    {
        samples.Enqueue("ERROR|" + ex.GetType().Name + ":" + ex.Message);
    }
    finally
    {
        samples.Enqueue("DONE");
    }
}
RunActorStateProbe().Forget();
return "STARTED|" + actorLabel + "|" + stateName + "|" + storageKey;
'''


PROBE_TEMPLATE = r'''
float inputX = __INPUT_X__f;
float inputY = __INPUT_Y__f;
XEngine.Runtime.MonoBehaviour? controller = null;
XEngine.Runtime.MonoBehaviour? rig = null;
foreach (var root in Scene.Current.RootObjects)
{
    foreach (var candidate in root.GetComponentsInChildren<XEngine.Runtime.MonoBehaviour>(true, true))
    {
        if (!candidate.EnabledInHierarchy) continue;
        string fullName = candidate.GetType().FullName ?? candidate.GetType().Name;
        if (fullName == "XEngine.Zonezero.Combat.HeroCombatController") controller = candidate;
        else if (fullName == "XEngine.Zonezero.Combat.BattleFollowCamera") rig = candidate;
    }
    if (controller != null && rig != null) break;
}
if (controller == null || rig == null) return "ERROR|missing hero controller or camera rig";
var hero = controller.GameObject;
var cc = hero.GetComponent<XEngine.Runtime.CharacterController>();
if (cc == null) return "ERROR|hero CharacterController missing";
var yawField = rig.GetType().GetField("YawDeg", System.Reflection.BindingFlags.Public
    | System.Reflection.BindingFlags.Instance);
var runSpeedField = controller.GetType().GetField("RunSpeed", System.Reflection.BindingFlags.Public
    | System.Reflection.BindingFlags.Instance);
if (yawField == null || runSpeedField == null) return "ERROR|required public fields missing";
float yawDeg = (float)(yawField.GetValue(rig) ?? 0f);
float runSpeed = (float)(runSpeedField.GetValue(controller) ?? 0f);

var input = new XEngine.Vector.Float2(inputX, inputY);
XEngine.Vector.Float3 wish = default;
bool helperAvailable = false;
var flags = System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic
    | System.Reflection.BindingFlags.Static;
foreach (var method in controller.GetType().GetMethods(flags))
{
    if (method.Name != "ResolveMoveDirection" || method.ReturnType != typeof(XEngine.Vector.Float3))
        continue;
    var parameters = method.GetParameters();
    if (parameters.Length != 2 || parameters[0].ParameterType != typeof(XEngine.Vector.Float2)
        || parameters[1].ParameterType != typeof(float)) continue;
    var reflected = method.Invoke(null, new object[] { input, yawDeg });
    if (reflected is XEngine.Vector.Float3 direction)
    {
        wish = direction;
        helperAvailable = true;
    }
    break;
}
if (!helperAvailable)
{
    float yawRad = yawDeg * System.MathF.PI / 180f;
    float sinYaw = System.MathF.Sin(yawRad);
    float cosYaw = System.MathF.Cos(yawRad);
    var cameraForward = new XEngine.Vector.Float3(sinYaw, 0f, cosYaw);
    var cameraRight = new XEngine.Vector.Float3(cosYaw, 0f, -sinYaw);
    wish = cameraForward * input.Y + cameraRight * input.X;
}
float wishLength = System.MathF.Sqrt(wish.X * wish.X + wish.Z * wish.Z);
if (wishLength < 0.0001f) return "ERROR|resolved movement direction is zero";
wish = new XEngine.Vector.Float3(wish.X / wishLength, 0f, wish.Z / wishLength);

// Disable input-driven updates on the runtime clone.  Animator and fixed camera keep ticking.
controller.Enabled = false;
var start = hero.Transform.Position;
var cameraBefore = rig.Transform.Forward;
var motorType = controller.GetType().Assembly.GetType("XEngine.Zonezero.Combat.CombatMotor");
if (motorType == null) return "ERROR|CombatMotor type missing";
var motorFlags = System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.Static;
var turnMethod = motorType.GetMethod("TurnToward", motorFlags, null,
    new[] { typeof(XEngine.Vector.Transform), typeof(XEngine.Vector.Float3), typeof(float), typeof(float) }, null);
var moveMethod = motorType.GetMethod("MoveGrounded", motorFlags, null,
    new[] { typeof(XEngine.Runtime.CharacterController), typeof(XEngine.Vector.Float3), typeof(float) }, null);
if (turnMethod == null || moveMethod == null) return "ERROR|CombatMotor methods missing";
turnMethod.Invoke(null, new object[] { hero.Transform, wish, 3600f, 1f });
for (int i = 0; i < __MOVE_ITERATIONS__; i++)
    moveMethod.Invoke(null, new object[] { cc, wish, runSpeed });
var end = hero.Transform.Position;
var heroForward = hero.Transform.Forward;
var cameraAfterCall = rig.Transform.Forward;
var inv = System.Globalization.CultureInfo.InvariantCulture;
return "PROBE|" + (helperAvailable ? "production-helper" : "yaw-basis-fallback") + "|"
    + yawDeg.ToString("R", inv) + "|"
    + wish.X.ToString("R", inv) + "|" + wish.Z.ToString("R", inv) + "|"
    + start.X.ToString("R", inv) + "|" + start.Y.ToString("R", inv) + "|" + start.Z.ToString("R", inv) + "|"
    + end.X.ToString("R", inv) + "|" + end.Y.ToString("R", inv) + "|" + end.Z.ToString("R", inv) + "|"
    + heroForward.X.ToString("R", inv) + "|" + heroForward.Z.ToString("R", inv) + "|"
    + cameraBefore.X.ToString("R", inv) + "|" + cameraBefore.Z.ToString("R", inv) + "|"
    + cameraAfterCall.X.ToString("R", inv) + "|" + cameraAfterCall.Z.ToString("R", inv);
'''


CAMERA_SNAPSHOT_CODE = r'''
XEngine.Runtime.MonoBehaviour? controller = null;
XEngine.Runtime.MonoBehaviour? rig = null;
foreach (var root in Scene.Current.RootObjects)
{
    foreach (var candidate in root.GetComponentsInChildren<XEngine.Runtime.MonoBehaviour>(true, true))
    {
        string fullName = candidate.GetType().FullName ?? candidate.GetType().Name;
        if (fullName == "XEngine.Zonezero.Combat.HeroCombatController" && !candidate.IsDisposed)
            controller = candidate;
        else if (fullName == "XEngine.Zonezero.Combat.BattleFollowCamera" && candidate.EnabledInHierarchy)
            rig = candidate;
    }
    if (controller != null && rig != null) break;
}
if (controller == null || rig == null) return "ERROR|missing hero controller or camera rig";
var yawField = rig.GetType().GetField("YawDeg", System.Reflection.BindingFlags.Public
    | System.Reflection.BindingFlags.Instance);
var pitchField = rig.GetType().GetField("PitchDeg", System.Reflection.BindingFlags.Public
    | System.Reflection.BindingFlags.Instance);
if (yawField == null || pitchField == null) return "ERROR|camera YawDeg/PitchDeg field missing";
float yawDeg = (float)(yawField.GetValue(rig) ?? 0f);
float pitchDeg = (float)(pitchField.GetValue(rig) ?? 0f);
var hero = controller.GameObject;
var forward = rig.Transform.Forward;
var up = rig.Transform.Up;
var position = rig.Transform.Position;
var heroPosition = hero.Transform.Position;
var inv = System.Globalization.CultureInfo.InvariantCulture;
return "CAMERA|" + yawDeg.ToString("R", inv) + "|" + pitchDeg.ToString("R", inv) + "|"
    + forward.X.ToString("R", inv) + "|" + forward.Y.ToString("R", inv) + "|"
    + forward.Z.ToString("R", inv) + "|" + position.X.ToString("R", inv) + "|"
    + position.Y.ToString("R", inv) + "|" + position.Z.ToString("R", inv) + "|"
    + heroPosition.X.ToString("R", inv) + "|" + heroPosition.Y.ToString("R", inv) + "|"
    + heroPosition.Z.ToString("R", inv) + "|" + up.X.ToString("R", inv) + "|"
    + up.Y.ToString("R", inv) + "|" + up.Z.ToString("R", inv);
'''


VISUAL_DIRECTION_CODE = r'''
XEngine.Runtime.MonoBehaviour? controller = null;
XEngine.Runtime.MonoBehaviour? rig = null;
foreach (var root in Scene.Current.RootObjects)
{
    foreach (var candidate in root.GetComponentsInChildren<XEngine.Runtime.MonoBehaviour>(true, true))
    {
        string fullName = candidate.GetType().FullName ?? candidate.GetType().Name;
        if (fullName == "XEngine.Zonezero.Combat.HeroCombatController" && candidate.EnabledInHierarchy)
            controller = candidate;
        else if (fullName == "XEngine.Zonezero.Combat.BattleFollowCamera" && candidate.EnabledInHierarchy)
            rig = candidate;
    }
    if (controller != null && rig != null) break;
}
if (controller == null || rig == null) return "ERROR|missing hero controller or camera rig";
var hero = controller.GameObject;
var animator = hero.GetComponent<XEngine.Runtime.Animator>();
XEngine.Vector.Transform? travelBone = null;
string travelBonePath = "<missing>";
if (animator?.Skeleton != null)
{
    var skeleton = animator.Skeleton;
    for (int i = 0; i < skeleton.Bones.Length; i++)
    {
        var bone = skeleton.Bones[i];
        string path = i < skeleton.Paths.Length ? skeleton.Paths[i] : "";
        if (bone != null && (bone.GameObject.Name.Equals("Bip001", System.StringComparison.OrdinalIgnoreCase)
            || path.EndsWith("/Bip001", System.StringComparison.OrdinalIgnoreCase)))
        {
            travelBone = bone;
            travelBonePath = path;
            break;
        }
    }
}
var rootPosition = hero.Transform.Position;
var rootForward = hero.Transform.Forward;
var bonePosition = travelBone?.Position ?? rootPosition + new XEngine.Vector.Float3(0f, 1f, 0f);
var boneForward = travelBone?.Forward ?? rootForward;
var cameraPosition = rig.Transform.Position;
var cameraForward = rig.Transform.Forward;
var cameraRight = rig.Transform.Right;
var cameraUp = rig.Transform.Up;
var relative = bonePosition - cameraPosition;
float depth = XEngine.Vector.Float3.Dot(relative, cameraForward);
float screenX = depth > 0.0001f ? XEngine.Vector.Float3.Dot(relative, cameraRight) / depth : float.NaN;
float screenY = depth > 0.0001f ? XEngine.Vector.Float3.Dot(relative, cameraUp) / depth : float.NaN;
var inv = System.Globalization.CultureInfo.InvariantCulture;
return "VISUAL|" + travelBonePath + "|"
    + rootPosition.X.ToString("R", inv) + "|" + rootPosition.Y.ToString("R", inv) + "|"
    + rootPosition.Z.ToString("R", inv) + "|" + rootForward.X.ToString("R", inv) + "|"
    + rootForward.Z.ToString("R", inv) + "|" + bonePosition.X.ToString("R", inv) + "|"
    + bonePosition.Y.ToString("R", inv) + "|" + bonePosition.Z.ToString("R", inv) + "|"
    + boneForward.X.ToString("R", inv) + "|" + boneForward.Z.ToString("R", inv) + "|"
    + cameraPosition.X.ToString("R", inv) + "|" + cameraPosition.Y.ToString("R", inv) + "|"
    + cameraPosition.Z.ToString("R", inv) + "|" + cameraForward.X.ToString("R", inv) + "|"
    + cameraForward.Y.ToString("R", inv) + "|" + cameraForward.Z.ToString("R", inv) + "|"
    + screenX.ToString("R", inv) + "|" + screenY.ToString("R", inv) + "|"
    + depth.ToString("R", inv);
'''


LATERAL_STRESS_TEMPLATE = r'''
XEngine.Runtime.MonoBehaviour? controller = null;
XEngine.Runtime.MonoBehaviour? rig = null;
foreach (var root in Scene.Current.RootObjects)
{
    foreach (var candidate in root.GetComponentsInChildren<XEngine.Runtime.MonoBehaviour>(true, true))
    {
        string fullName = candidate.GetType().FullName ?? candidate.GetType().Name;
        if (fullName == "XEngine.Zonezero.Combat.HeroCombatController" && !candidate.IsDisposed)
            controller = candidate;
        else if (fullName == "XEngine.Zonezero.Combat.BattleFollowCamera" && candidate.EnabledInHierarchy)
            rig = candidate;
    }
    if (controller != null && rig != null) break;
}
if (controller == null || rig == null) return "ERROR|missing hero controller or camera rig";
var cc = controller.GameObject.GetComponent<XEngine.Runtime.CharacterController>();
if (cc == null) return "ERROR|hero CharacterController missing";
var yawField = rig.GetType().GetField("YawDeg", System.Reflection.BindingFlags.Public
    | System.Reflection.BindingFlags.Instance);
var runSpeedField = controller.GetType().GetField("RunSpeed", System.Reflection.BindingFlags.Public
    | System.Reflection.BindingFlags.Instance);
if (yawField == null || runSpeedField == null) return "ERROR|required public fields missing";
float yawDeg = (float)(yawField.GetValue(rig) ?? 0f);
float runSpeed = (float)(runSpeedField.GetValue(controller) ?? 0f);
float yawRad = yawDeg * System.MathF.PI / 180f;
var right = new XEngine.Vector.Float3(System.MathF.Cos(yawRad), 0f, -System.MathF.Sin(yawRad));
var start = controller.Transform.Position;
var motorType = controller.GetType().Assembly.GetType("XEngine.Zonezero.Combat.CombatMotor");
if (motorType == null) return "ERROR|CombatMotor type missing";
var moveMethod = motorType.GetMethod("MoveGrounded", System.Reflection.BindingFlags.Public
    | System.Reflection.BindingFlags.Static, null,
    new[] { typeof(XEngine.Runtime.CharacterController), typeof(XEngine.Vector.Float3), typeof(float) }, null);
if (moveMethod == null) return "ERROR|CombatMotor.MoveGrounded missing";
for (int i = 0; i < __STRESS_ITERATIONS__; i++)
    moveMethod.Invoke(null, new object[] { cc, right, runSpeed });
var end = controller.Transform.Position;
var inv = System.Globalization.CultureInfo.InvariantCulture;
return "LATERAL|" + start.X.ToString("R", inv) + "|" + start.Z.ToString("R", inv) + "|"
    + end.X.ToString("R", inv) + "|" + end.Z.ToString("R", inv) + "|"
    + right.X.ToString("R", inv) + "|" + right.Z.ToString("R", inv);
'''


def _state_value(state: Any, name: str) -> Any:
    if not isinstance(state, dict):
        return None
    wanted = name.casefold()
    for key, value in state.items():
        if str(key).casefold() == wanted:
            return value
    return None


def _wait_for_playmode(client: EditorMcp, playing: bool, timeout: float) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last: Any = None
    while time.monotonic() < deadline:
        last = response_value(client.tool("runtime_state", timeout=20))
        if bool(_state_value(last, "isPlaying")) is playing:
            return last if isinstance(last, dict) else {"value": last}
        time.sleep(0.1)
    raise TimeoutError(f"playmode did not become {playing}; last state={last!r}")


def _wait_for_runtime_ready(client: EditorMcp, timeout: float) -> dict[str, int]:
    deadline = time.monotonic() + timeout
    last = ""
    while time.monotonic() < deadline:
        try:
            last = client.eval(READY_CODE, timeout=min(60.0, timeout))
            fields = last.strip().split("|")
            if len(fields) == 4 and fields[0] == "READY":
                result = {
                    "heroesWithCharacterController": int(fields[1]),
                    "cameraRigs": int(fields[2]),
                    "heroControllers": int(fields[3]),
                }
                if all(value == 1 for value in result.values()):
                    return result
        except Exception as exc:  # imports and Start may still be settling
            last = str(exc)
        time.sleep(0.25)
    raise TimeoutError(f"Battle2 runtime did not become ready: {last}")


def _parse_preflight(text: str) -> dict[str, Any]:
    lines = [line for line in text.splitlines() if line]
    if not lines or not lines[0].startswith("PREFLIGHT|"):
        raise ValueError(f"unexpected preflight response: {text!r}")
    fields = lines[0].split("|", 6)
    if len(fields) != 7:
        raise ValueError(f"malformed preflight response: {lines[0]!r}")
    forbidden: list[dict[str, str]] = []
    for line in lines[1:]:
        parts = line.split("|", 3)
        if len(parts) == 4 and parts[0] == "FORBIDDEN":
            forbidden.append({"root": parts[1], "gameObject": parts[2], "type": parts[3]})
    return {
        "enabledHeroControllers": int(fields[1]),
        "enabledBattleFollowCameras": int(fields[2]),
        "heroCharacterControllers": int(fields[3]),
        "enabledForbiddenCount": int(fields[4]),
        "productionDirectionHelperAvailable": fields[5] == "1",
        "productionDirectionHelper": fields[6],
        "enabledForbiddenComponents": forbidden,
    }


def _parse_production_state(text: str, elapsed: float) -> dict[str, Any]:
    fields = text.strip().split("|")
    frame: int | None = None
    if fields and fields[0] == "FRAME":
        if len(fields) != 46:
            raise ValueError(f"unexpected production-frame response: {text!r}")
        frame = int(fields[1])
        fields = ["STATE", *fields[2:]]
    if len(fields) != 45 or fields[0] != "STATE":
        raise ValueError(f"unexpected production-state response: {text!r}")
    numeric = _floats(fields[1:13])
    bone_and_camera = _floats(fields[15:25])
    return {
        "elapsedSeconds": elapsed,
        "frame": frame,
        "input": numeric[0:2],
        "resolvedWishXZ": numeric[2:4],
        "rootPosition": numeric[4:7],
        "heroForwardXZ": numeric[7:9],
        "characterControllerRequestedMotion": numeric[9:12],
        "collisionFlags": int(fields[13]),
        "travelBonePath": fields[14],
        "travelBoneWorldPosition": bone_and_camera[0:3],
        "travelBoneLocalPosition": bone_and_camera[3:6],
        "cameraYawDeg": bone_and_camera[6],
        "cameraForward": bone_and_camera[7:10],
        "activeAction": fields[25],
        "clip": fields[26],
        "pelvisBonePath": fields[27],
        "pelvisWorldPosition": _floats(fields[28:31]),
        "pelvisLocalPosition": _floats(fields[31:34]),
        "primaryBodyRendererCount": int(fields[34]),
        "primaryBodyBoundsCenter": _floats(fields[35:38]),
        "primaryBodyBoundsSize": _floats(fields[38:41]),
        "cameraPitchDeg": float(fields[41]),
        "cameraUp": _floats(fields[42:45]),
    }


def _floats(fields: list[str]) -> list[float]:
    return [float(value) for value in fields]


def _parse_action_frame(text: str) -> dict[str, Any]:
    fields = text.strip().split("|")
    if len(fields) != 38 or fields[0] != "ACTIONFRAME":
        raise ValueError(f"unexpected action-frame response: {text!r}")
    return {
        "frame": int(fields[1]),
        "rootPosition": _floats(fields[2:5]),
        "heroForwardXZ": _floats(fields[5:7]),
        "characterControllerRequestedMotion": _floats(fields[7:10]),
        "travelBonePath": fields[10],
        "travelBoneWorldPosition": _floats(fields[11:14]),
        "travelBoneLocalPosition": _floats(fields[14:17]),
        "travelBoneForwardXZ": _floats(fields[17:19]),
        "activeAction": fields[19],
        "clip": fields[20],
        "normalizedTime": float(fields[21]),
        "lockedTarget": fields[22],
        "lockedTargetPosition": _floats(fields[23:26]),
        "lungeRemaining": float(fields[26]),
        "lungeDirectionXZ": _floats(fields[27:29]),
        "skillLungeSpeed": float(fields[29]),
        "skillLungeDuration": float(fields[30]),
        "applyRootMotion": fields[31] == "1",
        "screenProxy": _floats(fields[32:35]),
        "cameraPosition": _floats(fields[35:38]),
    }


def _parse_action_renderer_evidence(text: str) -> dict[str, Any]:
    hero_position: list[float] | None = None
    expected_renderer_count: int | None = None
    renderers: list[dict[str, Any]] = []
    bip001_bindings: list[dict[str, Any]] = []
    key_bones: list[dict[str, Any]] = []
    for line in (line for line in text.splitlines() if line):
        fields = line.split("|")
        if fields[0] == "ERROR":
            raise RuntimeError("renderer evidence failed: " + "|".join(fields[1:]))
        if fields[0] == "HERO":
            if len(fields) != 5:
                raise ValueError(f"unexpected hero renderer-evidence response: {line!r}")
            hero_position = _floats(fields[1:4])
            expected_renderer_count = int(fields[4])
        elif fields[0] == "RENDERER":
            if len(fields) != 21:
                raise ValueError(f"unexpected renderer-evidence response: {line!r}")
            renderers.append(
                {
                    "index": int(fields[1]),
                    "gameObject": fields[2],
                    "enabledInHierarchy": fields[3] == "1",
                    "authoredRootBonePath": fields[4],
                    "resolvedRootBonePath": fields[5],
                    "rootBoneWorldPosition": _floats(fields[6:9]),
                    "worldBoundsCenter": _floats(fields[9:12]),
                    "worldBoundsSize": _floats(fields[12:15]),
                    "boundsCenterScreenProxy": _floats(fields[15:18]),
                    "boneCount": int(fields[18]),
                    "unresolvedBoneCount": int(fields[19]),
                    "bip001BindingCount": int(fields[20]),
                }
            )
        elif fields[0] == "BIP001":
            if len(fields) != 10:
                raise ValueError(f"unexpected Bip001 renderer-evidence response: {line!r}")
            bip001_bindings.append(
                {
                    "source": fields[1],
                    "index": int(fields[2]),
                    "path": fields[3],
                    "worldPosition": _floats(fields[4:7]),
                    "localPosition": _floats(fields[7:10]),
                }
            )
        elif fields[0] == "KEYBONE":
            if len(fields) != 25:
                raise ValueError(f"unexpected key-bone renderer-evidence response: {line!r}")
            key_bones.append(
                {
                    "index": int(fields[1]),
                    "path": fields[2],
                    "name": fields[3],
                    "worldPosition": _floats(fields[4:7]),
                    "localPosition": _floats(fields[7:10]),
                    "worldRotation": _floats(fields[10:14]),
                    "localRotation": _floats(fields[14:18]),
                    "referenceLocalPosition": _floats(fields[18:21]),
                    "referenceLocalRotation": _floats(fields[21:25]),
                }
            )
        else:
            raise ValueError(f"unexpected renderer-evidence line: {line!r}")

    if hero_position is None or expected_renderer_count is None:
        raise ValueError("renderer evidence did not include the hero root")
    if len(renderers) != expected_renderer_count:
        raise ValueError(
            f"renderer evidence expected {expected_renderer_count} renderers, got {len(renderers)}"
        )

    enabled = [renderer for renderer in renderers if renderer["enabledInHierarchy"]]
    for renderer in renderers:
        root = renderer["rootBoneWorldPosition"]
        center = renderer["worldBoundsCenter"]
        root_delta = [root[index] - hero_position[index] for index in range(3)]
        bounds_delta = [center[index] - hero_position[index] for index in range(3)]
        renderer["rootBoneOffsetFromHero"] = root_delta
        renderer["rootBoneHorizontalOffsetFromHero"] = math.hypot(
            root_delta[0], root_delta[2]
        )
        renderer["rootBoneWorldDistanceFromHero"] = math.sqrt(
            sum(value * value for value in root_delta)
        )
        renderer["boundsCenterOffsetFromHero"] = bounds_delta
        renderer["boundsCenterHorizontalOffsetFromHero"] = math.hypot(
            bounds_delta[0], bounds_delta[2]
        )
        renderer["boundsCenterWorldDistanceFromHero"] = math.sqrt(
            sum(value * value for value in bounds_delta)
        )
    for bone in key_bones:
        world = bone["worldPosition"]
        local = bone["localPosition"]
        reference = bone["referenceLocalPosition"]
        world_delta = [world[index] - hero_position[index] for index in range(3)]
        reference_delta = [local[index] - reference[index] for index in range(3)]
        bone["worldOffsetFromHero"] = world_delta
        bone["horizontalDistanceFromHero"] = math.hypot(
            world_delta[0], world_delta[2]
        )
        bone["worldDistanceFromHero"] = math.sqrt(
            sum(value * value for value in world_delta)
        )
        bone["localOffsetFromReference"] = reference_delta
        bone["localDistanceFromReference"] = math.sqrt(
            sum(value * value for value in reference_delta)
        )
    primary_body = [
        renderer
        for renderer in enabled
        if renderer["authoredRootBonePath"].replace("\\", "/").casefold().endswith(
            "/bip001 pelvis"
        )
    ]
    named_key_bones = {
        name: next(
            (
                bone
                for bone in key_bones
                if bone["name"].casefold() == runtime_name.casefold()
            ),
            None,
        )
        for name, runtime_name in (
            ("bip001", "Bip001"),
            ("pelvis", "Bip001 Pelvis"),
        )
    }
    metrics = {
        "maxRendererRootBoneHorizontalOffset": max(
            (renderer["rootBoneHorizontalOffsetFromHero"] for renderer in enabled),
            default=0.0,
        ),
        "maxRendererRootBoneWorldDistance": max(
            (renderer["rootBoneWorldDistanceFromHero"] for renderer in enabled),
            default=0.0,
        ),
        "maxRendererBoundsCenterHorizontalOffset": max(
            (
                renderer["boundsCenterHorizontalOffsetFromHero"]
                for renderer in enabled
            ),
            default=0.0,
        ),
        "maxRendererBoundsCenterWorldDistance": max(
            (renderer["boundsCenterWorldDistanceFromHero"] for renderer in enabled),
            default=0.0,
        ),
        "maxPrimaryBodyBoundsCenterHorizontalOffset": max(
            (
                renderer["boundsCenterHorizontalOffsetFromHero"]
                for renderer in primary_body
            ),
            default=0.0,
        ),
        "maxPrimaryBodyBoundsCenterWorldDistance": max(
            (renderer["boundsCenterWorldDistanceFromHero"] for renderer in primary_body),
            default=0.0,
        ),
        "maxRendererBoundsExtent": max(
            (max(renderer["worldBoundsSize"]) for renderer in enabled),
            default=0.0,
        ),
        "maxPrimaryBodyBoundsExtent": max(
            (max(renderer["worldBoundsSize"]) for renderer in primary_body),
            default=0.0,
        ),
        "maxKeyBoneWorldDistance": max(
            (bone["worldDistanceFromHero"] for bone in key_bones),
            default=0.0,
        ),
        "maxKeyBoneLocalReferenceExcursion": max(
            (bone["localDistanceFromReference"] for bone in key_bones),
            default=0.0,
        ),
        "allEvidenceFinite": all(
            _is_finite_vector(vector)
            for renderer in enabled
            for vector in (
                renderer["rootBoneWorldPosition"],
                renderer["worldBoundsCenter"],
                renderer["worldBoundsSize"],
                renderer["boundsCenterScreenProxy"],
            )
        )
        and all(
            _is_finite_vector(vector)
            for bone in key_bones
            for vector in (
                bone["worldPosition"],
                bone["localPosition"],
                bone["worldRotation"],
                bone["localRotation"],
                bone["referenceLocalPosition"],
                bone["referenceLocalRotation"],
            )
        ),
        "primaryBodyRendererCount": len(primary_body),
        "unresolvedBoneCount": sum(
            renderer["unresolvedBoneCount"] for renderer in enabled
        ),
        "bip001BindingCount": len(bip001_bindings),
        "bip001KeyBoneCount": sum(
            bone["name"].casefold() == "bip001" for bone in key_bones
        ),
        "pelvisKeyBoneCount": sum(
            bone["name"].casefold() == "bip001 pelvis" for bone in key_bones
        ),
    }
    return {
        "heroPosition": hero_position,
        "rendererCount": len(renderers),
        "enabledRendererCount": len(enabled),
        "renderers": renderers,
        "bip001Bindings": bip001_bindings,
        "keyBones": key_bones,
        "distanceSummary": {
            "heroWorldPosition": hero_position,
            "bip001": named_key_bones["bip001"],
            "pelvis": named_key_bones["pelvis"],
            "rendererBounds": {
                "maximumHorizontalDistanceFromHero": metrics[
                    "maxRendererBoundsCenterHorizontalOffset"
                ],
                "maximumWorldDistanceFromHero": metrics[
                    "maxRendererBoundsCenterWorldDistance"
                ],
                "primaryBodyHorizontalDistanceFromHero": metrics[
                    "maxPrimaryBodyBoundsCenterHorizontalOffset"
                ],
                "primaryBodyWorldDistanceFromHero": metrics[
                    "maxPrimaryBodyBoundsCenterWorldDistance"
                ],
            },
        },
        "metrics": metrics,
    }


def _parse_action_curve_evidence(text: str) -> dict[str, Any]:
    states: dict[str, dict[str, Any]] = {}
    for line in (line for line in text.splitlines() if line):
        fields = line.split("|")
        if fields[0] == "ERROR":
            raise RuntimeError("action curve evidence failed: " + "|".join(fields[1:]))
        if fields[0] == "STATECURVES":
            if len(fields) != 6:
                raise ValueError(f"unexpected state-curve summary: {line!r}")
            entry = states.setdefault(fields[1], {"reportedTranslationBones": []})
            entry.update(
                {
                    "clip": fields[2],
                    "rootBonePath": fields[3],
                    "largeTranslationBoneCount": int(fields[4]),
                    "totalBoneCount": int(fields[5]),
                }
            )
        elif fields[0] == "CURVE":
            if len(fields) != 21:
                raise ValueError(f"unexpected action-curve evidence: {line!r}")
            ranges = _floats(fields[8:17])
            entry = states.setdefault(fields[1], {"reportedTranslationBones": []})
            entry["reportedTranslationBones"].append(
                {
                    "clip": fields[2],
                    "rootBonePath": fields[3],
                    "duration": float(fields[4]),
                    "boneIndex": int(fields[5]),
                    "bonePath": fields[6],
                    "depth": int(fields[7]),
                    "positionX": {
                        "min": ranges[0],
                        "max": ranges[1],
                        "range": ranges[2],
                    },
                    "positionY": {
                        "min": ranges[3],
                        "max": ranges[4],
                        "range": ranges[5],
                    },
                    "positionZ": {
                        "min": ranges[6],
                        "max": ranges[7],
                        "range": ranges[8],
                    },
                    "hasSourceReferencePose": fields[17] == "1",
                    "sourcePosition": _floats(fields[18:21]),
                }
            )
        else:
            raise ValueError(f"unexpected action-curve line: {line!r}")

    for state in states.values():
        state["reportedTranslationBones"].sort(
            key=lambda bone: max(
                bone["positionX"]["range"],
                bone["positionY"]["range"],
                bone["positionZ"]["range"],
            ),
            reverse=True,
        )
        state["largeTranslationBones"] = [
            bone
            for bone in state["reportedTranslationBones"]
            if max(
                bone["positionX"]["range"],
                bone["positionY"]["range"],
                bone["positionZ"]["range"],
            )
            > 0.5
        ]
    return {"rangeThresholdMetersExclusive": 0.5, "states": states}


def _parse_actor_state_samples(text: str) -> list[dict[str, Any]]:
    frames_by_number: dict[int, dict[str, Any]] = {}
    for line in (line for line in text.splitlines() if line):
        fields = line.split("|")
        if fields[0] == "ERROR":
            raise RuntimeError("actor-state probe failed: " + "|".join(fields[1:]))
        if fields[0] in {"DONE", "ACTOR_CAPTURE"}:
            continue
        if fields[0] == "ACTORFRAME":
            if len(fields) != 38:
                raise ValueError(f"unexpected actor-state frame: {line!r}")
            frame_number = int(fields[1])
            frames_by_number[frame_number] = {
                "frame": frame_number,
                "stateObserved": fields[2] == "1",
                "normalizedTime": float(fields[3]),
                "applyRootMotion": fields[4] == "1",
                "rootPosition": _floats(fields[5:8]),
                "bip001WorldPosition": _floats(fields[8:11]),
                "bip001LocalPosition": _floats(fields[11:14]),
                "bip001ReferenceLocalPosition": _floats(fields[14:17]),
                "pelvisWorldPosition": _floats(fields[17:20]),
                "pelvisLocalPosition": _floats(fields[20:23]),
                "pelvisReferenceLocalPosition": _floats(fields[23:26]),
                "primaryBodyRendererCount": int(fields[26]),
                "primaryBodyBoundsCenter": _floats(fields[27:30]),
                "primaryBodyBoundsSize": _floats(fields[30:33]),
                "enabledRendererCount": int(fields[33]),
                "unresolvedBoneCount": int(fields[34]),
                "clip": fields[35],
                "engineFrame": int(fields[36]),
                "sampledWhilePaused": fields[37] == "1",
                "corePose": {},
                "renderers": [],
            }
        elif fields[0] == "FACINGFRAME":
            if len(fields) != 11:
                raise ValueError(f"unexpected anatomical facing frame: {line!r}")
            frames_by_number[int(fields[1])].update({
                "leftThighWorldPosition": _floats(fields[2:5]),
                "rightThighWorldPosition": _floats(fields[5:8]),
                "spineWorldPosition": _floats(fields[8:11]),
            })
        elif fields[0] == "ACTOR_STEP":
            if len(fields) != 8:
                raise ValueError(f"unexpected controlled evaluation step: {line!r}")
            frames_by_number[int(fields[1])]["controlledEvaluation"] = {
                "deltaSeconds": float(fields[2]),
                "normalizedTimeBefore": float(fields[3]),
                "clipDurationSeconds": float(fields[4]),
                "playbackSpeed": float(fields[5]),
                "presentationBefore": int(fields[6]),
                "presentationAfter": int(fields[7]),
            }
        elif fields[0] == "ACTORRENDERER":
            if len(fields) != 17:
                raise ValueError(f"unexpected per-renderer visual frame: {line!r}")
            frames_by_number[int(fields[1])]["renderers"].append({
                "index": int(fields[2]), "name": fields[3], "primary": fields[4] == "1",
                "boneCount": int(fields[5]), "unresolvedBoneCount": int(fields[6]),
                "hasRootBone": fields[7] == "1", "rootPosition": _floats(fields[8:11]),
                "boundsCenter": _floats(fields[11:14]), "boundsSize": _floats(fields[14:17]),
            })
        elif fields[0] == "ACTORPOSE":
            if len(fields) != 11:
                raise ValueError(f"unexpected actor-state pose: {line!r}")
            frame_number = int(fields[1])
            frame = frames_by_number.get(frame_number)
            if frame is None:
                raise ValueError(f"actor pose preceded its frame: {line!r}")
            frame["corePose"][fields[2]] = {
                "rotation": _floats(fields[3:7]),
                "referenceRotation": _floats(fields[7:11]),
            }
        else:
            raise ValueError(f"unexpected actor-state probe line: {line!r}")
    frames = sorted(frames_by_number.values(), key=lambda frame: frame["frame"])
    if not frames:
        raise RuntimeError("actor-state probe returned no frames")
    return frames


def _is_finite_vector(values: list[float]) -> bool:
    return bool(values) and all(math.isfinite(value) for value in values)


def _distance3(left: list[float], right: list[float]) -> float:
    return math.sqrt(sum((left[index] - right[index]) ** 2 for index in range(3)))


def _quaternion_angle_deg(left: list[float], right: list[float]) -> float:
    if len(left) != 4 or len(right) != 4 or not all(math.isfinite(v) for v in [*left, *right]):
        return 0.0
    left_length = math.sqrt(sum(value * value for value in left))
    right_length = math.sqrt(sum(value * value for value in right))
    if left_length <= 1e-9 or right_length <= 1e-9:
        return 180.0
    dot = sum(left[index] * right[index] for index in range(4)) / (
        left_length * right_length
    )
    # q and -q represent the same orientation.
    return math.degrees(2.0 * math.acos(max(-1.0, min(1.0, abs(dot)))))


def _nearest_normalized_sample(
    frames: list[dict[str, Any]], target: float
) -> dict[str, Any]:
    return min(frames, key=lambda frame: abs(frame["normalizedTime"] - target))


def _summarize_failures(failures: list[str]) -> list[str]:
    grouped: dict[tuple[str, str, str], list[int]] = {}
    order: list[str | tuple[str, str, str]] = []
    seen: set[str] = set()
    for failure in failures:
        match = re.match(r"^(.*?)(frame|sample) (\d+) (.*)$", failure)
        if match:
            key = (match[1], match[2], match[4])
            if key not in grouped:
                grouped[key] = []
                order.append(key)
            grouped[key].append(int(match[3]))
        elif failure not in seen:
            order.append(failure)
            seen.add(failure)
    result = []
    for item in order:
        if isinstance(item, str):
            result.append(item)
            continue
        indices = grouped[item]
        if len(indices) == 1:
            result.append(f"{item[0]}{item[1]} {indices[0]} {item[2]}")
        else:
            result.append(f"{item[0]}{item[1]}s {min(indices)}-{max(indices)} "
                          f"(count={len(indices)}): {item[2]}")
    return result


def _analyse_actor_state(
    actor: str, state: str, frames: list[dict[str, Any]],
    *, stationary_root: bool = True, require_full_state: bool = True,
    controlled_evaluation: bool = False,
) -> tuple[dict[str, Any], list[str]]:
    observed = [frame for frame in frames if frame["stateObserved"]]
    failures: list[str] = []
    scope = f"{actor}/{state}"
    if not observed:
        return {"sampleCount": len(frames), "stateObserved": False}, [
            f"{scope}: requested Animator state was never observed"
        ]

    if controlled_evaluation:
        if not all(frame["sampledWhilePaused"] for frame in observed):
            failures.append(f"{scope}: controlled evaluation sampled while gameplay was running")
        for index, frame in enumerate(observed):
            step = frame.get("controlledEvaluation")
            if step is None:
                failures.append(f"{scope}: frame {frame['frame']} is missing controlled evaluation telemetry")
                continue
            values = [step[key] for key in ("deltaSeconds", "normalizedTimeBefore", "clipDurationSeconds", "playbackSpeed")]
            if not all(math.isfinite(value) for value in values) or any(values[i] <= 0 for i in (0, 2, 3)):
                failures.append(f"{scope}: frame {frame['frame']} contains invalid controlled evaluation timing")
                continue
            advance = frame["normalizedTime"] - step["normalizedTimeBefore"]
            expected = step["deltaSeconds"] * step["playbackSpeed"] / step["clipDurationSeconds"]
            if not 0 < advance <= ISOLATED_NORMALIZED_STEP + ISOLATED_NORMALIZED_TOLERANCE:
                failures.append(f"{scope}: frame {frame['frame']} exceeded the controlled normalized step")
            if abs(advance - expected) > ISOLATED_NORMALIZED_TOLERANCE:
                failures.append(f"{scope}: frame {frame['frame']} animation time disagrees with EvaluateGraph delta")
            if index and abs(step["normalizedTimeBefore"] - observed[index - 1]["normalizedTime"]) > ISOLATED_NORMALIZED_TOLERANCE:
                failures.append(f"{scope}: frame {frame['frame']} advanced outside controlled evaluation")
            if step["presentationAfter"] <= step["presentationBefore"]:
                failures.append(f"{scope}: frame {frame['frame']} was sampled before its evaluated pose was presented")
    elif any(frame["sampledWhilePaused"] for frame in observed):
        failures.append(f"{scope}: a visual sample was recorded while gameplay was paused")
    if any(current["engineFrame"] <= previous["engineFrame"]
           for previous, current in zip(observed, observed[1:])):
        failures.append(f"{scope}: engine frame IDs do not advance strictly")

    baseline = observed[0]
    normalized_values = [frame["normalizedTime"] for frame in observed]
    if not all(math.isfinite(value) for value in normalized_values):
        failures.append(f"{scope}: non-finite normalized animation time")
    normalized_advance = max(normalized_values) - min(normalized_values)
    root_path = 0.0
    max_bip_world_distance = 0.0
    max_pelvis_world_distance = 0.0
    max_bip_reference_excursion = 0.0
    max_pelvis_reference_excursion = 0.0
    max_bounds_world_offset = 0.0
    max_bounds_relative_excursion = 0.0
    max_bounds_extent = 0.0
    previous_root = baseline["rootPosition"]
    baseline_bounds_relative = [
        baseline["primaryBodyBoundsCenter"][index] - baseline["rootPosition"][index]
        for index in range(3)
    ]

    for frame in observed:
        vectors = (
            frame["rootPosition"],
            frame["bip001WorldPosition"],
            frame["bip001LocalPosition"],
            frame["bip001ReferenceLocalPosition"],
            frame["pelvisWorldPosition"],
            frame["pelvisLocalPosition"],
            frame["pelvisReferenceLocalPosition"],
            frame["primaryBodyBoundsCenter"],
            frame["primaryBodyBoundsSize"],
        )
        if not all(_is_finite_vector(vector) for vector in vectors):
            failures.append(f"{scope}: frame {frame['frame']} contains non-finite transform/bounds data")
            continue
        root_path += _distance3(frame["rootPosition"], previous_root)
        previous_root = frame["rootPosition"]
        max_bip_world_distance = max(
            max_bip_world_distance,
            _distance3(frame["bip001WorldPosition"], frame["rootPosition"]),
        )
        max_pelvis_world_distance = max(
            max_pelvis_world_distance,
            _distance3(frame["pelvisWorldPosition"], frame["rootPosition"]),
        )
        max_bip_reference_excursion = max(
            max_bip_reference_excursion,
            _distance3(
                frame["bip001LocalPosition"], frame["bip001ReferenceLocalPosition"]
            ),
        )
        max_pelvis_reference_excursion = max(
            max_pelvis_reference_excursion,
            _distance3(
                frame["pelvisLocalPosition"], frame["pelvisReferenceLocalPosition"]
            ),
        )
        bounds_relative = [
            frame["primaryBodyBoundsCenter"][index] - frame["rootPosition"][index]
            for index in range(3)
        ]
        max_bounds_world_offset = max(
            max_bounds_world_offset,
            math.sqrt(sum(value * value for value in bounds_relative)),
        )
        max_bounds_relative_excursion = max(
            max_bounds_relative_excursion,
            _distance3(bounds_relative, baseline_bounds_relative),
        )
        max_bounds_extent = max(max_bounds_extent, *frame["primaryBodyBoundsSize"])
        if any(size <= 1e-5 for size in frame["primaryBodyBoundsSize"]):
            failures.append(f"{scope}: frame {frame['frame']} has an empty primary-body bounds axis")
        if frame["primaryBodyRendererCount"] <= 0:
            failures.append(f"{scope}: frame {frame['frame']} has no primary body renderer")
        if frame["enabledRendererCount"] <= 0:
            failures.append(f"{scope}: frame {frame['frame']} has no enabled renderer")
        if frame["unresolvedBoneCount"] != 0:
            failures.append(
                f"{scope}: frame {frame['frame']} has "
                f"{frame['unresolvedBoneCount']} unresolved renderer bones"
            )
        for name, pose in frame["corePose"].items():
            if any(not _is_finite_vector(pose[field]) or sum(v * v for v in pose[field]) <= 1e-12
                   for field in ("rotation", "referenceRotation")):
                failures.append(f"{scope}: frame {frame['frame']} has invalid quaternion for {name}")
        if len(frame["renderers"]) != frame["enabledRendererCount"]:
            failures.append(f"{scope}: frame {frame['frame']} renderer coverage is incomplete")
        for renderer in frame["renderers"]:
            label = f"{scope}: frame {frame['frame']} renderer {renderer['name']}"
            if not renderer["hasRootBone"] or renderer["boneCount"] <= 0 or renderer["unresolvedBoneCount"]:
                failures.append(label + " has missing root/skinning bones")
            if not all(_is_finite_vector(renderer[field]) for field in ("rootPosition", "boundsCenter", "boundsSize")):
                failures.append(label + " has NaN/Infinity")
                continue
            if renderer["primary"]:
                if any(value <= 1e-5 for value in renderer["boundsSize"]) or max(renderer["boundsSize"]) > THRESHOLDS["primaryBodyBoundsExtentMax"]:
                    failures.append(label + " has empty or oversized primary body bounds")
                if _distance3(renderer["boundsCenter"], frame["rootPosition"]) > THRESHOLDS["primaryBodyBoundsWorldOffsetMax"]:
                    failures.append(label + " bounds center is detached from actor root")
                if _distance3(renderer["rootPosition"], frame["rootPosition"]) > THRESHOLDS["keyBoneWorldDistanceFromActorMax"]:
                    failures.append(label + " RootBone is detached from actor root")
        missing_pose = [name for name in CORE_POSE_BONES if name not in frame["corePose"]]
        if missing_pose:
            failures.append(
                f"{scope}: frame {frame['frame']} is missing core pose bones {missing_pose}"
            )

    if any(frame["applyRootMotion"] for frame in observed):
        failures.append(f"{scope}: Animator.ApplyRootMotion became true")
    if require_full_state and normalized_advance < THRESHOLDS["poseNormalisedAdvanceMin"]:
        failures.append(
            f"{scope}: normalized time advanced only {normalized_advance:.6f}; "
            "the state did not demonstrably run"
        )
    if stationary_root and root_path > THRESHOLDS["nonLungeRootTravelMax"]:
        failures.append(
            f"{scope}: isolated animation moved actor root {root_path:.6f}m"
        )
    if max_bip_world_distance > THRESHOLDS["keyBoneWorldDistanceFromActorMax"]:
        failures.append(
            f"{scope}: Bip001 reached {max_bip_world_distance:.6f}m from actor root"
        )
    if max_pelvis_world_distance > THRESHOLDS["keyBoneWorldDistanceFromActorMax"]:
        failures.append(
            f"{scope}: Pelvis reached {max_pelvis_world_distance:.6f}m from actor root"
        )
    if max_bip_reference_excursion > THRESHOLDS["keyBoneLocalReferenceExcursionMax"]:
        failures.append(
            f"{scope}: Bip001 local/reference excursion is {max_bip_reference_excursion:.6f}m"
        )
    if max_pelvis_reference_excursion > THRESHOLDS["keyBoneLocalReferenceExcursionMax"]:
        failures.append(
            f"{scope}: Pelvis local/reference excursion is {max_pelvis_reference_excursion:.6f}m"
        )
    if max_bounds_world_offset > THRESHOLDS["primaryBodyBoundsWorldOffsetMax"]:
        failures.append(
            f"{scope}: primary-body bounds center reached {max_bounds_world_offset:.6f}m "
            "from actor root"
        )
    if max_bounds_relative_excursion > THRESHOLDS["visualRelativeOffsetExcursionMax"]:
        failures.append(
            f"{scope}: primary-body bounds/root relative excursion is "
            f"{max_bounds_relative_excursion:.6f}m"
        )
    if max_bounds_extent > THRESHOLDS["primaryBodyBoundsExtentMax"]:
        failures.append(
            f"{scope}: primary-body bounds extent reached {max_bounds_extent:.6f}m"
        )

    reference_angles: dict[str, float] = {}
    dynamic_angles: dict[str, float] = {}
    first_pose = observed[0]["corePose"]
    for bone_name in CORE_POSE_BONES:
        reference_angles[bone_name] = max(
            (
                _quaternion_angle_deg(
                    frame["corePose"][bone_name]["rotation"],
                    frame["corePose"][bone_name]["referenceRotation"],
                )
                for frame in observed
                if bone_name in frame["corePose"]
            ),
            default=0.0,
        )
        if bone_name in first_pose:
            dynamic_angles[bone_name] = max(
                (
                    _quaternion_angle_deg(
                        first_pose[bone_name]["rotation"],
                        frame["corePose"][bone_name]["rotation"],
                    )
                    for frame in observed
                    if bone_name in frame["corePose"]
                ),
                default=0.0,
            )
        else:
            dynamic_angles[bone_name] = 0.0

    idle = state == "Idle"
    reference_threshold = 0.25 if idle else THRESHOLDS["poseReferenceAngleMinDeg"]
    dynamic_threshold = 0.05 if idle else THRESHOLDS["poseDynamicAngleMinDeg"]
    required_bones = 1 if idle else 2
    reference_moving = sum(angle > reference_threshold for angle in reference_angles.values())
    dynamically_moving = sum(angle > dynamic_threshold for angle in dynamic_angles.values())
    if reference_moving < required_bones:
        failures.append(
            f"{scope}: only {reference_moving} core bones depart from reference pose; "
            "possible T-pose/static binding"
        )
    if dynamically_moving < required_bones:
        failures.append(
            f"{scope}: only {dynamically_moving} core bones change over time; "
            "possible T-pose/static animation"
        )

    sampled = []
    for target in (0.2, 0.5, 0.8):
        sample = _nearest_normalized_sample(observed, target)
        sampled.append(
            {
                "targetNormalizedTime": target,
                "frame": sample["frame"],
                "actualNormalizedTime": sample["normalizedTime"],
                "rootPosition": sample["rootPosition"],
                "bip001WorldPosition": sample["bip001WorldPosition"],
                "pelvisWorldPosition": sample["pelvisWorldPosition"],
                "primaryBodyBoundsCenter": sample["primaryBodyBoundsCenter"],
                "primaryBodyBoundsSize": sample["primaryBodyBoundsSize"],
            }
        )
    metrics = {
        "evaluationMode": ISOLATED_EVALUATION_MODE if controlled_evaluation else "liveGameplayFrames",
        "sampleCount": len(frames),
        "observedSampleCount": len(observed),
        "normalizedTimeRange": [min(normalized_values), max(normalized_values)],
        "normalizedTimeAdvance": normalized_advance,
        "rootPathTravel": root_path,
        "maxBip001WorldDistanceFromActor": max_bip_world_distance,
        "maxPelvisWorldDistanceFromActor": max_pelvis_world_distance,
        "maxBip001LocalReferenceExcursion": max_bip_reference_excursion,
        "maxPelvisLocalReferenceExcursion": max_pelvis_reference_excursion,
        "maxPrimaryBodyBoundsWorldOffset": max_bounds_world_offset,
        "maxPrimaryBodyBoundsRelativeExcursion": max_bounds_relative_excursion,
        "maxPrimaryBodyBoundsExtent": max_bounds_extent,
        "coreBoneReferenceAnglesDeg": reference_angles,
        "coreBoneDynamicAnglesDeg": dynamic_angles,
        "referenceMovingBoneCount": reference_moving,
        "dynamicallyMovingBoneCount": dynamically_moving,
        "sampledKeyframes": sampled,
    }
    return metrics, _summarize_failures(failures)


def _parse_probe(text: str) -> dict[str, Any]:
    fields = text.strip().split("|")
    if len(fields) != 17 or fields[0] != "PROBE":
        raise ValueError(f"unexpected probe response: {text!r}")
    values = _floats(fields[2:])
    return {
        "directionSource": fields[1],
        "yawDeg": values[0],
        "resolvedDirection": values[1:3],
        "startPosition": values[3:6],
        "endPosition": values[6:9],
        "heroForward": values[9:11],
        "cameraForwardBefore": values[11:13],
        "cameraForwardAfterCall": values[13:15],
    }


def _parse_camera(text: str, elapsed: float) -> dict[str, Any]:
    fields = text.strip().split("|")
    if len(fields) != 15 or fields[0] != "CAMERA":
        raise ValueError(f"unexpected camera response: {text!r}")
    values = _floats(fields[1:])
    return {
        "elapsedSeconds": elapsed,
        "yawDeg": values[0],
        "pitchDeg": values[1],
        "forward": values[2:5],
        "position": values[5:8],
        "heroPosition": values[8:11],
        "up": values[11:14],
    }


def _parse_visual_direction(text: str) -> dict[str, Any]:
    fields = text.strip().split("|")
    if len(fields) != 21 or fields[0] != "VISUAL":
        raise ValueError(f"unexpected visual-direction response: {text!r}")
    return {
        "travelBonePath": fields[1],
        "rootPosition": _floats(fields[2:5]),
        "rootForwardXZ": _floats(fields[5:7]),
        "travelBoneWorldPosition": _floats(fields[7:10]),
        "travelBoneForwardXZ": _floats(fields[10:12]),
        "cameraPosition": _floats(fields[12:15]),
        "cameraForward": _floats(fields[15:18]),
        "screenProxy": _floats(fields[18:21]),
    }


def _parse_lateral(text: str) -> dict[str, Any]:
    fields = text.strip().split("|")
    if len(fields) != 7 or fields[0] != "LATERAL":
        raise ValueError(f"unexpected lateral-stress response: {text!r}")
    values = _floats(fields[1:])
    dx = values[2] - values[0]
    dz = values[3] - values[1]
    return {
        "startPositionXZ": values[0:2],
        "endPositionXZ": values[2:4],
        "expectedRightXZ": values[4:6],
        "horizontalTravel": math.hypot(dx, dz),
    }


def _normalise_xz(vector: list[float] | tuple[float, float]) -> tuple[float, float]:
    length = math.hypot(vector[0], vector[1])
    if length <= 1e-9:
        return (0.0, 0.0)
    return (vector[0] / length, vector[1] / length)


def _dot(left: tuple[float, float], right: tuple[float, float]) -> float:
    return left[0] * right[0] + left[1] * right[1]


def _angle_deg(left: tuple[float, float], right: tuple[float, float]) -> float:
    left = _normalise_xz(left)
    right = _normalise_xz(right)
    if left == (0.0, 0.0) or right == (0.0, 0.0):
        return 180.0
    return math.degrees(math.acos(max(-1.0, min(1.0, _dot(left, right)))))


def _wrapped_angle_delta_deg(left: float, right: float) -> float:
    return abs((left - right + 180.0) % 360.0 - 180.0)


def _vector_angle_deg3(left: list[float], right: list[float]) -> float:
    if len(left) != 3 or len(right) != 3 or not all(math.isfinite(v) for v in [*left, *right]):
        return 180.0
    left_length = math.sqrt(sum(value * value for value in left))
    right_length = math.sqrt(sum(value * value for value in right))
    if left_length <= 1e-9 or right_length <= 1e-9:
        return 180.0
    dot = sum(left[index] * right[index] for index in range(3)) / (
        left_length * right_length
    )
    return math.degrees(math.acos(max(-1.0, min(1.0, dot))))


def _expected_direction(yaw_deg: float, input_xy: tuple[float, float]) -> tuple[float, float]:
    yaw = math.radians(yaw_deg)
    forward = (math.sin(yaw), math.cos(yaw))
    right = (math.cos(yaw), -math.sin(yaw))
    return _normalise_xz(
        (
            forward[0] * input_xy[1] + right[0] * input_xy[0],
            forward[1] * input_xy[1] + right[1] * input_xy[0],
        )
    )



def _visual_observer_factory() -> str:
    """Read-only frame telemetry, shared by production input and actor asset probes."""
    setup = ACTOR_STATE_COROUTINE_TEMPLATE.split("var skeleton = animator.Skeleton;", 1)[1]
    setup = "var skeleton = animator.Skeleton;" + setup.split("async XEngine.Async.XTaskVoid RunActorStateProbe()", 1)[0]
    setup = setup.replace(
        'var samples = new System.Collections.Concurrent.ConcurrentQueue<string>();\n'
        'System.AppDomain.CurrentDomain.SetData(storageKey, samples);\n', "")
    setup = setup.replace('bool observed = stateInfo.IsName(stateName);', 'bool observed = true;')
    setup = re.sub(r'return ("ERROR\|[^;]+);', r'throw new System.InvalidOperationException(\1);', setup)
    setup = setup.replace("__CORE_BONE_NAMES__", ", ".join(json.dumps(name) for name in CORE_POSE_BONES))
    state_literals = ", ".join(json.dumps(name) for name in sorted({name for spec in ACTOR_STATES.values() for name in spec["states"]}))
    setup = setup.replace('string clip = animator.CurrentClip?.Name ?? "<null>";',
                          'string clip = animator.CurrentClip?.Name ?? "<null>";\n'
                          'foreach (string candidate in new[] { ' + state_literals + ' }) '
                          'if (stateInfo.IsName(candidate)) { clip = candidate; break; }')
    return (
        "System.Action<int> CreateVisualObserver(XEngine.Runtime.GameObject actor, "
        "System.Collections.Concurrent.ConcurrentQueue<string> samples) {\n"
        'var animator = actor.GetComponent<XEngine.Runtime.Animator>();\n'
        'if (animator?.Skeleton == null) throw new System.InvalidOperationException("actor Animator/Skeleton missing");\n'
        'string actorLabel = actor.Name;\n' + setup + "\nreturn Snapshot;\n}\n"
    )


def _attach_visual_frames(samples: list[dict[str, Any]], text: str) -> None:
    visual_text = "\n".join(line for line in text.splitlines() if line.startswith(("ACTORFRAME|", "ACTORPOSE|", "ACTORRENDERER|", "FACINGFRAME|")))
    visual = {frame["frame"]: frame for frame in _parse_actor_state_samples(visual_text)}
    if set(visual) != {frame["frame"] for frame in samples}:
        raise RuntimeError("production and visual frame coverage differ")
    for frame in samples:
        frame["visual"] = visual[frame["frame"]]



NATURAL_OBSERVER_TEMPLATE = r'''
string storageKey = "__STORAGE_KEY__";
__VISUAL_OBSERVER_FACTORY__
var queues = new System.Collections.Generic.Dictionary<string, System.Collections.Concurrent.ConcurrentQueue<string>>();
var observers = new System.Collections.Generic.List<System.Action<int>>();
var actors = new System.Collections.Generic.List<XEngine.Runtime.GameObject>();
var names = new[] { __ACTOR_ROOTS__ };
foreach (string name in names)
{
    XEngine.Runtime.GameObject? actor = null;
    foreach (var root in Scene.Current.RootObjects) if (root.Name == name) { actor = root; break; }
    if (actor == null) return "ERROR|natural actor missing:" + name;
    var queue = new System.Collections.Concurrent.ConcurrentQueue<string>();
    queues.Add(name, queue);
    System.AppDomain.CurrentDomain.SetData(storageKey + "-" + name, queue);
    observers.Add(CreateVisualObserver(actor, queue));
    actors.Add(actor);
}
System.AppDomain.CurrentDomain.SetData(storageKey, queues);
var markers = new System.Collections.Concurrent.ConcurrentQueue<string>();
System.AppDomain.CurrentDomain.SetData(storageKey + "-markers", markers);
var captured = new System.Collections.Generic.HashSet<string>();
async XEngine.Async.XTaskVoid ObserveNatural()
{
    try
    {
        for (int frame = 0; frame <= __MAX_FRAMES__; frame++)
        {
            do { await XEngine.Async.XTask.NextFrame(XEngine.Async.FrameTiming.EndOfFrame); } while (XEngine.Runtime.Application.IsPaused);
            bool pauseForCapture = false;
            for (int i = 0; i < observers.Count; i++)
            {
                observers[i](frame);
                var animator = actors[i].GetComponent<XEngine.Runtime.Animator>();
                var info = animator!.GetCurrentAnimatorStateInfo();
                string clip = animator.CurrentClip?.Name ?? "<null>";
                foreach (string candidate in new[] { __KNOWN_STATES__ })
                    if (info.IsName(candidate)) { clip = candidate; break; }
                string markerKey = actors[i].Name + "-" + clip;
                float cycleTime = info.normalizedTime - System.MathF.Floor(info.normalizedTime);
                if (cycleTime >= 0.45f && cycleTime <= 0.75f && captured.Add(markerKey))
                {
                    markers.Enqueue("CAPTURE|" + actors[i].Name + "|" + frame + "|" + clip);
                    pauseForCapture = true;
                }
            }
            if (pauseForCapture) XEngine.Runtime.Application.IsPaused = true;
        }
    }
    catch (System.Exception ex) { markers.Enqueue("ERROR|" + ex.ToString()); }
    finally
    {
        foreach (var queue in queues.Values) queue.Enqueue("DONE");
        markers.Enqueue("DONE");
    }
}
ObserveNatural().Forget();
return "STARTED|" + storageKey;
'''


def _run_natural_observer(client: EditorMcp, frames: int, timeout: float, output: Path) -> dict[str, Any]:
    """Observe authored Hero/Ally logic without changing input, controllers, transforms or playback."""
    storage_key = f"battle2-natural-{time.time_ns()}"
    code = (NATURAL_OBSERVER_TEMPLATE.replace("__STORAGE_KEY__", storage_key)
            .replace("__VISUAL_OBSERVER_FACTORY__", _visual_observer_factory())
            .replace("__ACTOR_ROOTS__", ", ".join(json.dumps(spec["root"]) for spec in ACTOR_STATES.values()))
            .replace("__KNOWN_STATES__", ", ".join(json.dumps(name) for name in sorted({state for spec in ACTOR_STATES.values() for state in spec["states"]})))
            .replace("__MAX_FRAMES__", str(frames)))
    started = client.eval(code, timeout=180)
    if not started.startswith("STARTED|"):
        raise RuntimeError(f"natural observer failed to start: {started}")
    marker_code = READ_PRODUCTION_SAMPLES_TEMPLATE.replace("__STORAGE_KEY__", storage_key + "-markers")
    deadline = time.monotonic() + timeout
    handled: set[str] = set()
    captures: list[dict[str, Any]] = []
    try:
        while time.monotonic() < deadline:
            text = client.eval(marker_code, timeout=120)
            lines = text.splitlines()
            errors = [line for line in lines if line.startswith("ERROR|")]
            if errors:
                raise RuntimeError("natural observer: " + "; ".join(errors))
            markers = [line for line in lines if line.startswith("CAPTURE|") and line not in handled]
            if markers:
                capture_start = time.monotonic()
                try:
                    screenshot = capture(client, output, f"natural-{len(captures):02d}.png")
                    for marker in markers:
                        _, actor, frame, clip = marker.split("|", 3)
                        captures.append({"actorRoot": actor, "frame": int(frame), "clip": clip, "screenshot": screenshot})
                        handled.add(marker)
                finally:
                    client.eval('XEngine.Runtime.Application.IsPaused = false; return "resumed";', timeout=60)
                    deadline += time.monotonic() - capture_start
            if "DONE" in lines:
                break
            time.sleep(0.1)
        else:
            raise TimeoutError("natural observer did not finish within its runtime budget")
        result: dict[str, Any] = {"mode": "naturalProductionObservation", "actors": {}, "captures": captures, "failures": []}
        for actor, spec in ACTOR_STATES.items():
            read_code = READ_PRODUCTION_SAMPLES_TEMPLATE.replace("__STORAGE_KEY__", storage_key + "-" + spec["root"])
            collected: list[str] = []
            while True:
                before_count = len(collected)
                _read_sample_updates(client, read_code, collected, timeout=120)
                if "DONE" in collected[before_count:]:
                    break
                if len(collected) == before_count:
                    raise RuntimeError(f"natural/{actor}: completed producer has no DONE marker")
            actor_frames = _parse_actor_state_samples("\n".join(collected))
            if len(actor_frames) != frames + 1:
                raise RuntimeError(f"natural/{actor}: expected {frames + 1} frames, got {len(actor_frames)}")
            state_groups: dict[str, list[dict[str, Any]]] = {}
            for frame in actor_frames:
                clip = frame["clip"]
                state = next((name for name in sorted(spec["states"], key=len, reverse=True)
                              if clip == name or clip.endswith("_" + name)), clip)
                state_groups.setdefault(state, []).append(frame)
            state_metrics: dict[str, Any] = {}
            actor_failures: list[str] = []
            # Production input tests cover the hero's Run/actions; this passive window must
            # cover its Idle and the naturally-controlled allies' Run and normal combo.
            required = ("Idle",) if actor == "Anbi" else ("Run", "Attack_Normal_1", "Attack_Normal_2", "Attack_Normal_3")
            for state in required:
                if state not in state_groups:
                    actor_failures.append(f"natural/{actor}: required state {state} was not observed")
            for state, state_frames in state_groups.items():
                metrics, failures = _analyse_actor_state(
                    f"natural/{actor}", state, state_frames,
                    stationary_root=False, require_full_state=False)
                state_metrics[state] = metrics
                actor_failures.extend(failures)
            result["actors"][actor] = {"frames": actor_frames, "states": state_metrics,
                                       "failures": actor_failures, "passed": not actor_failures}
            result["failures"].extend(actor_failures)
        result["passed"] = not result["failures"]
        return result
    finally:
        client.eval('XEngine.Runtime.Application.IsPaused = false; return "resumed";', timeout=60)


def _read_sample_updates(
    client: EditorMcp, code: str, collected: list[str], timeout: float,
) -> str:
    try:
        page = client.eval(code, timeout=timeout)
    except Exception as exc:
        raise RuntimeError(f"reading incremental sample page failed: {type(exc).__name__}: {exc}") from exc
    if page == "MISSING":
        raise RuntimeError("diagnostic sample queue disappeared before completion")
    collected.extend(line for line in page.splitlines() if line)
    return "\n".join(collected)


def _sample_production_locomotion(
    client: EditorMcp, key: str, frame_count: int, timeout: float
) -> list[dict[str, Any]]:
    storage_key = f"battle2-controls-{key}-{time.time_ns()}"
    start_code = (
        PRODUCTION_COROUTINE_TEMPLATE.replace("__VISUAL_OBSERVER_FACTORY__", _visual_observer_factory())
        .replace("__STORAGE_KEY__", storage_key)
        .replace("__KEY__", key)
        .replace("__FRAME_COUNT__", str(frame_count))
    )
    started_result = client.eval(start_code, timeout=120)
    if not started_result.startswith(f"STARTED|{key}|"):
        raise RuntimeError(f"failed to start production locomotion probe: {started_result!r}")
    read_code = READ_PRODUCTION_SAMPLES_TEMPLATE.replace("__STORAGE_KEY__", storage_key)
    deadline = time.monotonic() + timeout
    last = ""
    collected: list[str] = []
    while time.monotonic() < deadline:
        last = _read_sample_updates(client, read_code, collected, timeout=120)
        lines = [line for line in last.splitlines() if line]
        if "DONE" in lines:
            errors = [line for line in lines if line.startswith("ERROR|")]
            if errors:
                raise RuntimeError("production coroutine failed: " + "; ".join(errors))
            parsed = [
                _parse_production_state(line, float(int(line.split("|", 2)[1])))
                for line in lines
                if line.startswith("FRAME|")
            ]
            parsed.sort(key=lambda sample: int(sample["frame"] or 0))
            if len(parsed) != frame_count + 1:
                raise RuntimeError(
                    f"expected {frame_count + 1} frame samples, got {len(parsed)}"
                )
            _attach_visual_frames(parsed, last)
            return parsed
        time.sleep(0.1)
    # Always clear a possibly held key if the coroutine failed to reach its finally block.
    client.eval(RELEASE_KEY_TEMPLATE.replace("__KEY__", key), timeout=60)
    raise TimeoutError(f"production locomotion probe timed out: {last[-1000:]!r}")


def _wait_for_action_states(client: EditorMcp, key: str, timeout: float) -> str:
    state_literals = ", ".join(json.dumps(state) for state in ACTION_STATES[key])
    code = ACTION_READY_TEMPLATE.replace("__STATES__", state_literals)
    deadline = time.monotonic() + timeout
    last = ""
    while time.monotonic() < deadline:
        last = client.eval(code, timeout=120)
        if last.startswith("READY|1|"):
            return last
        time.sleep(0.25)
    raise TimeoutError(f"{key} animator states did not become ready: {last!r}")


def _prepare_actor_matrix(client: EditorMcp) -> str:
    root_literals = ", ".join(
        json.dumps(str(spec["root"])) for spec in ACTOR_STATES.values()
    )
    result = client.eval(
        ACTOR_MATRIX_PREFLIGHT_CODE.replace("__ACTOR_ROOTS__", root_literals),
        timeout=180,
    )
    if not result.startswith("READY|"):
        raise RuntimeError(f"actor matrix preflight failed: {result!r}")
    return result


def _capture_paused_actor_closeup(
    client: EditorMcp, actor_root: str, output: Path, filename: str,
) -> str:
    """Frame the authored actor root while paused, restoring the camera before any simulation."""
    storage_key = f"battle2-paused-camera-{time.time_ns()}"
    save_camera = (
        "System.AppDomain.CurrentDomain.SetData(" + json.dumps(storage_key) + ", "
        "new System.Tuple<XEngine.Runtime.Camera, XEngine.Vector.Float3, XEngine.Vector.Quaternion>("
        "camera, camera.Transform.Position, camera.Transform.Rotation));\n"
    )
    closeup_code = (
        'if (!XEngine.Runtime.Application.IsPaused) return "ERROR|closeup requires paused gameplay";\n'
        + CLOSEUP_CAMERA_TEMPLATE.replace("__HERO__", actor_root).replace(
            "var target = hero.Transform.Position", save_camera + "var target = hero.Transform.Position")
    )
    restore_code = (
        "var saved = System.AppDomain.CurrentDomain.GetData(" + json.dumps(storage_key) + ") as "
        "System.Tuple<XEngine.Runtime.Camera, XEngine.Vector.Float3, XEngine.Vector.Quaternion>;\n"
        "if (saved != null) { saved.Item1.Transform.Position = saved.Item2; "
        "saved.Item1.Transform.Rotation = saved.Item3; }\n"
        "System.AppDomain.CurrentDomain.SetData(" + json.dumps(storage_key) + ', null); return "restored";'
    )
    try:
        result = client.eval(closeup_code, timeout=120)
        if not result.startswith("closeup:"):
            raise RuntimeError(f"failed to frame paused actor {actor_root}: {result}")
        return capture(client, output, filename)
    finally:
        client.eval(restore_code, timeout=60)


def _validate_isolated_captures(
    actor: str, state: str, frames: list[dict[str, Any]], captures: list[dict[str, Any]],
) -> list[str]:
    scope = f"{actor}/{state}"
    if len(captures) != 3:
        return [f"{scope}: expected 3 normalized-time screenshots, got {len(captures)}"]
    failures: list[str] = []
    by_frame = {frame["frame"]: frame for frame in frames}
    for index, (capture_entry, target) in enumerate(zip(captures, (0.2, 0.5, 0.8))):
        actual = capture_entry["actualNormalizedTime"]
        sample = by_frame.get(capture_entry["frame"])
        if capture_entry["targetNormalizedTime"] != target or not math.isfinite(actual) or abs(actual - target) > ISOLATED_NORMALIZED_TOLERANCE:
            failures.append(f"{scope}: screenshot {index} missed normalized-time target {target}")
        if sample is None or not math.isfinite(sample["normalizedTime"]) or abs(sample["normalizedTime"] - actual) > ISOLATED_NORMALIZED_TOLERANCE:
            failures.append(f"{scope}: screenshot {index} has no matching evaluated pose sample")
        if index and capture_entry["frame"] <= captures[index - 1]["frame"]:
            failures.append(f"{scope}: screenshots reused an evaluated frame")
    return failures


def _run_actor_state_probe(
    client: EditorMcp,
    actor: str,
    actor_root: str,
    state: str,
    max_frames: int,
    timeout: float,
    output: Path,
    *,
    drive_state: bool = True,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    storage_key = f"battle2-actor-{actor}-{state}-{time.time_ns()}"
    core_literals = ", ".join(json.dumps(name) for name in CORE_POSE_BONES)
    start_code = (
        ACTOR_STATE_COROUTINE_TEMPLATE.replace("__STORAGE_KEY__", storage_key)
        .replace("__ACTOR_LABEL__", json.dumps(actor))
        .replace("__ACTOR_ROOT__", json.dumps(actor_root))
        .replace("__STATE_NAME__", json.dumps(state))
        .replace("__DRIVE_STATE__", "true" if drive_state else "false")
        .replace("__CORE_BONE_NAMES__", core_literals)
        .replace("__MAX_FRAMES__", str(max_frames))
        .replace("__ISOLATED_STEP__", str(ISOLATED_NORMALIZED_STEP))
        .replace("__ISOLATED_TOLERANCE__", str(ISOLATED_NORMALIZED_TOLERANCE))
    )
    started = client.eval(start_code, timeout=180)
    expected_prefix = f"STARTED|{actor}|{state}|"
    if not started.startswith(expected_prefix):
        raise RuntimeError(f"failed to start {actor}/{state} probe: {started!r}")
    read_code = READ_PRODUCTION_SAMPLES_TEMPLATE.replace("__STORAGE_KEY__", storage_key)
    deadline = time.monotonic() + timeout
    last = ""
    collected: list[str] = []
    captures: list[dict[str, Any]] = []
    handled: set[int] = set()
    try:
        while time.monotonic() < deadline:
            last = _read_sample_updates(client, read_code, collected, timeout=180)
            lines = [line for line in last.splitlines() if line]
            for marker in (line for line in lines if line.startswith("ACTOR_CAPTURE|")):
                _, frame_text, index_text, normalized_text = marker.split("|")
                index = int(index_text)
                if index in handled:
                    continue
                started_capture = time.monotonic()
                try:
                    filename = f"actor-{actor}-{state}-{index}.png"
                    screenshot = (
                        _capture_paused_actor_closeup(client, actor_root, output, filename)
                        if drive_state else capture(client, output, filename)
                    )
                    captures.append({
                        "frame": int(frame_text), "targetNormalizedTime": (0.2, 0.5, 0.8)[index],
                        "actualNormalizedTime": float(normalized_text),
                        "evaluationMode": ISOLATED_EVALUATION_MODE if drive_state else "liveGameplayFrames",
                        "screenshot": screenshot,
                        "cameraView": "fixedActorRootCloseup" if drive_state else "productionCamera",
                    })
                finally:
                    if drive_state:
                        client.eval(
                            "System.AppDomain.CurrentDomain.SetData(" + json.dumps(storage_key + "-captureAck")
                            + f', {index}); return "capture acknowledged";', timeout=60)
                    else:
                        client.eval('XEngine.Runtime.Application.IsPaused = false; return "resumed";', timeout=60)
                    deadline += time.monotonic() - started_capture
                handled.add(index)
            if "DONE" in lines:
                frames = _parse_actor_state_samples(last)
                if len(captures) != 3:
                    raise RuntimeError(f"{actor}/{state}: expected 3 normalized-time screenshots, got {len(captures)}")
                if drive_state:
                    capture_failures = _validate_isolated_captures(actor, state, frames, captures)
                    if capture_failures:
                        raise RuntimeError("; ".join(capture_failures))
                return frames, captures
            time.sleep(0.1)
    finally:
        try:
            client.eval(
                "System.AppDomain.CurrentDomain.SetData("
                + json.dumps(storage_key)
                + ', null); System.AppDomain.CurrentDomain.SetData('
                + json.dumps(storage_key + "-captureAck")
                + ', null); XEngine.Runtime.Application.IsPaused = false; return "cleared";',
                timeout=60,
            )
        except Exception:
            pass
    raise TimeoutError(f"{actor}/{state} probe timed out: {last[-1000:]!r}")


def _run_action_probe(
    client: EditorMcp,
    key: str,
    max_frames: int,
    capture_frame: int,
    timeout: float,
    output: Path,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    storage_key = f"battle2-action-{key}-{time.time_ns()}"
    capture_state_literals = ", ".join(json.dumps(state) for state in ACTION_STATES[key])
    start_code = (
        ACTION_COROUTINE_TEMPLATE.replace("__VISUAL_OBSERVER_FACTORY__", _visual_observer_factory())
        .replace("__STORAGE_KEY__", storage_key)
        .replace("__KEY__", key)
        .replace("__MAX_FRAMES__", str(max_frames))
        .replace("__CAPTURE_FRAME__", str(capture_frame))
        .replace("__PHASE_CAPTURE_STATES__", capture_state_literals)
        .replace("__CAPTURE_EACH_PHASE__", "true" if key == "I" else "false")
    )
    started = client.eval(start_code, timeout=180)
    if not started.startswith(f"STARTED|{key}|"):
        raise RuntimeError(f"failed to start {key} action probe: {started!r}")

    read_code = READ_PRODUCTION_SAMPLES_TEMPLATE.replace("__STORAGE_KEY__", storage_key)
    deadline = time.monotonic() + timeout
    captures: list[dict[str, Any]] = []
    capture_errors: list[str] = []
    handled_marker_count = 0
    last = ""
    collected: list[str] = []
    try:
        while time.monotonic() < deadline:
            last = _read_sample_updates(client, read_code, collected, timeout=180)
            lines = [line for line in last.splitlines() if line]
            markers = [line for line in lines if line.startswith("CAPTURE_READY|")]
            for marker_line in markers[handled_marker_count:]:
                parts = marker_line.split("|")
                if len(parts) != 5:
                    raise ValueError(f"unexpected action capture marker: {marker_line!r}")
                marker = {
                    "frame": int(parts[1]),
                    "phaseFrame": int(parts[2]),
                    "activeAction": parts[3],
                    "clip": parts[4],
                }
                phase_label = next(
                    (
                        label
                        for label, state in I_ACTION_PHASES
                        if key == "I" and state == marker["clip"]
                    ),
                    "Keyframe",
                )
                marker["phase"] = phase_label
                filename = (
                    f"action-{key}-{phase_label.lower()}-keyframe.png"
                    if key == "I"
                    else f"action-{key}-keyframe.png"
                )
                try:
                    renderer_evidence = _parse_action_renderer_evidence(
                        client.eval(ACTION_RENDERER_EVIDENCE_CODE, timeout=180)
                    )
                    screenshot = capture(client, output, filename)
                    captures.append(
                        {
                            "phase": phase_label,
                            "clip": marker["clip"],
                            "marker": marker,
                            "keyframeScreenshot": screenshot,
                            "rendererEvidence": renderer_evidence,
                            "distanceSummary": renderer_evidence["distanceSummary"],
                        }
                    )
                except Exception as exc:
                    capture_errors.append(
                        f"{phase_label}/{marker['clip']}: {type(exc).__name__}: {exc}"
                    )
                finally:
                    client.eval(
                        "XEngine.Runtime.Application.IsPaused = false; return \"resumed\";",
                        timeout=60,
                    )
            handled_marker_count = len(markers)
            if "DONE" in lines:
                errors = [line for line in lines if line.startswith("ERROR|")]
                if errors:
                    raise RuntimeError(f"{key} action coroutine failed: {'; '.join(errors)}")
                frames = [_parse_action_frame(line) for line in lines if line.startswith("ACTIONFRAME|")]
                frames.sort(key=lambda frame: frame["frame"])
                if not frames:
                    raise RuntimeError(f"{key} action coroutine returned no frame samples")
                if capture_errors:
                    raise RuntimeError(
                        f"{key} capture failures: {'; '.join(capture_errors)}"
                    )
                expected_capture_count = len(I_ACTION_PHASES) if key == "I" else 1
                if len(captures) != expected_capture_count:
                    raise RuntimeError(
                        f"{key} expected {expected_capture_count} keyframe captures, "
                        f"got {len(captures)}"
                    )
                if key == "I":
                    captured_clips = {capture_entry["clip"] for capture_entry in captures}
                    missing_clips = [
                        state for _, state in I_ACTION_PHASES if state not in captured_clips
                    ]
                    if missing_clips:
                        raise RuntimeError(
                            f"I phase keyframe captures missing states: {missing_clips}"
                        )
                _attach_visual_frames(frames, last)
                return frames, captures
            time.sleep(0.1)
    finally:
        if time.monotonic() >= deadline:
            client.eval(
                "XEngine.Runtime.InputInjector.Release(XEngine.Runtime.KeyCode."
                + key
                + "); XEngine.Runtime.Application.IsPaused = false; return \"cleaned\";",
                timeout=60,
            )
    raise TimeoutError(f"{key} action probe timed out: {last[-1000:]!r}")


def _renderer_evidence_failures(
    scope: str, renderer_evidence: dict[str, Any]
) -> list[str]:
    renderer_metrics = renderer_evidence["metrics"]
    failures: list[str] = []
    if renderer_metrics["primaryBodyRendererCount"] == 0:
        failures.append(f"{scope}: no enabled primary body SkinnedMeshRenderer was identified")
    if renderer_metrics["bip001BindingCount"] == 0:
        failures.append(f"{scope}: no Bip001 binding was found in the active hero skeleton/renderers")
    if renderer_metrics["bip001KeyBoneCount"] == 0:
        failures.append(f"{scope}: Bip001 world-distance evidence is missing")
    if renderer_metrics["pelvisKeyBoneCount"] == 0:
        failures.append(f"{scope}: Bip001 Pelvis world-distance evidence is missing")
    if renderer_metrics["unresolvedBoneCount"] != 0:
        failures.append(
            f"{scope}: {renderer_metrics['unresolvedBoneCount']} "
            "SkinnedMeshRenderer bones are unresolved"
        )
    if (
        renderer_metrics["maxPrimaryBodyBoundsCenterHorizontalOffset"]
        > THRESHOLDS["primaryBodyBoundsHorizontalOffsetMax"]
    ):
        failures.append(
            f"{scope}: primary body bounds center is "
            f"{renderer_metrics['maxPrimaryBodyBoundsCenterHorizontalOffset']:.6f}m "
            f"from hero root; expected <= "
            f"{THRESHOLDS['primaryBodyBoundsHorizontalOffsetMax']:.2f}m"
        )
    if (
        renderer_metrics["maxRendererBoundsCenterHorizontalOffset"]
        > THRESHOLDS["rendererBoundsHorizontalOffsetMax"]
    ):
        failures.append(
            f"{scope}: a renderer bounds center is "
            f"{renderer_metrics['maxRendererBoundsCenterHorizontalOffset']:.6f}m "
            f"from hero root; expected <= "
            f"{THRESHOLDS['rendererBoundsHorizontalOffsetMax']:.2f}m"
        )
    if (
        renderer_metrics["maxPrimaryBodyBoundsCenterWorldDistance"]
        > THRESHOLDS["primaryBodyBoundsWorldOffsetMax"]
    ):
        failures.append(
            f"{scope}: primary body bounds center is "
            f"{renderer_metrics['maxPrimaryBodyBoundsCenterWorldDistance']:.6f}m "
            "from hero root in 3D"
        )
    if renderer_metrics["maxPrimaryBodyBoundsExtent"] > THRESHOLDS["primaryBodyBoundsExtentMax"]:
        failures.append(
            f"{scope}: primary body bounds extent is "
            f"{renderer_metrics['maxPrimaryBodyBoundsExtent']:.6f}m"
        )
    if renderer_metrics["maxKeyBoneWorldDistance"] > THRESHOLDS["keyBoneWorldDistanceFromActorMax"]:
        failures.append(
            f"{scope}: Bip001/Pelvis reached "
            f"{renderer_metrics['maxKeyBoneWorldDistance']:.6f}m from hero root"
        )
    if (
        renderer_metrics["maxKeyBoneLocalReferenceExcursion"]
        > THRESHOLDS["keyBoneLocalReferenceExcursionMax"]
    ):
        failures.append(
            f"{scope}: Bip001/Pelvis local/reference excursion reached "
            f"{renderer_metrics['maxKeyBoneLocalReferenceExcursion']:.6f}m"
        )
    if not renderer_metrics["allEvidenceFinite"]:
        failures.append(f"{scope}: renderer/key-bone evidence contains NaN/Infinity")
    if any(
        any(value <= 1e-5 for value in renderer["worldBoundsSize"])
        for renderer in renderer_evidence["renderers"]
        if renderer["enabledInHierarchy"]
    ):
        failures.append(f"{scope}: an enabled renderer has an empty bounds axis")
    return failures


def _analyse_action(
    key: str,
    frames: list[dict[str, Any]],
    renderer_evidence: dict[str, Any],
    phase_captures: dict[str, dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], list[str]]:
    baseline = frames[0]
    segments: list[dict[str, Any]] = []
    root_path = 0.0
    max_root_step = 0.0
    max_requested_motion = 0.0
    requested_path = 0.0
    requested_direction_samples: list[tuple[float, float]] = []
    max_bone_local_excursion = 0.0
    previous = baseline
    for frame in frames[1:]:
        root_delta = (
            frame["rootPosition"][0] - previous["rootPosition"][0],
            frame["rootPosition"][2] - previous["rootPosition"][2],
        )
        root_step = math.hypot(*root_delta)
        requested = frame["characterControllerRequestedMotion"]
        requested_horizontal = math.hypot(requested[0], requested[2])
        local_delta = [
            frame["travelBoneLocalPosition"][index]
            - baseline["travelBoneLocalPosition"][index]
            for index in range(3)
        ]
        local_excursion = math.sqrt(sum(value * value for value in local_delta))
        root_path += root_step
        requested_path += requested_horizontal
        if requested_horizontal > 1e-8:
            requested_direction_samples.append(
                (requested[0] / requested_horizontal, requested[2] / requested_horizontal)
            )
        max_root_step = max(max_root_step, root_step)
        max_requested_motion = max(max_requested_motion, requested_horizontal)
        max_bone_local_excursion = max(max_bone_local_excursion, local_excursion)
        segments.append(
            {
                "frame": frame["frame"],
                "rootDeltaXZ": list(root_delta),
                "rootStep": root_step,
                "requestedMotionXZ": [requested[0], requested[2]],
                "worldMinusRequestedXZ": [root_delta[0] - requested[0], root_delta[1] - requested[2]],
                "requestedSource": "manualLunge" if key == "L" else "expectedZero",
                "worldMinusRequestedMeaning": "collision response or other transform writers; no source is assumed",

                "activeAction": frame["activeAction"],
                "clip": frame["clip"],
            }
        )
        previous = frame

    end = frames[-1]
    root_displacement = (
        end["rootPosition"][0] - baseline["rootPosition"][0],
        end["rootPosition"][2] - baseline["rootPosition"][2],
    )
    root_distance = math.hypot(*root_displacement)
    active_frames = [frame for frame in frames if frame["activeAction"] not in {"None", "<missing>"}]
    actions_seen = list(dict.fromkeys(frame["activeAction"] for frame in active_frames))
    clips_seen = list(dict.fromkeys(frame["clip"] for frame in active_frames))
    apply_root_motion_seen = any(frame["applyRootMotion"] for frame in frames)
    expected_action = {
        "J": "Normal",
        "K": "SkillK",
        "L": "SkillL",
        "I": "SkillIStart",
    }[key]
    metrics: dict[str, Any] = {
        "sampleCount": len(frames),
        "actionsSeen": actions_seen,
        "clipsSeen": clips_seen,
        "rootDisplacementXZ": list(root_displacement),
        "rootNetTravel": root_distance,
        "rootPathTravel": root_path,
        "maxRootFrameStep": max_root_step,
        "maxCharacterControllerRequestedMotion": max_requested_motion,
        "characterControllerRequestedPath": requested_path,
        "travelBonePath": end["travelBonePath"],
        "maxTravelBoneLocalExcursion": max_bone_local_excursion,
        "applyRootMotionSeen": apply_root_motion_seen,
        "rendererEvidenceMetrics": renderer_evidence["metrics"],
        "distanceSummary": renderer_evidence["distanceSummary"],
        "segments": segments,
    }
    failures: list[str] = []
    if not all(_is_finite_vector(frame[field]) for frame in frames for field in (
        "rootPosition", "characterControllerRequestedMotion", "heroForwardXZ",
        "travelBoneWorldPosition", "travelBoneLocalPosition", "lungeDirectionXZ", "lockedTargetPosition")):
        failures.append(f"{key}: action/controller data contains NaN/Infinity")
    if not all(math.isfinite(frame[field]) for frame in frames for field in ("skillLungeSpeed", "skillLungeDuration")):
        failures.append(f"{key}: lunge configuration contains NaN/Infinity")
    if expected_action not in actions_seen:
        failures.append(f"{key}: expected action {expected_action} was not observed: {actions_seen}")
    missing_states = [state for state in ACTION_STATES[key] if state not in clips_seen]
    if missing_states:
        failures.append(
            f"{key}: expected animator states were not observed: {missing_states}; saw {clips_seen}"
        )
    if apply_root_motion_seen:
        failures.append(f"{key}: Animator.ApplyRootMotion became true")
    if max_bone_local_excursion > THRESHOLDS["travelBoneLocalExcursionMax"]:
        failures.append(
            f"{key}: travel bone local excursion {max_bone_local_excursion:.6f}m exceeds "
            f"{THRESHOLDS['travelBoneLocalExcursionMax']:.2f}m"
        )
    visual_metrics: dict[str, Any] = {}
    for state in ACTION_STATES[key]:
        visual_frames = [frame["visual"] for frame in frames if frame["clip"] == state]
        if not visual_frames:
            failures.append(f"{key}/{state}: no production visual frames")
            continue
        state_metrics, state_failures = _analyse_actor_state(
            f"production/{key}", state, visual_frames, stationary_root=key != "L")
        visual_metrics[state] = state_metrics
        failures.extend(state_failures)
    metrics["productionVisualStates"] = visual_metrics
    if key == "I":
        phase_windows: dict[str, dict[str, Any]] = {}
        available_phase_captures = phase_captures or {}
        for phase_label, state in I_ACTION_PHASES:
            state_frames = [frame for frame in active_frames if frame["clip"] == state]
            capture_entry = available_phase_captures.get(phase_label)
            if not state_frames:
                failures.append(f"I/{phase_label}: no frame samples were recorded for {state}")
                continue
            phase_root_path = sum(
                math.hypot(
                    current["rootPosition"][0] - previous_frame["rootPosition"][0],
                    current["rootPosition"][2] - previous_frame["rootPosition"][2],
                )
                for previous_frame, current in zip(state_frames, state_frames[1:])
            )
            bip001_horizontal_distances = [
                math.hypot(
                    frame["travelBoneWorldPosition"][0] - frame["rootPosition"][0],
                    frame["travelBoneWorldPosition"][2] - frame["rootPosition"][2],
                )
                for frame in state_frames
            ]
            bip001_world_distances = [
                math.sqrt(
                    sum(
                        (
                            frame["travelBoneWorldPosition"][index]
                            - frame["rootPosition"][index]
                        )
                        ** 2
                        for index in range(3)
                    )
                )
                for frame in state_frames
            ]
            phase_windows[phase_label] = {
                "clip": state,
                "firstFrame": state_frames[0]["frame"],
                "lastFrame": state_frames[-1]["frame"],
                "sampleCount": len(state_frames),
                "heroRootStartPosition": state_frames[0]["rootPosition"],
                "heroRootEndPosition": state_frames[-1]["rootPosition"],
                "heroRootPathTravel": phase_root_path,
                "maximumBip001HorizontalDistanceFromHero": max(
                    bip001_horizontal_distances, default=0.0
                ),
                "maximumBip001WorldDistanceFromHero": max(
                    bip001_world_distances, default=0.0
                ),
                "captureFrame": capture_entry["marker"]["frame"]
                if capture_entry
                else None,
                "capturePhaseFrame": capture_entry["marker"]["phaseFrame"]
                if capture_entry
                else None,
                "captureDistances": capture_entry["distanceSummary"]
                if capture_entry
                else None,
            }
            if capture_entry is None:
                failures.append(
                    f"I/{phase_label}: renderer/Pelvis keyframe capture is missing for {state}"
                )
            else:
                failures.extend(
                    _renderer_evidence_failures(
                        f"I/{phase_label}", capture_entry["rendererEvidence"]
                    )
                )
        metrics["phaseWindows"] = phase_windows
    else:
        failures.extend(_renderer_evidence_failures(key, renderer_evidence))

    if key != "L":
        if requested_path > THRESHOLDS["nonLungeRequestedPathMax"]:
            failures.append(
                f"{key}: CharacterController received {requested_path:.6f}m of horizontal "
                "requested displacement; only L may request skill movement"
            )
        if root_path > THRESHOLDS["nonLungeRootTravelMax"]:
            failures.append(
                f"{key}: code path moved root {root_path:.6f}m; expected <= 0.03m"
            )
        if max_root_step > THRESHOLDS["nonLungeFrameTeleportMax"]:
            failures.append(
                f"{key}: instantaneous root step {max_root_step:.6f}m exceeds 0.02m"
            )
    else:
        lunge_frame = next(
            (
                frame
                for frame in active_frames
                if math.hypot(*frame["lungeDirectionXZ"]) > 0.5
            ),
            None,
        )
        if lunge_frame is None:
            failures.append("L: no non-zero manual lunge direction was observed")
        else:
            lunge_direction = _normalise_xz(lunge_frame["lungeDirectionXZ"])
            root_direction = _normalise_xz(root_displacement)
            direction_dot = _dot(root_direction, lunge_direction)
            target_delta = (
                lunge_frame["lockedTargetPosition"][0] - lunge_frame["rootPosition"][0],
                lunge_frame["lockedTargetPosition"][2] - lunge_frame["rootPosition"][2],
            )
            target_direction = _normalise_xz(target_delta)
            target_dot = _dot(lunge_direction, target_direction)
            hero_forward_dot = _dot(
                _normalise_xz(lunge_frame["heroForwardXZ"]), lunge_direction
            )
            configured_distance = (
                lunge_frame["skillLungeSpeed"] * lunge_frame["skillLungeDuration"]
            )
            frame_limit = EXPECTED_LUNGE_SPEED / 30.0 + THRESHOLDS["lungeFrameAllowance"]
            requested_direction_dots = [
                _dot(direction, lunge_direction)
                for direction in requested_direction_samples
            ]
            metrics.update(
                {
                    "lungeDirectionXZ": list(lunge_direction),
                    "lockedTarget": lunge_frame["lockedTarget"],
                    "lockedTargetDirectionDot": target_dot,
                    "heroForwardToLungeDot": hero_forward_dot,
                    "rootDirectionToLungeDot": direction_dot,
                    "configuredLungeSpeed": lunge_frame["skillLungeSpeed"],
                    "configuredLungeDuration": lunge_frame["skillLungeDuration"],
                    "configuredLungeDistance": configured_distance,
                    "expectedLungeSpeed": EXPECTED_LUNGE_SPEED,
                    "expectedLungeDuration": EXPECTED_LUNGE_DURATION,
                    "expectedLungeDistance": EXPECTED_LUNGE_DISTANCE,
                    "requestedDirectionDots": requested_direction_dots,
                    "maximumAllowedFrameStep": frame_limit,
                }
            )
            if not math.isclose(
                lunge_frame["skillLungeSpeed"], EXPECTED_LUNGE_SPEED, abs_tol=1e-5
            ):
                failures.append(
                    f"L: SkillLungeSpeed is {lunge_frame['skillLungeSpeed']:.6f}; "
                    f"expected exactly {EXPECTED_LUNGE_SPEED:.2f}"
                )
            if not math.isclose(
                lunge_frame["skillLungeDuration"], EXPECTED_LUNGE_DURATION, abs_tol=1e-5
            ):
                failures.append(
                    f"L: SkillLungeDuration is {lunge_frame['skillLungeDuration']:.6f}; "
                    f"expected exactly {EXPECTED_LUNGE_DURATION:.2f}"
                )
            if (
                abs(requested_path - EXPECTED_LUNGE_DISTANCE)
                > THRESHOLDS["lungeRequestedDistanceTolerance"]
            ):
                failures.append(
                    f"L: requested displacement path is {requested_path:.6f}m; expected "
                    f"{EXPECTED_LUNGE_DISTANCE:.6f}m after a clamped final partial step"
                )
            if (
                abs(root_path - EXPECTED_LUNGE_DISTANCE)
                > THRESHOLDS["lungeActualDistanceTolerance"]
            ):
                failures.append(
                    f"L: actual root path is {root_path:.6f}m; expected "
                    f"{EXPECTED_LUNGE_DISTANCE:.6f}m"
                )
            if root_path > requested_path + THRESHOLDS["lungeActualPathAllowance"]:
                failures.append(
                    f"L: actual root path {root_path:.6f}m exceeds requested path "
                    f"{requested_path:.6f}m"
                )
            if max_root_step > frame_limit:
                failures.append(
                    f"L: instantaneous root step {max_root_step:.6f}m exceeds {frame_limit:.6f}m"
                )
            if not requested_direction_dots:
                failures.append("L: no non-zero CharacterController lunge displacement was observed")
            elif min(requested_direction_dots) < THRESHOLDS["movementDirectionDotMin"]:
                failures.append(
                    f"L: requested displacement/lunge direction dot "
                    f"{min(requested_direction_dots):.6f} < 0.98"
                )
            if root_distance > 0.05 and direction_dot < THRESHOLDS["movementDirectionDotMin"]:
                failures.append(f"L: root/lunge direction dot {direction_dot:.6f} < 0.98")
            if lunge_frame["lockedTarget"] != "<null>" and target_dot < THRESHOLDS["movementDirectionDotMin"]:
                failures.append(f"L: lunge/target direction dot {target_dot:.6f} < 0.98")
            if hero_forward_dot < THRESHOLDS["lungeHeroForwardDotMin"]:
                failures.append(f"L: hero forward/lunge dot {hero_forward_dot:.6f} < 0.98")
    return metrics, failures


def _anatomical_forward_xz(visual: dict[str, Any]) -> tuple[float, float] | None:
    """Anatomical right cross torso up, independent of GameObject/local forward axes."""
    names = ("leftThighWorldPosition", "rightThighWorldPosition", "spineWorldPosition", "pelvisWorldPosition")
    if any(name not in visual or len(visual[name]) != 3 or not _is_finite_vector(visual[name])
           for name in names):
        return None
    right = [visual["rightThighWorldPosition"][i] - visual["leftThighWorldPosition"][i]
             for i in range(3)]
    up = [visual["spineWorldPosition"][i] - visual["pelvisWorldPosition"][i]
          for i in range(3)]
    cross_x = right[1] * up[2] - right[2] * up[1]
    cross_z = right[0] * up[1] - right[1] * up[0]
    length = math.hypot(cross_x, cross_z)
    if not math.isfinite(length) or length <= 1e-8:
        return None
    return cross_x / length, cross_z / length


def _analyse_locomotion_facing(
    key: str, samples: list[dict[str, Any]], expected: tuple[float, float],
) -> tuple[dict[str, Any], list[str]]:
    failures: list[str] = []
    observations: list[dict[str, Any]] = []
    settled_count = 0
    for previous, current in zip(samples, samples[1:]):
        visual = current["visual"]
        hero_forward = _normalise_xz(current["heroForwardXZ"])
        world_step = math.hypot(current["rootPosition"][0] - previous["rootPosition"][0],
                                current["rootPosition"][2] - previous["rootPosition"][2])
        settled = (visual["clip"] == "Run" and world_step > 1e-5
                   and _dot(hero_forward, expected) >= THRESHOLDS["locomotionFacingRootSettledDotMin"])
        settled_count = settled_count + 1 if settled else 0
        if settled_count < THRESHOLDS["locomotionFacingStableFrameCount"]:
            continue
        anatomical_forward = _anatomical_forward_xz(visual)
        observation = {"frame": current["frame"], "engineFrame": visual["engineFrame"],
                       "anatomicalForwardXZ": list(anatomical_forward) if anatomical_forward else None,
                       "heroForwardXZ": list(hero_forward), "expectedMovementXZ": list(expected)}
        if anatomical_forward is None:
            failures.append(f"{key}: frame {current['frame']} anatomical facing evidence is missing or degenerate")
        else:
            wish_dot = _dot(anatomical_forward, expected)
            hero_dot = _dot(anatomical_forward, hero_forward)
            observation.update({"anatomicalToWishDot": wish_dot, "anatomicalToHeroForwardDot": hero_dot})
            if min(wish_dot, hero_dot) < THRESHOLDS["locomotionAnatomicalForwardDotMin"]:
                failures.append(f"{key}: frame {current['frame']} anatomical body facing disagrees with settled root/movement")
        observations.append(observation)
    if len(observations) < THRESHOLDS["locomotionFacingStableFrameCount"]:
        failures.append(f"{key}: fewer than {THRESHOLDS['locomotionFacingStableFrameCount']} stable moving Run frames for anatomical facing acceptance")
    return {
        "derivation": "XZ projection of Cross(RThigh-LThigh, Spine-Pelvis)",
        "minimumDot": THRESHOLDS["locomotionAnatomicalForwardDotMin"],
        "maximumAllowedRunPoseTwistDegrees": 60.0,
        "stableSampleCount": len(observations),
        "minimumAnatomicalToWishDot": min((x["anatomicalToWishDot"] for x in observations if "anatomicalToWishDot" in x), default=None),
        "minimumAnatomicalToHeroForwardDot": min((x["anatomicalToHeroForwardDot"] for x in observations if "anatomicalToHeroForwardDot" in x), default=None),
        "samples": observations,
    }, _summarize_failures(failures)


def _analyse_production_locomotion(
    key: str, samples: list[dict[str, Any]]
) -> tuple[dict[str, Any], list[str]]:
    if len(samples) < 2:
        raise ValueError(f"{key}: production locomotion yielded fewer than two samples")
    baseline = samples[0]
    moving = [sample for sample in samples[1:] if math.hypot(*sample["input"]) > 0.5]
    if not moving:
        return {
            "inputObserved": False,
            "sampleCount": len(samples),
        }, [f"{key}: injected key never reached the Move InputAction"]

    yaw = baseline["cameraYawDeg"]
    expected = _expected_direction(yaw, KEY_INPUTS[key])
    first = baseline["rootPosition"]
    last = moving[-1]["rootPosition"]
    displacement = (last[0] - first[0], last[2] - first[2])
    travel = math.hypot(*displacement)
    movement_direction = _normalise_xz(displacement)
    movement_dot = _dot(movement_direction, expected)
    perpendicular = (-expected[1], expected[0])
    secondary_ratio = abs(_dot(movement_direction, perpendicular))

    input_dots: list[float] = []
    wish_dots: list[float] = []
    requested_motion_dots: list[float] = []
    hero_forward_dots: list[float] = []
    camera_samples: list[dict[str, Any]] = [
        {
            "yawDeg": baseline["cameraYawDeg"],
            "pitchDeg": baseline["cameraPitchDeg"],
            "forward": baseline["cameraForward"],
            "up": baseline["cameraUp"],
        }
    ]
    segment_metrics: list[dict[str, Any]] = []
    previous = baseline
    for sample in moving:
        observed_input = _normalise_xz((sample["input"][0], sample["input"][1]))
        expected_input = _normalise_xz(KEY_INPUTS[key])
        input_dots.append(_dot(observed_input, expected_input))
        wish_dots.append(_dot(_normalise_xz(sample["resolvedWishXZ"]), expected))
        requested = sample["characterControllerRequestedMotion"]
        requested_motion_dots.append(
            _dot(_normalise_xz((requested[0], requested[2])), expected)
        )
        hero_forward_dots.append(
            _dot(_normalise_xz(sample["heroForwardXZ"]), expected)
        )
        camera_samples.append(
            {
                "yawDeg": sample["cameraYawDeg"],
                "pitchDeg": sample["cameraPitchDeg"],
                "forward": sample["cameraForward"],
                "up": sample["cameraUp"],
            }
        )
        previous_position = previous["rootPosition"]
        current_position = sample["rootPosition"]
        segment = (
            current_position[0] - previous_position[0],
            current_position[2] - previous_position[2],
        )
        segment_travel = math.hypot(*segment)
        segment_metrics.append(
            {
                "endElapsedSeconds": sample["elapsedSeconds"],
                "displacementXZ": list(segment),
                "horizontalTravel": segment_travel,
                "directionDot": (
                    _dot(_normalise_xz(segment), expected) if segment_travel > 1e-8 else None
                ),
                "collisionFlags": sample["collisionFlags"],
            }
        )
        previous = sample

    bone_start_world = baseline["travelBoneWorldPosition"]
    bone_end_world = moving[-1]["travelBoneWorldPosition"]
    bone_world_delta = (
        bone_end_world[0] - bone_start_world[0],
        bone_end_world[2] - bone_start_world[2],
    )
    bone_start_local = baseline["travelBoneLocalPosition"]
    bone_end_local = moving[-1]["travelBoneLocalPosition"]
    bone_local_delta = [
        bone_end_local[index] - bone_start_local[index] for index in range(3)
    ]
    pelvis_start_world = baseline["pelvisWorldPosition"]
    pelvis_end_world = moving[-1]["pelvisWorldPosition"]
    pelvis_world_delta = (
        pelvis_end_world[0] - pelvis_start_world[0],
        pelvis_end_world[2] - pelvis_start_world[2],
    )
    bounds_start_world = baseline["primaryBodyBoundsCenter"]
    bounds_end_world = moving[-1]["primaryBodyBoundsCenter"]
    bounds_world_delta = (
        bounds_end_world[0] - bounds_start_world[0],
        bounds_end_world[2] - bounds_start_world[2],
    )
    visual_series = [baseline, *moving]
    baseline_relative = {
        field: [
            baseline[field][index] - baseline["rootPosition"][index]
            for index in range(3)
        ]
        for field in (
            "travelBoneWorldPosition",
            "pelvisWorldPosition",
            "primaryBodyBoundsCenter",
        )
    }
    max_visual_relative_excursion: dict[str, float] = {}
    for field, reference in baseline_relative.items():
        max_visual_relative_excursion[field] = max(
            (
                _distance3(
                    [
                        sample[field][index] - sample["rootPosition"][index]
                        for index in range(3)
                    ],
                    reference,
                )
                for sample in visual_series
            ),
            default=0.0,
        )
    all_visual_finite = all(
        _is_finite_vector(sample[field])
        for sample in visual_series
        for field in (
            "travelBoneWorldPosition",
            "pelvisWorldPosition",
            "primaryBodyBoundsCenter",
            "primaryBodyBoundsSize",
        )
    )
    bone_world_travel = math.hypot(*bone_world_delta)
    pelvis_world_travel = math.hypot(*pelvis_world_delta)
    bounds_world_travel = math.hypot(*bounds_world_delta)
    bone_world_dot = _dot(_normalise_xz(bone_world_delta), expected)
    pelvis_world_dot = _dot(_normalise_xz(pelvis_world_delta), expected)
    bounds_world_dot = _dot(_normalise_xz(bounds_world_delta), expected)
    root_forward_to_travel_dot = _dot(
        _normalise_xz(moving[-1]["heroForwardXZ"]), movement_direction
    )
    camera = _camera_metrics(camera_samples)
    metrics = {
        "inputObserved": True,
        "sampleCount": len(samples),
        "expectedDirectionXZ": list(expected),
        "rootDisplacementXZ": list(displacement),
        "rootHorizontalTravel": travel,
        "rootMovementDirectionDot": movement_dot,
        "rootSecondaryAxisRatio": secondary_ratio,
        "inputDirectionDots": input_dots,
        "resolvedWishDirectionDots": wish_dots,
        "characterControllerRequestedMotionDirectionDots": requested_motion_dots,
        "heroForwardDirectionDots": hero_forward_dots,
        "finalHeroForwardDot": hero_forward_dots[-1],
        "segments": segment_metrics,
        "travelBonePath": moving[-1]["travelBonePath"],
        "travelBoneWorldDeltaXZ": list(bone_world_delta),
        "travelBoneWorldTravel": bone_world_travel,
        "travelBoneWorldDirectionDot": bone_world_dot,
        "travelBoneLocalDelta": bone_local_delta,
        "pelvisBonePath": moving[-1]["pelvisBonePath"],
        "pelvisWorldDeltaXZ": list(pelvis_world_delta),
        "pelvisWorldTravel": pelvis_world_travel,
        "pelvisWorldDirectionDot": pelvis_world_dot,
        "primaryBodyBoundsWorldDeltaXZ": list(bounds_world_delta),
        "primaryBodyBoundsWorldTravel": bounds_world_travel,
        "primaryBodyBoundsWorldDirectionDot": bounds_world_dot,
        "maxVisualRelativeExcursion": max_visual_relative_excursion,
        "allVisualDataFinite": all_visual_finite,
        "primaryBodyRendererCounts": [
            sample["primaryBodyRendererCount"] for sample in visual_series
        ],
        "primaryBodyBoundsSizes": [
            sample["primaryBodyBoundsSize"] for sample in visual_series
        ],
        "rootForwardToTravelDot": root_forward_to_travel_dot,
        "camera": camera,
    }
    failures: list[str] = []
    if travel < THRESHOLDS["minimumHorizontalTravel"]:
        failures.append(f"{key}: production root travel {travel:.6f}m is below minimum")
    if movement_dot < THRESHOLDS["movementDirectionDotMin"]:
        failures.append(f"{key}: production root movement dot {movement_dot:.6f} < 0.98")
    if secondary_ratio > THRESHOLDS["secondaryAxisRatioMax"]:
        failures.append(
            f"{key}: production root secondary-axis ratio {secondary_ratio:.6f} > 0.05"
        )
    if min(input_dots, default=-1.0) < THRESHOLDS["movementDirectionDotMin"]:
        failures.append(f"{key}: Move InputAction has the wrong sign")
    if min(wish_dots, default=-1.0) < THRESHOLDS["movementDirectionDotMin"]:
        failures.append(f"{key}: resolved movement wish has the wrong sign")
    if min(requested_motion_dots, default=-1.0) < THRESHOLDS["movementDirectionDotMin"]:
        failures.append(f"{key}: CharacterController requested motion has the wrong sign")
    if hero_forward_dots[-1] < THRESHOLDS["locomotionHeroForwardDotMin"]:
        failures.append(
            f"{key}: final production hero forward dot {hero_forward_dots[-1]:.6f} < "
            f"{THRESHOLDS['locomotionHeroForwardDotMin']:.2f} (backwards-facing guard)"
        )
    if root_forward_to_travel_dot < THRESHOLDS["locomotionHeroForwardDotMin"]:
        failures.append(
            f"{key}: hero faces opposite its actual root travel; dot "
            f"{root_forward_to_travel_dot:.6f}"
        )
    visual_directions = (
        ("Bip001", bone_world_travel, bone_world_dot),
        ("Pelvis", pelvis_world_travel, pelvis_world_dot),
        ("primary body bounds", bounds_world_travel, bounds_world_dot),
    )
    for label, visual_travel, visual_dot in visual_directions:
        if visual_travel < THRESHOLDS["minimumHorizontalTravel"]:
            failures.append(
                f"{key}: {label} travelled only {visual_travel:.6f}m while the root moved"
            )
        elif visual_dot < THRESHOLDS["visualMovementDirectionDotMin"]:
            failures.append(
                f"{key}: {label} movement direction dot {visual_dot:.6f} < "
                f"{THRESHOLDS['visualMovementDirectionDotMin']:.2f}"
            )
    for field, excursion in max_visual_relative_excursion.items():
        if excursion > THRESHOLDS["visualRelativeOffsetExcursionMax"]:
            failures.append(
                f"{key}: {field} moved {excursion:.6f}m relative to the hero root"
            )
    if not all_visual_finite:
        failures.append(f"{key}: movement visual evidence contains NaN/Infinity")
    if any(count <= 0 for count in metrics["primaryBodyRendererCounts"]):
        failures.append(f"{key}: movement sample is missing the primary body renderer")
    for frame_index, size in enumerate(metrics["primaryBodyBoundsSizes"]):
        if any(value <= 1e-5 for value in size):
            failures.append(f"{key}: movement bounds are empty at sample {frame_index}")
            break
        if max(size) > THRESHOLDS["primaryBodyBoundsExtentMax"]:
            failures.append(
                f"{key}: movement bounds extent exceeds "
                f"{THRESHOLDS['primaryBodyBoundsExtentMax']:.2f}m"
            )
            break
    if camera["maxYawErrorDegrees"] >= THRESHOLDS["cameraYawErrorDegMaxExclusive"]:
        failures.append(
            f"{key}: production camera yaw error {camera['maxYawErrorDegrees']:.6f}deg is not < 0.5deg"
        )
    if camera["maxYawDriftDegrees"] >= THRESHOLDS["cameraYawDriftDegMaxExclusive"]:
        failures.append(
            f"{key}: production camera yaw drift {camera['maxYawDriftDegrees']:.6f}deg is not < 0.5deg"
        )
    if camera["maxYawFieldDriftDegrees"] >= THRESHOLDS["cameraYawDriftDegMaxExclusive"]:
        failures.append(
            f"{key}: production camera YawDeg field drift "
            f"{camera['maxYawFieldDriftDegrees']:.6f}deg is not < 0.5deg"
        )
    if camera["maxPitchFieldDriftDegrees"] >= THRESHOLDS["cameraPitchDriftDegMaxExclusive"]:
        failures.append(
            f"{key}: production camera PitchDeg drift "
            f"{camera['maxPitchFieldDriftDegrees']:.6f}deg is not < 0.5deg"
        )
    if camera["maxUpDriftDegrees"] >= THRESHOLDS["cameraUpDriftDegMaxExclusive"]:
        failures.append(
            f"{key}: production camera up-vector drift "
            f"{camera['maxUpDriftDegrees']:.6f}deg is not < 0.5deg"
        )
    pose_metrics, pose_failures = _analyse_actor_state(
        f"production/{key}", "Run", [sample["visual"] for sample in moving],
        stationary_root=False, require_full_state=False)
    metrics["productionVisualPose"] = pose_metrics
    failures.extend(pose_failures)
    facing_metrics, facing_failures = _analyse_locomotion_facing(key, samples, expected)
    metrics["anatomicalBodyFacing"] = facing_metrics
    failures.extend(facing_failures)
    return metrics, failures


def _camera_metrics(samples: list[dict[str, Any]]) -> dict[str, Any]:
    yaw = samples[0]["yawDeg"]
    pitch = samples[0]["pitchDeg"]
    expected = _expected_direction(yaw, (0.0, 1.0))
    horizontal_forwards = [
        _normalise_xz((sample["forward"][0], sample["forward"][2])) for sample in samples
    ]
    errors = [_angle_deg(forward, expected) for forward in horizontal_forwards]
    baseline = horizontal_forwards[0]
    drifts = [_angle_deg(forward, baseline) for forward in horizontal_forwards]
    yaw_field_drifts = [
        _wrapped_angle_delta_deg(sample["yawDeg"], yaw) for sample in samples
    ]
    pitch_field_drifts = [abs(sample["pitchDeg"] - pitch) for sample in samples]
    baseline_up = samples[0]["up"]
    up_drifts = [_vector_angle_deg3(baseline_up, sample["up"]) for sample in samples]
    return {
        "expectedForwardXZ": list(expected),
        "yawErrorDegrees": errors,
        "yawDriftFromBaselineDegrees": drifts,
        "yawFieldDriftFromBaselineDegrees": yaw_field_drifts,
        "pitchFieldDriftFromBaselineDegrees": pitch_field_drifts,
        "upDriftFromBaselineDegrees": up_drifts,
        "maxYawErrorDegrees": max(errors, default=180.0),
        "maxYawDriftDegrees": max(drifts, default=180.0),
        "maxYawFieldDriftDegrees": max(yaw_field_drifts, default=180.0),
        "maxPitchFieldDriftDegrees": max(pitch_field_drifts, default=180.0),
        "maxUpDriftDegrees": max(up_drifts, default=180.0),
    }


def _analyse_direction(
    key: str,
    probe: dict[str, Any],
    snapshots: list[dict[str, Any]],
    lateral: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    expected = _expected_direction(probe["yawDeg"], KEY_INPUTS[key])
    start = probe["startPosition"]
    end = probe["endPosition"]
    displacement = (end[0] - start[0], end[2] - start[2])
    travel = math.hypot(*displacement)
    actual = _normalise_xz(displacement)
    perpendicular = (-expected[1], expected[0])
    direction_dot = _dot(actual, expected)
    secondary_ratio = abs(_dot(actual, perpendicular))
    hero_forward = _normalise_xz(probe["heroForward"])
    hero_forward_dot = _dot(hero_forward, expected)
    helper_direction = _normalise_xz(probe["resolvedDirection"])
    helper_direction_dot = _dot(helper_direction, expected)
    camera = _camera_metrics(snapshots)

    metrics = {
        "expectedDirectionXZ": list(expected),
        "displacementXZ": list(displacement),
        "horizontalTravel": travel,
        "movementDirectionDot": direction_dot,
        "secondaryAxisRatio": secondary_ratio,
        "heroForwardDot": hero_forward_dot,
        "resolvedDirectionDot": helper_direction_dot,
        "camera": camera,
    }
    failures: list[str] = []
    if travel < THRESHOLDS["minimumHorizontalTravel"]:
        failures.append(f"{key}: horizontal travel {travel:.6f}m is below minimum")
    if direction_dot < THRESHOLDS["movementDirectionDotMin"]:
        failures.append(f"{key}: movement direction dot {direction_dot:.6f} < 0.98")
    if secondary_ratio > THRESHOLDS["secondaryAxisRatioMax"]:
        failures.append(f"{key}: secondary-axis ratio {secondary_ratio:.6f} > 0.05")
    if hero_forward_dot < THRESHOLDS["locomotionHeroForwardDotMin"]:
        failures.append(
            f"{key}: hero forward dot {hero_forward_dot:.6f} < "
            f"{THRESHOLDS['locomotionHeroForwardDotMin']:.2f} (backwards-facing guard)"
        )
    if helper_direction_dot < THRESHOLDS["movementDirectionDotMin"]:
        failures.append(f"{key}: resolved direction dot {helper_direction_dot:.6f} < 0.98")
    if lateral["horizontalTravel"] < THRESHOLDS["minimumLateralStressTravel"]:
        failures.append(
            f"{key}: lateral camera stress moved only {lateral['horizontalTravel']:.6f}m"
        )
    if camera["maxYawErrorDegrees"] >= THRESHOLDS["cameraYawErrorDegMaxExclusive"]:
        failures.append(
            f"{key}: camera yaw error {camera['maxYawErrorDegrees']:.6f}deg is not < 0.5deg"
        )
    if camera["maxYawDriftDegrees"] >= THRESHOLDS["cameraYawDriftDegMaxExclusive"]:
        failures.append(
            f"{key}: camera yaw drift {camera['maxYawDriftDegrees']:.6f}deg is not < 0.5deg"
        )
    if camera["maxYawFieldDriftDegrees"] >= THRESHOLDS["cameraYawDriftDegMaxExclusive"]:
        failures.append(
            f"{key}: camera YawDeg field drift "
            f"{camera['maxYawFieldDriftDegrees']:.6f}deg is not < 0.5deg"
        )
    if camera["maxPitchFieldDriftDegrees"] >= THRESHOLDS["cameraPitchDriftDegMaxExclusive"]:
        failures.append(
            f"{key}: camera PitchDeg drift "
            f"{camera['maxPitchFieldDriftDegrees']:.6f}deg is not < 0.5deg"
        )
    if camera["maxUpDriftDegrees"] >= THRESHOLDS["cameraUpDriftDegMaxExclusive"]:
        failures.append(
            f"{key}: camera up-vector drift "
            f"{camera['maxUpDriftDegrees']:.6f}deg is not < 0.5deg"
        )
    return metrics, failures


def _force_open_scene(client: EditorMcp) -> str:
    opened = client.eval(
        'return XEngine.Editor.GUI.SceneView.EditorSceneManager.OpenScene("'
        + SCENE_PATH
        + '");',
        timeout=300,
    )
    if opened.strip().casefold() != "true":
        raise RuntimeError(f"failed to open required scene {SCENE_PATH}: {opened!r}")
    state = response_value(client.tool("runtime_state", timeout=60))
    actual = str(_state_value(state, "scenePath") or "").replace("\\", "/").lstrip("/")
    if actual.casefold() != SCENE_PATH.casefold():
        raise RuntimeError(f"required scene mismatch: expected {SCENE_PATH}, got {actual!r}")
    return actual


def _sample_camera_after_lateral_stress(
    client: EditorMcp, schedule: tuple[float, ...]
) -> list[dict[str, Any]]:
    samples = [_parse_camera(client.eval(CAMERA_SNAPSHOT_CODE), 0.0)]
    started = time.monotonic()
    for target_time in schedule:
        remaining = target_time - (time.monotonic() - started)
        if remaining > 0:
            time.sleep(remaining)
        samples.append(_parse_camera(client.eval(CAMERA_SNAPSHOT_CODE), time.monotonic() - started))
    return samples


def _build_profile_evidence(project: Path) -> dict[str, Any]:
    path = project / "Assets/Settings/BuildProfiles/Desktop.buildprofile"
    if not path.is_file():
        return {"path": str(path), "exists": False, "enabledScenes": []}
    try:
        # Echo text is JSON-shaped but typed integer literals such as ``1L`` make a full
        # ``json.loads`` invalid.  Read only the scene-entry fields this acceptance needs.
        text = path.read_text(encoding="utf-8")
        override_match = re.search(
            r'"OverrideScenes"\s*:\s*(true|false|1B|0B)', text, re.IGNORECASE
        )
        override_token = override_match.group(1).casefold() if override_match else ""
        override_scenes = override_token in {"true", "1b"}
        entries = re.findall(
            r'\{\s*"Path"\s*:\s*"([^"]+)"(?:(?!\}\s*,?\s*\{).)*?'
            r'"Enabled"\s*:\s*(true|false|1B|0B)\s*\}',
            text,
            flags=re.IGNORECASE | re.DOTALL,
        )
        enabled = [scene for scene, token in entries if token.casefold() in {"true", "1b"}]
        return {
            "path": str(path),
            "exists": True,
            "format": "echo-text",
            "overrideScenes": override_scenes,
            "enabledScenes": enabled,
            "battle2IsOnlyEnabledScene": override_scenes
            and enabled == ["Assets/Scenes/ZonezeroBattle2.scene"],
        }
    except Exception as exc:
        return {"path": str(path), "exists": True, "error": f"{type(exc).__name__}: {exc}"}


def _analyse_w_visual(
    before: dict[str, Any], after: dict[str, Any], build_profile: dict[str, Any]
) -> tuple[dict[str, Any], list[str]]:
    root_delta = (
        after["rootPosition"][0] - before["rootPosition"][0],
        after["rootPosition"][2] - before["rootPosition"][2],
    )
    bone_delta = (
        after["travelBoneWorldPosition"][0] - before["travelBoneWorldPosition"][0],
        after["travelBoneWorldPosition"][2] - before["travelBoneWorldPosition"][2],
    )
    camera_forward = _normalise_xz(
        (after["cameraForward"][0], after["cameraForward"][2])
    )
    screen_delta = [
        after["screenProxy"][index] - before["screenProxy"][index] for index in range(2)
    ]
    root_dot = _dot(_normalise_xz(root_delta), camera_forward)
    bone_dot = _dot(_normalise_xz(bone_delta), camera_forward)
    root_travel = math.hypot(*root_delta)
    bone_travel = math.hypot(*bone_delta)
    root_forward_dot = _dot(
        _normalise_xz(after["rootForwardXZ"]), camera_forward
    )
    bone_forward_to_root = _dot(
        _normalise_xz(after["travelBoneForwardXZ"]),
        _normalise_xz(after["rootForwardXZ"]),
    )
    launch_is_battle2 = build_profile.get("battle2IsOnlyEnabledScene") is True
    if root_dot >= 0.98 and bone_dot >= 0.98 and launch_is_battle2:
        conclusion = (
            "Current Battle2 moves both the GameObject root and Bip001 along the camera-authored "
            "W direction. The camera follows the hero, so screen position remains nearly centred; "
            "a visible backward impression is not a world-motion sign reversal in this scene."
        )
    elif not launch_is_battle2:
        conclusion = (
            "The active build profile does not exclusively launch ZonezeroBattle2; reproduce from "
            "the forced scene before changing movement signs."
        )
    else:
        conclusion = "World and visual travel disagree; inspect model-root orientation before changing input signs."
    failures: list[str] = []
    if root_travel < THRESHOLDS["minimumHorizontalTravel"]:
        failures.append(f"W: visual root travelled only {root_travel:.6f}m")
    elif root_dot < THRESHOLDS["movementDirectionDotMin"]:
        failures.append(f"W: visual root/camera-forward dot {root_dot:.6f} < 0.98")
    if bone_travel < THRESHOLDS["minimumHorizontalTravel"]:
        failures.append(f"W: visual Bip001 travelled only {bone_travel:.6f}m")
    elif bone_dot < THRESHOLDS["visualMovementDirectionDotMin"]:
        failures.append(
            f"W: visual Bip001/camera-forward dot {bone_dot:.6f} < "
            f"{THRESHOLDS['visualMovementDirectionDotMin']:.2f}"
        )
    if root_forward_dot < THRESHOLDS["locomotionHeroForwardDotMin"]:
        failures.append(
            f"W: visual hero-forward/camera-forward dot {root_forward_dot:.6f} < "
            f"{THRESHOLDS['locomotionHeroForwardDotMin']:.2f}"
        )
    if not all(
        math.isfinite(value)
        for value in (*root_delta, *bone_delta, root_dot, bone_dot, *screen_delta)
    ):
        failures.append("W: visual direction evidence contains NaN/Infinity")
    metrics = {
        "rootDeltaXZ": list(root_delta),
        "travelBoneWorldDeltaXZ": list(bone_delta),
        "rootTravel": root_travel,
        "travelBoneWorldTravel": bone_travel,
        "rootTravelDotCameraForward": root_dot,
        "travelBoneTravelDotCameraForward": bone_dot,
        "rootForwardDotCameraForward": root_forward_dot,
        "travelBoneForwardDotRootForward": bone_forward_to_root,
        "screenProxyDeltaXY": screen_delta,
        "screenProxyDepthBeforeAfter": [before["screenProxy"][2], after["screenProxy"][2]],
        "buildProfileBattle2Only": launch_is_battle2,
        "conclusion": conclusion,
    }
    return metrics, failures



def _self_test() -> int:
    """Synthetic passing baselines and targeted regressions; never starts an editor."""
    import copy
    checks: list[str] = []

    def require(condition: bool, name: str) -> None:
        if not condition:
            raise AssertionError(name)
        checks.append(name)

    rpc = '{"result":{"ok":true},"id":338,"jsonrpc":"2.0"}'
    require(_decode_mcp_response_line(rpc)["id"] == 338, "complete MCP response is decoded")
    require(_decode_mcp_response_line(rpc + 'VAO: [ID 0] Mesh uploaded successfully to VRAM (GPU)\n')["id"] == 338,
            "complete MCP response survives the observed interleaved VAO log suffix")
    require(_decode_mcp_response_line('VAO: [ID 0] Mesh uploaded successfully to VRAM (GPU)\n') is None,
            "ordinary editor logs are not treated as RPC responses")
    require(_decode_mcp_response_line('{"id":338,"message":"renderer uploaded"}') is None,
            "ordinary JSON logs with an integer id are not mistaken for RPC responses")
    require(_decode_mcp_response_line('{"jsonrpc":"2.0","method":"notifications/progress"}') is None,
            "server notifications do not complete a pending RPC")
    for name, text in (("truncated MCP JSON", rpc[:-4]),
                       ("damaged MCP JSON", '{"result":BROKEN,"id":338}'),
                       ("unrecognized trailing RPC corruption", rpc + '{"incomplete":'),
                       ("missing RPC result/error", '{"jsonrpc":"2.0","id":338}'),
                       ("ambiguous RPC result/error", '{"jsonrpc":"2.0","id":338,"result":{},"error":{}}'),
                       ("boolean RPC id", '{"jsonrpc":"2.0","id":true,"result":{}}')):
        try:
            _decode_mcp_response_line(text)
        except ValueError:
            require(True, name + " fails explicitly")
        else:
            require(False, name + " was incorrectly accepted")

    class PageClient:
        def __init__(self, pages: list[str]):
            self.pages = iter(pages)

        def eval(self, code: str, timeout: float) -> str:
            return next(self.pages)

    collected: list[str] = []
    page_client = PageClient(["FRAME|0\nFRAME|1\n", "CAPTURE_READY|1\n", "FRAME|2\nDONE\n"])
    for _ in range(3):
        combined = _read_sample_updates(page_client, "fixture", collected, timeout=1)
    require(combined.splitlines() == ["FRAME|0", "FRAME|1", "CAPTURE_READY|1", "FRAME|2", "DONE"],
            "incremental pages preserve each frame, capture marker and DONE exactly once")
    try:
        _read_sample_updates(PageClient(["MISSING"]), "fixture", [], timeout=1)
    except RuntimeError:
        require(True, "missing sample queue fails explicitly")
    else:
        require(False, "missing sample queue was incorrectly accepted")

    def visual_frames(distance: float = 0.0) -> list[dict[str, Any]]:
        result = []
        for index in range(21):
            z = distance * index / 20
            rotation = [math.sin(0.05 + index * 0.01), 0.0, 0.0, math.cos(0.05 + index * 0.01)]
            result.append({
                "frame": index, "engineFrame": index + 100, "sampledWhilePaused": False,
                "stateObserved": True, "normalizedTime": index / 20,
                "applyRootMotion": False, "rootPosition": [0.0, 0.0, z],
                "bip001WorldPosition": [0.0, 1.0, z], "bip001LocalPosition": [0.0, 1.0, 0.0],
                "bip001ReferenceLocalPosition": [0.0, 1.0, 0.0],
                "pelvisWorldPosition": [0.0, 1.0, z], "pelvisLocalPosition": [0.0, 0.0, 0.0],
                "leftThighWorldPosition": [-0.1, 0.9, z],
                "rightThighWorldPosition": [0.1, 0.9, z],
                "spineWorldPosition": [0.0, 1.2, z],
                "pelvisReferenceLocalPosition": [0.0, 0.0, 0.0],
                "primaryBodyRendererCount": 1, "primaryBodyBoundsCenter": [0.0, 1.0, z],
                "primaryBodyBoundsSize": [1.0, 2.0, 0.5], "enabledRendererCount": 1,
                "unresolvedBoneCount": 0, "clip": "Run",
                "corePose": {name: {"rotation": rotation[:], "referenceRotation": [0.0, 0.0, 0.0, 1.0]}
                             for name in CORE_POSE_BONES},
                "renderers": [{"index": 0, "name": "body", "primary": True, "boneCount": 10,
                               "unresolvedBoneCount": 0, "hasRootBone": True, "rootPosition": [0.0, 1.0, z],
                               "boundsCenter": [0.0, 1.0, z], "boundsSize": [1.0, 2.0, 0.5]}],
            })
        return result

    baseline = visual_frames()
    require(not _analyse_actor_state("fixture", "Run", baseline)[1], "valid animated actor passes")
    controlled = copy.deepcopy(baseline)
    for index, frame in enumerate(controlled):
        frame["sampledWhilePaused"] = True
        frame["normalizedTime"] = (index + 1) * ISOLATED_NORMALIZED_STEP
        frame["controlledEvaluation"] = {
            "deltaSeconds": ISOLATED_NORMALIZED_STEP,
            "normalizedTimeBefore": index * ISOLATED_NORMALIZED_STEP,
            "clipDurationSeconds": 1.0,
            "playbackSpeed": 1.0,
            "presentationBefore": index + 100,
            "presentationAfter": index + 101,
        }
    require(not _analyse_actor_state("fixture", "Run", controlled, controlled_evaluation=True)[1],
            "explicit controlled evaluation accepts paused rendered frames with advancing graph telemetry")
    require(bool(_analyse_actor_state("fixture", "Run", controlled)[1]),
            "controlled paused telemetry cannot pass production or natural observation")
    for label, field, value in (
        ("controlled simulation unexpectedly resumed", "sampledWhilePaused", False),
        ("wall-clock jump during controlled evaluation", "normalizedTime", 1.0),
    ):
        broken = copy.deepcopy(controlled)
        broken[10][field] = value
        require(bool(_analyse_actor_state("fixture", "Run", broken, controlled_evaluation=True)[1]), label + " is rejected")
    for label, field, value in (
        ("zero controlled delta", "deltaSeconds", 0.0),
        ("incorrect controlled playback rate", "playbackSpeed", 2.0),
        ("animation advances outside controlled evaluation", "normalizedTimeBefore", 0.26),
        ("stale pose sampled before presentation", "presentationAfter", 110),
    ):
        broken = copy.deepcopy(controlled)
        broken[10]["controlledEvaluation"][field] = value
        require(bool(_analyse_actor_state("fixture", "Run", broken, controlled_evaluation=True)[1]), label + " is rejected")
    broken = copy.deepcopy(controlled)
    del broken[10]["controlledEvaluation"]
    require(bool(_analyse_actor_state("fixture", "Run", broken, controlled_evaluation=True)[1]),
            "missing controlled evaluation telemetry is rejected")
    capture_samples = [{"frame": index * 10 + 1, "normalizedTime": target}
                       for index, target in enumerate((0.2, 0.5, 0.8))]
    capture_entries = [{"frame": sample["frame"], "actualNormalizedTime": sample["normalizedTime"],
                        "targetNormalizedTime": sample["normalizedTime"]} for sample in capture_samples]
    require(not _validate_isolated_captures("fixture", "Run", capture_samples, capture_entries),
            "three distinct evaluated keyframes satisfy isolated screenshot acceptance")
    for label, field, value in (
        ("endpoint screenshot substituted for a skipped keyframe", "actualNormalizedTime", 1.0),
        ("same-frame screenshot reused at another target", "frame", capture_entries[0]["frame"]),
        ("non-finite screenshot animation time", "actualNormalizedTime", math.nan),
    ):
        broken_captures = copy.deepcopy(capture_entries)
        broken_captures[1][field] = value
        require(bool(_validate_isolated_captures("fixture", "Run", capture_samples, broken_captures)), label + " is rejected")
    require(bool(_validate_isolated_captures("fixture", "Run", capture_samples, capture_entries[:2])),
            "missing third normalized-time screenshot fails acceptance")
    for label, field, value in (
        ("paused-frame telemetry", "sampledWhilePaused", True),
        ("duplicate engine frame", "engineFrame", baseline[9]["engineFrame"]),
    ):
        broken = copy.deepcopy(baseline)
        broken[10][field] = value
        require(bool(_analyse_actor_state("fixture", "Run", broken)[1]), label + " is rejected")
    summary = _summarize_failures([
        "fixture/Run: frame 10 renderer body has NaN/Infinity",
        "fixture/Run: frame 11 renderer body has NaN/Infinity",
        "fixture/Run: frame 15 renderer body has NaN/Infinity",
        "fixture/Run: a separate regression",
    ])
    require(summary == [
        "fixture/Run: frames 10-15 (count=3): renderer body has NaN/Infinity",
        "fixture/Run: a separate regression",
    ], "repeated diagnostics retain first/last/count and distinct conditions")
    for label, field, value in (
        ("vertical Pelvis flyoff", "pelvisWorldPosition", [0.0, 50.0, 0.0]),
        ("oversized bounds", "primaryBodyBoundsSize", [50.0, 2.0, 0.5]),
        ("NaN bounds", "primaryBodyBoundsCenter", [math.nan, 1.0, 0.0]),
        ("infinite bounds", "primaryBodyBoundsSize", [math.inf, 2.0, 0.5]),
        ("missing renderer", "renderers", []),
    ):
        broken = copy.deepcopy(baseline)
        broken[10][field] = value
        require(bool(_analyse_actor_state("fixture", "Run", broken)[1]), label + " is rejected")
    broken = copy.deepcopy(baseline)
    for frame in broken:
        for pose in frame["corePose"].values():
            pose["rotation"] = pose["referenceRotation"][:]
    require(bool(_analyse_actor_state("fixture", "Run", broken)[1]), "static T-pose is rejected")
    broken = copy.deepcopy(baseline)
    broken[5]["corePose"][CORE_POSE_BONES[0]]["rotation"] = [math.nan, 0.0, 0.0, 1.0]
    require(bool(_analyse_actor_state("fixture", "Run", broken)[1]), "NaN quaternion is rejected")

    # Full production action analyzer, including visual and requested-distance gates.
    evidence_keys = re.findall(r'renderer_metrics\["([^"]+)"\]',
                               Path(__file__).read_text(encoding="utf-8").split('def _renderer_evidence_failures', 1)[1].split('def _analyse_action', 1)[0])
    evidence = {"metrics": {key: 0 for key in evidence_keys}, "distanceSummary": {}, "renderers": []}
    for key in ("primaryBodyRendererCount", "bip001BindingCount", "bip001KeyBoneCount", "pelvisKeyBoneCount"):
        evidence["metrics"][key] = 1
    evidence["metrics"]["allEvidenceFinite"] = True

    def action_frames(key: str, distance: float = 0.0) -> list[dict[str, Any]]:
        frames = []
        for visual in visual_frames(distance):
            index = visual["frame"]
            frames.append({
                "frame": index, "rootPosition": visual["rootPosition"], "heroForwardXZ": [0.0, 1.0],
                "characterControllerRequestedMotion": [0.0, 0.0, distance / 20 if index else 0.0],
                "travelBonePath": "Bip001", "travelBoneWorldPosition": visual["bip001WorldPosition"],
                "travelBoneLocalPosition": visual["bip001LocalPosition"],
                "activeAction": {"J": "Normal", "K": "SkillK", "L": "SkillL", "I": "SkillIStart"}[key],
                "clip": ACTION_STATES[key][0], "applyRootMotion": False, "lungeDirectionXZ": [0.0, 1.0],
                "skillLungeSpeed": 7.5, "skillLungeDuration": 0.38, "lockedTarget": "<null>",
                "lockedTargetPosition": [0.0, 0.0, 20.0], "visual": visual,
            })
        return frames

    require(not _analyse_action("L", action_frames("L", 2.85), evidence)[1], "L exactly 2.85m passes")
    require(any("requested displacement path" in failure for failure in _analyse_action("L", action_frames("L", 2.86), evidence)[1]),
            "L 2.86m request is rejected")
    for key in ("J", "K"):
        require(not _analyse_action(key, action_frames(key), evidence)[1], key + " zero movement passes")
    for key in ("J", "K", "I"):
        failures = _analyse_action(key, action_frames(key, 0.001), evidence)[1]
        require(any("only L may request" in failure for failure in failures), key + " code movement is rejected")

    samples = []
    for visual in visual_frames(2.0):
        index = visual["frame"]
        samples.append({
            "frame": index, "elapsedSeconds": index / 60, "input": [0.0, 1.0],
            "resolvedWishXZ": [0.0, 1.0], "rootPosition": visual["rootPosition"],
            "heroForwardXZ": [0.0, 1.0], "characterControllerRequestedMotion": [0.0, 0.0, 0.1],
            "collisionFlags": 0, "travelBonePath": "Bip001", "pelvisBonePath": "Bip001 Pelvis",
            "travelBoneWorldPosition": visual["bip001WorldPosition"], "travelBoneLocalPosition": visual["bip001LocalPosition"],
            "pelvisWorldPosition": visual["pelvisWorldPosition"], "primaryBodyBoundsCenter": visual["primaryBodyBoundsCenter"],
            "primaryBodyBoundsSize": visual["primaryBodyBoundsSize"], "primaryBodyRendererCount": 1,
            "cameraYawDeg": 0.0, "cameraPitchDeg": 0.0, "cameraForward": [0.0, 0.0, 1.0],
            "cameraUp": [0.0, 1.0, 0.0], "visual": visual,
        })
    require(not _analyse_production_locomotion("W", samples)[1], "root/controller/body aligned movement passes")
    require(_anatomical_forward_xz(samples[5]["visual"]) == (0.0, 1.0),
            "anatomical right cross torso up identifies forward independently of transform axes")
    for name, mutate in (
        ("reversed anatomical body", lambda visual: visual.update({
            "leftThighWorldPosition": visual["rightThighWorldPosition"],
            "rightThighWorldPosition": visual["leftThighWorldPosition"],
        })),
        ("degenerate anatomical axis", lambda visual: visual.update({
            "spineWorldPosition": visual["pelvisWorldPosition"][:],
        })),
        ("NaN anatomical axis", lambda visual: visual.update({
            "spineWorldPosition": [math.nan, 1.2, 0.0],
        })),
    ):
        broken = copy.deepcopy(samples)
        for frame in broken:
            mutate(frame["visual"])
        require(any("anatomical" in failure for failure in _analyse_production_locomotion("W", broken)[1]),
                name + " is rejected even when the root moves and faces forward")
    twisted = copy.deepcopy(samples)
    twist_angle = math.radians(45.0)
    for frame in twisted:
        z = frame["rootPosition"][2]
        frame["visual"]["leftThighWorldPosition"] = [-0.1 * math.cos(twist_angle), 0.9, z + 0.1 * math.sin(twist_angle)]
        frame["visual"]["rightThighWorldPosition"] = [0.1 * math.cos(twist_angle), 0.9, z - 0.1 * math.sin(twist_angle)]
    require(not _analyse_locomotion_facing("W", twisted, (0.0, 1.0))[1],
            "45-degree running torso twist passes the 60-degree anatomical tolerance")
    turning = copy.deepcopy(samples)
    for frame in turning[:7]:
        frame["heroForwardXZ"] = [1.0, 0.0]
        visual = frame["visual"]
        visual["leftThighWorldPosition"], visual["rightThighWorldPosition"] = (
            visual["rightThighWorldPosition"], visual["leftThighWorldPosition"])
    require(not _analyse_locomotion_facing("W", turning, (0.0, 1.0))[1],
            "anatomical facing is enforced after root turning settles")
    for frame in turning[:7]:
        frame["heroForwardXZ"] = [0.0, 1.0]
        frame["visual"]["clip"] = "Attack_Normal_1"
    require(not _analyse_locomotion_facing("W", turning, (0.0, 1.0))[1],
            "attack flips are excluded from the Run-only anatomical gate")
    require(math.isclose(_vector_angle_deg3([0.0, 1.0, 0.0], [1.0, 0.0, 0.0]), 90.0),
            "three-dimensional perpendicular camera-up vectors differ by 90 degrees")
    for name, up in (
        ("camera-up drift", [1.0, 0.0, 0.0]),
        ("NaN camera-up", [math.nan, 1.0, 0.0]),
        ("infinite camera-up", [0.0, math.inf, 0.0]),
        ("zero camera-up", [0.0, 0.0, 0.0]),
        ("malformed camera-up", [0.0, 1.0, 0.0, 0.0]),
    ):
        broken = copy.deepcopy(samples)
        broken[10]["cameraUp"] = up
        require(any("up-vector drift" in failure for failure in _analyse_production_locomotion("W", broken)[1]),
                name + " is rejected by the production camera gate")
    broken = copy.deepcopy(samples)
    for frame in broken:
        frame["primaryBodyBoundsCenter"][2] *= -1
    require(any("primary body bounds movement direction" in failure for failure in _analyse_production_locomotion("W", broken)[1]),
            "root forward but body backward is rejected")
    factory = _visual_observer_factory()
    require(not re.search(r'__[A-Z_]+__', factory), "shared visual observer placeholders resolved")
    require('animator.Play(' not in factory and 'Enabled = false' not in factory and 'SetValue(' not in factory,
            "production visual observer does not alter tested behavior")
    print(json.dumps({"passed": True, "checks": checks}, ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    script = Path(__file__).resolve()
    source_project = script.parents[1]
    engine = source_project.parent
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, default=source_project)
    parser.add_argument(
        "--editor",
        type=Path,
        default=engine / "Build/Editor/Release/net10.0/XEngine.Editor.exe",
    )
    parser.add_argument("--backend", choices=("opengl", "vulkan", "d3d12"), default="vulkan")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--ready-timeout", type=float, default=240.0)
    parser.add_argument("--locomotion-frames", type=int, default=180)
    parser.add_argument("--action-max-frames", type=int, default=360)
    parser.add_argument("--actor-state-max-frames", type=int, default=360)
    parser.add_argument("--natural-frames", type=int, default=1800, help="passive production observation frame count")
    parser.add_argument("--self-test", action="store_true", help="run analyzer negative fixtures without an editor")
    parser.add_argument(
        "--action-capture-frame",
        type=int,
        default=8,
        help=(
            "frame offset after entering the target J/K/L state; for I, within each of "
            "BigSkill_Start, BigSkill, and BigSkill_End"
        ),
    )
    parser.add_argument("--move-iterations", type=int, default=18)
    parser.add_argument("--stress-iterations", type=int, default=12)
    parser.add_argument(
        "--camera-sample-times",
        default="0.02,0.08,0.20,0.50,1.0,2.0,5.0",
        help="comma-separated seconds after lateral target motion",
    )
    args = parser.parse_args()
    if args.self_test:
        return _self_test()

    project = args.project.resolve()
    editor = args.editor.resolve()
    if not editor.is_file():
        parser.error(f"editor not found: {editor}")
    if not (project / "Assets" / SCENE_PATH).is_file():
        parser.error(f"required scene not found below project: {SCENE_PATH}")
    if (
        args.natural_frames <= 0
        or args.locomotion_frames <= 0
        or args.action_max_frames <= 0
        or args.actor_state_max_frames <= 0
        or args.action_capture_frame <= 0
        or args.action_capture_frame > args.action_max_frames
        or args.move_iterations <= 0
        or args.stress_iterations <= 0
    ):
        parser.error("iteration counts must be positive")
    schedule = tuple(
        sorted({float(value) for value in args.camera_sample_times.split(",") if value.strip()})
    )
    if not schedule or schedule[0] < 0:
        parser.error("--camera-sample-times must contain non-negative seconds")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output = (
        args.output or project / "diag" / "battle2-controls" / f"{stamp}-{args.backend}"
    ).resolve()
    output.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {
        "startedUtc": datetime.now(timezone.utc).isoformat(),
        "scenePath": SCENE_PATH,
        "backend": args.backend,
        "editor": str(editor),
        "project": str(project),
        "engineSha": git_sha(engine),
        "projectSha": git_sha(project),
        "sourceProjectSha": git_sha(source_project),
        "buildProfileEvidence": _build_profile_evidence(project),
        "thresholds": THRESHOLDS,
        "probePolicy": {
            "realKeyboardInjection": False,
            "engineInputInjector": True,
            "productionLocomotionTickExercised": True,
            "locomotionFrames": args.locomotion_frames,
            "actionMaxFrames": args.action_max_frames,
            "naturalObservationFrames": args.natural_frames,
            "productionInputActor": "Anbi",
            "actorMatrixMode": ISOLATED_EVALUATION_MODE,
            "isolatedNormalizedStepMax": ISOLATED_NORMALIZED_STEP,
            "isolatedCaptureNormalizedTolerance": ISOLATED_NORMALIZED_TOLERANCE,
            "isolatedGameplayPaused": True,
            "isolatedAnimatorEnabledAndAuthoredSpeedPreserved": True,
            "naturalObserverMutatesControllersOrPlayback": False,
            "actorStateMaxFrames": args.actor_state_max_frames,
            "actionCaptureFrame": args.action_capture_frame,
            "iPhaseCaptureFrameOffset": args.action_capture_frame,
            "iPhaseCaptures": [
                {"phase": phase, "clip": clip} for phase, clip in I_ACTION_PHASES
            ],
            "freshPlaySessionPerDirection": True,
            "freshPlaySessionPerAction": True,
            "expectedProductionHelperSignature": EXPECTED_HELPER_SIGNATURE,
            "fallback": "YawDeg camera forward/right basis, then CombatMotor + CharacterController",
        },
        "directions": {},
        "actions": {},
        "actors": {},
        "animationCurveEvidence": None,
        "screenshots": [],
        "errors": [],
    }

    client = EditorMcp(editor, project, args.backend, output, skin_diag=False)
    try:
        client.initialize()
        report["initialState"] = wait_until_ready(client, args.ready_timeout)
        # Start persistent log observation before entering Play.
        client.tool("runtime_logs", {"limit": 1}, timeout=60)
        client.tool(
            "runtime_menu", {"action": "invoke", "path": "Window/General/New Game View"}, timeout=120
        )
        time.sleep(0.5)

        helper_availability: bool | None = None
        for key, input_xy in KEY_INPUTS.items():
            print(f"[Battle2] production movement {key}", flush=True)
            try:
                state = response_value(client.tool("runtime_state", timeout=60))
                if bool(_state_value(state, "isPlaying")):
                    client.tool("runtime_playmode", {"action": "exit"}, timeout=300)
                    _wait_for_playmode(client, False, args.ready_timeout)

                opened_path = _force_open_scene(client)
                client.tool("runtime_playmode", {"action": "enter"}, timeout=300)
                play_state = _wait_for_playmode(client, True, args.ready_timeout)
                ready = _wait_for_runtime_ready(client, args.ready_timeout)
                time.sleep(0.25)

                preflight = _parse_preflight(client.eval(PREFLIGHT_CODE, timeout=120))
                helper_availability = preflight["productionDirectionHelperAvailable"]
                entry: dict[str, Any] = {
                    "input": list(input_xy),
                    "openedScenePath": opened_path,
                    "playState": play_state,
                    "runtimeReady": ready,
                    "preflight": preflight,
                }
                report["directions"][key] = entry
                if preflight["enabledForbiddenCount"] != 0:
                    report["errors"].append(
                        f"{key}: found {preflight['enabledForbiddenCount']} enabled forbidden camera/controller components"
                    )
                if preflight["enabledHeroControllers"] != 1:
                    report["errors"].append(
                        f"{key}: expected 1 enabled HeroCombatController, got {preflight['enabledHeroControllers']}"
                    )
                if preflight["enabledBattleFollowCameras"] != 1:
                    report["errors"].append(
                        f"{key}: expected 1 enabled BattleFollowCamera, got {preflight['enabledBattleFollowCameras']}"
                    )

                visual_before: dict[str, Any] | None = None
                visual_failures: list[str] = []
                entry["startScreenshot"] = capture(client, output, f"move-{key}-start.png")
                report["screenshots"].append(entry["startScreenshot"])
                if key == "W":
                    visual_before = _parse_visual_direction(
                        client.eval(VISUAL_DIRECTION_CODE, timeout=120)
                    )

                production_samples = _sample_production_locomotion(
                    client, key, args.locomotion_frames, args.ready_timeout
                )
                production_metrics, production_failures = _analyse_production_locomotion(
                    key, production_samples
                )
                entry["endScreenshot"] = capture(client, output, f"move-{key}-end.png")
                report["screenshots"].append(entry["endScreenshot"])
                if key == "W":
                    closeup = _capture_paused_actor_closeup(
                        client, "Battle_Hero", output, "move-W-anatomical-facing-closeup.png")
                    report["screenshots"].append(closeup)
                    entry["anatomicalFacingCloseup"] = {
                        "screenshot": closeup, "sampleFrame": production_samples[-1]["frame"],
                        "engineFrame": production_samples[-1]["visual"]["engineFrame"],
                        "view": "temporaryFixedActorRootCloseupWhilePaused",
                        "productionCameraRestoredBeforeSimulation": True,
                    }
                entry["productionLocomotionSamples"] = production_samples
                entry["productionLocomotionMetrics"] = production_metrics
                report["errors"].extend(production_failures)
                if key == "W" and visual_before is not None:
                    visual_after = _parse_visual_direction(
                        client.eval(VISUAL_DIRECTION_CODE, timeout=120)
                    )
                    visual_metrics, visual_failures = _analyse_w_visual(
                        visual_before, visual_after, report["buildProfileEvidence"]
                    )
                    entry["visualDirectionEvidence"] = {
                        "before": visual_before,
                        "after": visual_after,
                        "metrics": visual_metrics,
                        "failures": visual_failures,
                        "passed": not visual_failures,
                    }
                    report["errors"].extend(visual_failures)
                client.eval('XEngine.Runtime.Application.IsPaused = false; return "resumed";', timeout=60)
                time.sleep(0.1)

                client.tool("runtime_playmode", {"action": "exit"}, timeout=300)
                _wait_for_playmode(client, False, args.ready_timeout)
                _force_open_scene(client)
                client.tool("runtime_playmode", {"action": "enter"}, timeout=300)
                _wait_for_playmode(client, True, args.ready_timeout)
                _wait_for_runtime_ready(client, args.ready_timeout)
                entry["isolatedMotorFreshPlaySession"] = True
                probe_code = (
                    PROBE_TEMPLATE.replace("__INPUT_X__", format(input_xy[0], ".9g"))
                    .replace("__INPUT_Y__", format(input_xy[1], ".9g"))
                    .replace("__MOVE_ITERATIONS__", str(args.move_iterations))
                )
                probe = _parse_probe(client.eval(probe_code, timeout=120))
                # Let the follow camera react to the primary move, then move the target strictly
                # sideways and sample the entire smoothing transient for yaw contamination.
                time.sleep(0.15)
                before_stress = _parse_camera(client.eval(CAMERA_SNAPSHOT_CODE), 0.0)
                lateral_code = LATERAL_STRESS_TEMPLATE.replace(
                    "__STRESS_ITERATIONS__", str(args.stress_iterations)
                )
                lateral = _parse_lateral(client.eval(lateral_code, timeout=120))
                camera_samples = [before_stress]
                camera_samples.extend(_sample_camera_after_lateral_stress(client, schedule))
                isolated_metrics, isolated_failures = _analyse_direction(
                    key, probe, camera_samples, lateral
                )
                failures = [*production_failures, *visual_failures, *isolated_failures]
                entry.update(
                    {
                        "isolatedMotorProbe": probe,
                        "lateralCameraStress": lateral,
                        "cameraSamples": camera_samples,
                        "isolatedMotorMetrics": isolated_metrics,
                        "passed": not failures and preflight["enabledForbiddenCount"] == 0,
                        "failures": failures,
                    }
                )
                report["errors"].extend(isolated_failures)
            except Exception as exc:
                report["errors"].append(
                    f"{key}: driver failure: {type(exc).__name__}: {exc}"
                )
            finally:
                try:
                    client.tool("runtime_playmode", {"action": "exit"}, timeout=300)
                    _wait_for_playmode(client, False, args.ready_timeout)
                except Exception as exc:
                    report["errors"].append(
                        f"{key}: failed to exit Play cleanly: {type(exc).__name__}: {exc}"
                    )

        for key in ACTION_STATES:
            print(f"[Battle2] production action {key}", flush=True)
            try:
                state = response_value(client.tool("runtime_state", timeout=60))
                if bool(_state_value(state, "isPlaying")):
                    client.tool("runtime_playmode", {"action": "exit"}, timeout=300)
                    _wait_for_playmode(client, False, args.ready_timeout)

                opened_path = _force_open_scene(client)
                client.tool("runtime_playmode", {"action": "enter"}, timeout=300)
                play_state = _wait_for_playmode(client, True, args.ready_timeout)
                ready = _wait_for_runtime_ready(client, args.ready_timeout)
                state_ready = _wait_for_action_states(client, key, args.ready_timeout)
                if report["animationCurveEvidence"] is None:
                    report["animationCurveEvidence"] = _parse_action_curve_evidence(
                        client.eval(ACTION_CURVE_EVIDENCE_CODE, timeout=180)
                    )
                preflight = _parse_preflight(client.eval(PREFLIGHT_CODE, timeout=120))
                frames, captures = _run_action_probe(
                    client,
                    key,
                    args.action_max_frames,
                    args.action_capture_frame,
                    args.ready_timeout,
                    output,
                )
                primary_capture = captures[0]
                phase_captures = (
                    {capture_entry["phase"]: capture_entry for capture_entry in captures}
                    if key == "I"
                    else None
                )
                metrics, failures = _analyse_action(
                    key,
                    frames,
                    primary_capture["rendererEvidence"],
                    phase_captures,
                )
                report["screenshots"].extend(
                    capture_entry["keyframeScreenshot"] for capture_entry in captures
                )
                action_entry: dict[str, Any] = {
                    "openedScenePath": opened_path,
                    "playState": play_state,
                    "runtimeReady": ready,
                    "controllerStatesReady": state_ready,
                    "preflight": preflight,
                    "frames": frames,
                    # Backward-compatible aliases remain the single J/K/L capture and the
                    # Start capture for I.  I's complete evidence is in phaseCaptures below.
                    "keyframeScreenshot": primary_capture["keyframeScreenshot"],
                    "captureMarker": primary_capture["marker"],
                    "rendererEvidence": primary_capture["rendererEvidence"],
                    "metrics": metrics,
                    "failures": failures,
                    "passed": not failures and preflight["enabledForbiddenCount"] == 0,
                }
                if phase_captures is not None:
                    action_entry["phaseCaptures"] = phase_captures
                report["actions"][key] = action_entry
                if preflight["enabledForbiddenCount"] != 0:
                    report["errors"].append(
                        f"{key}: found {preflight['enabledForbiddenCount']} enabled forbidden camera/controller components"
                    )
                report["errors"].extend(failures)
            except Exception as exc:
                report["errors"].append(
                    f"{key}: action driver failure: {type(exc).__name__}: {exc}"
                )
            finally:
                try:
                    client.eval(
                        "XEngine.Runtime.InputInjector.Release(XEngine.Runtime.KeyCode."
                        + key
                        + "); XEngine.Runtime.Application.IsPaused = false; return \"cleaned\";",
                        timeout=60,
                    )
                except Exception:
                    pass
                try:
                    client.tool("runtime_playmode", {"action": "exit"}, timeout=300)
                    _wait_for_playmode(client, False, args.ready_timeout)
                except Exception as exc:
                    report["errors"].append(
                        f"{key}: failed to exit action Play cleanly: {type(exc).__name__}: {exc}"
                    )

        # Exercise every relevant state on all three authored player rigs in isolation.  The scene
        # has one HeroCombatController and two AllyCombatAI components, so this is deliberately a
        # separate matrix from the production-input probes above.
        try:
            state = response_value(client.tool("runtime_state", timeout=60))
            if bool(_state_value(state, "isPlaying")):
                client.tool("runtime_playmode", {"action": "exit"}, timeout=300)
                _wait_for_playmode(client, False, args.ready_timeout)
            opened_path = _force_open_scene(client)
            client.tool("runtime_playmode", {"action": "enter"}, timeout=300)
            matrix_play_state = _wait_for_playmode(client, True, args.ready_timeout)
            matrix_runtime_ready = _wait_for_runtime_ready(client, args.ready_timeout)
            try:
                report["naturalObservation"] = _run_natural_observer(
                    client, args.natural_frames, args.ready_timeout, output)
                report["errors"].extend(report["naturalObservation"]["failures"])
                report["screenshots"].extend(item["screenshot"] for item in report["naturalObservation"]["captures"])
            except Exception as exc:
                report["errors"].append(f"natural observation failed: {type(exc).__name__}: {exc}")
            # Restart before isolated asset playback so each mode has authored initial conditions.
            client.tool("runtime_playmode", {"action": "exit"}, timeout=300)
            _wait_for_playmode(client, False, args.ready_timeout)
            _force_open_scene(client)
            client.tool("runtime_playmode", {"action": "enter"}, timeout=300)
            _wait_for_playmode(client, True, args.ready_timeout)
            _wait_for_runtime_ready(client, args.ready_timeout)
            matrix_preflight = _prepare_actor_matrix(client)
            for actor, actor_spec in ACTOR_STATES.items():
                actor_entry: dict[str, Any] = {
                    "root": actor_spec["root"],
                    "mode": ISOLATED_EVALUATION_MODE,
                    "controllerLogicDisabled": True,
                    "openedScenePath": opened_path,
                    "playState": matrix_play_state,
                    "runtimeReady": matrix_runtime_ready,
                    "matrixPreflight": matrix_preflight,
                    "states": {},
                }
                report["actors"][actor] = actor_entry
                for animator_state in actor_spec["states"]:
                    print(f"[Battle2] isolated {actor}/{animator_state}", flush=True)
                    try:
                        actor_frames, actor_captures = _run_actor_state_probe(
                            client,
                            actor,
                            str(actor_spec["root"]),
                            animator_state,
                            args.actor_state_max_frames,
                            args.ready_timeout,
                            output,
                        )
                        actor_metrics, actor_failures = _analyse_actor_state(
                            actor, animator_state, actor_frames, controlled_evaluation=True
                        )
                        report["screenshots"].extend(item["screenshot"] for item in actor_captures)
                        actor_entry["states"][animator_state] = {
                            "frames": actor_frames,
                            "captures": actor_captures,
                            "metrics": actor_metrics,
                            "failures": actor_failures,
                            "passed": not actor_failures,
                        }
                        report["errors"].extend(actor_failures)
                    except Exception as exc:
                        message = (
                            f"{actor}/{animator_state}: actor-state driver failure: "
                            f"{type(exc).__name__}: {exc}"
                        )
                        actor_entry["states"][animator_state] = {
                            "metrics": None,
                            "failures": [message],
                            "passed": False,
                        }
                        report["errors"].append(message)
                actor_entry["passed"] = (
                    set(actor_entry["states"]) == set(actor_spec["states"])
                    and all(
                        state_entry["passed"]
                        for state_entry in actor_entry["states"].values()
                    )
                )
        except Exception as exc:
            report["errors"].append(
                f"actor matrix driver failure: {type(exc).__name__}: {exc}"
            )
        finally:
            try:
                client.tool("runtime_playmode", {"action": "exit"}, timeout=300)
                _wait_for_playmode(client, False, args.ready_timeout)
            except Exception as exc:
                report["errors"].append(
                    "actor matrix: failed to exit Play cleanly: "
                    f"{type(exc).__name__}: {exc}"
                )

        missing_actors = [actor for actor in ACTOR_STATES if actor not in report["actors"]]
        if missing_actors:
            report["errors"].append(f"actor matrix missing required actors: {missing_actors}")

        report["productionDirectionHelper"] = {
            "available": helper_availability,
            "expectedSignature": EXPECTED_HELPER_SIGNATURE,
            "recommendation": (
                None
                if helper_availability
                else "Extract HeroCombatController's YawDeg-based forward/right calculation into "
                "the side-effect-free helper above and call it from LocomotionTick."
            ),
        }
        logs = client.tool(
            "runtime_logs", {"limit": 200, "minimumSeverity": "Warning"}, timeout=120
        )
        report["runtimeLogs"] = response_value(logs)
        for message in fatal_runtime_logs(runtime_log_entries(logs)):
            report["errors"].append(f"runtime log failure: {message}")
    except Exception as exc:
        report["errors"].append(f"driver failure: {type(exc).__name__}: {exc}")
    finally:
        client.close()
        report["finishedUtc"] = datetime.now(timezone.utc).isoformat()
        report["errors"] = _summarize_failures(report["errors"])
        report["passed"] = not report["errors"]
        (output / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    print(str(output))
    print(json.dumps({"passed": report["passed"], "errors": report["errors"]}, ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
