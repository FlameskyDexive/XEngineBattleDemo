#!/usr/bin/env python3
"""Deterministic Battle2 WASD and fixed-heading camera acceptance through editor MCP.

The probe launches an editor process that it owns, forces ``Scenes/ZonezeroBattle2.scene``,
and starts a fresh Play session for each direction.  It never injects keyboard input and never
persists scene changes.  When the production controller exposes the proposed pure direction
helper it is invoked through reflection; until then the report explicitly records that the
YawDeg basis fallback was used before exercising CombatMotor and CharacterController.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from battle2_skinning_diagnostic import (
    EditorMcp,
    capture,
    fatal_runtime_logs,
    git_sha,
    response_value,
    runtime_log_entries,
    wait_until_ready,
)


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

THRESHOLDS = {
    "movementDirectionDotMin": 0.98,
    "secondaryAxisRatioMax": 0.05,
    "heroForwardDotMin": 0.98,
    "cameraYawErrorDegMaxExclusive": 0.5,
    "cameraYawDriftDegMaxExclusive": 0.5,
    "minimumHorizontalTravel": 0.05,
    "minimumLateralStressTravel": 0.05,
    "nonLungeRootTravelMax": 0.03,
    "nonLungeFrameTeleportMax": 0.02,
    "lungeTravelAllowance": 0.05,
    "lungeFrameAllowance": 0.02,
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
var instanceNonPublic = System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.NonPublic;
var instancePublic = System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.Public;
var staticFlags = System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic
    | System.Reflection.BindingFlags.Static;
var moveField = controller.GetType().GetField("_move", instanceNonPublic);
var yawField = rig.GetType().GetField("YawDeg", instancePublic);
var lastVelocityField = typeof(XEngine.Runtime.CharacterController).GetField("lastVelocity", instanceNonPublic);
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
        var resolveRoot = typeof(XEngine.Runtime.Animator).GetMethod("ResolveRootBone", instanceNonPublic);
        travelBone = resolveRoot?.Invoke(animator, null) as XEngine.Vector.Transform;
        if (travelBone != null) travelBonePath = "<resolved-root>";
    }
}
var samples = new System.Collections.Concurrent.ConcurrentQueue<string>();
System.AppDomain.CurrentDomain.SetData(storageKey, samples);

string Snapshot(int frame)
{
    var moveAction = moveField?.GetValue(controller);
    var currentValue = moveAction?.GetType().GetField("_currentValue", instanceNonPublic)?.GetValue(moveAction);
    var input = currentValue is XEngine.Vector.Float2 inputValue ? inputValue : default;
    float yawDeg = (float)(yawField?.GetValue(rig) ?? 0f);
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
    var requestedMotion = (XEngine.Vector.Float3)(lastVelocityField?.GetValue(cc)
        ?? default(XEngine.Vector.Float3));
    var rootPosition = hero.Transform.Position;
    var heroForward = hero.Transform.Forward;
    var boneWorld = travelBone?.Position ?? default;
    var boneLocal = travelBone?.LocalPosition ?? default;
    var cameraForwardNow = rig.Transform.Forward;
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
        + action + "|" + clip;
}

async XEngine.Async.XTaskVoid RunProbe()
{
    samples.Enqueue(Snapshot(0));
    XEngine.Runtime.InputInjector.Press(XEngine.Runtime.KeyCode.__KEY__);
    try
    {
        for (int frame = 1; frame <= __FRAME_COUNT__; frame++)
        {
            await XEngine.Async.XTask.NextFrame(XEngine.Async.FrameTiming.EndOfFrame);
            samples.Enqueue(Snapshot(frame));
        }
    }
    catch (System.Exception ex)
    {
        samples.Enqueue("ERROR|" + ex.GetType().Name + ":" + ex.Message);
    }
    finally
    {
        XEngine.Runtime.InputInjector.Release(XEngine.Runtime.KeyCode.__KEY__);
        samples.Enqueue("DONE");
    }
}
RunProbe().Forget();
return "STARTED|__KEY__|" + storageKey;
'''


READ_PRODUCTION_SAMPLES_TEMPLATE = r'''
var samples = System.AppDomain.CurrentDomain.GetData("__STORAGE_KEY__")
    as System.Collections.Concurrent.ConcurrentQueue<string>;
return samples == null ? "MISSING" : string.Join("\n", samples.ToArray());
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
if (yawField == null) return "ERROR|camera YawDeg field missing";
float yawDeg = (float)(yawField.GetValue(rig) ?? 0f);
var hero = controller.GameObject;
var forward = rig.Transform.Forward;
var position = rig.Transform.Position;
var heroPosition = hero.Transform.Position;
var inv = System.Globalization.CultureInfo.InvariantCulture;
return "CAMERA|" + yawDeg.ToString("R", inv) + "|"
    + forward.X.ToString("R", inv) + "|" + forward.Y.ToString("R", inv) + "|"
    + forward.Z.ToString("R", inv) + "|" + position.X.ToString("R", inv) + "|"
    + position.Y.ToString("R", inv) + "|" + position.Z.ToString("R", inv) + "|"
    + heroPosition.X.ToString("R", inv) + "|" + heroPosition.Y.ToString("R", inv) + "|"
    + heroPosition.Z.ToString("R", inv);
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
        if len(fields) != 28:
            raise ValueError(f"unexpected production-frame response: {text!r}")
        frame = int(fields[1])
        fields = ["STATE", *fields[2:]]
    if len(fields) != 27 or fields[0] != "STATE":
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
    }


def _floats(fields: list[str]) -> list[float]:
    return [float(value) for value in fields]


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
    if len(fields) != 11 or fields[0] != "CAMERA":
        raise ValueError(f"unexpected camera response: {text!r}")
    values = _floats(fields[1:])
    return {
        "elapsedSeconds": elapsed,
        "yawDeg": values[0],
        "forward": values[1:4],
        "position": values[4:7],
        "heroPosition": values[7:10],
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


def _sample_production_locomotion(
    client: EditorMcp, key: str, frame_count: int, timeout: float
) -> list[dict[str, Any]]:
    storage_key = f"battle2-controls-{key}-{time.time_ns()}"
    start_code = (
        PRODUCTION_COROUTINE_TEMPLATE.replace("__STORAGE_KEY__", storage_key)
        .replace("__KEY__", key)
        .replace("__FRAME_COUNT__", str(frame_count))
    )
    started_result = client.eval(start_code, timeout=120)
    if not started_result.startswith(f"STARTED|{key}|"):
        raise RuntimeError(f"failed to start production locomotion probe: {started_result!r}")
    read_code = READ_PRODUCTION_SAMPLES_TEMPLATE.replace("__STORAGE_KEY__", storage_key)
    deadline = time.monotonic() + timeout
    last = ""
    while time.monotonic() < deadline:
        last = client.eval(read_code, timeout=120)
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
            return parsed
        time.sleep(0.1)
    # Always clear a possibly held key if the coroutine failed to reach its finally block.
    client.eval(RELEASE_KEY_TEMPLATE.replace("__KEY__", key), timeout=60)
    raise TimeoutError(f"production locomotion probe timed out: {last[-1000:]!r}")


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

    yaw = moving[0]["cameraYawDeg"]
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
    camera_samples: list[dict[str, Any]] = []
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
                "forward": sample["cameraForward"],
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
        "travelBoneWorldDirectionDot": _dot(_normalise_xz(bone_world_delta), expected),
        "travelBoneLocalDelta": bone_local_delta,
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
    if hero_forward_dots[-1] < THRESHOLDS["heroForwardDotMin"]:
        failures.append(
            f"{key}: final production hero forward dot {hero_forward_dots[-1]:.6f} < 0.98"
        )
    if camera["maxYawErrorDegrees"] >= THRESHOLDS["cameraYawErrorDegMaxExclusive"]:
        failures.append(
            f"{key}: production camera yaw error {camera['maxYawErrorDegrees']:.6f}deg is not < 0.5deg"
        )
    if camera["maxYawDriftDegrees"] >= THRESHOLDS["cameraYawDriftDegMaxExclusive"]:
        failures.append(
            f"{key}: production camera yaw drift {camera['maxYawDriftDegrees']:.6f}deg is not < 0.5deg"
        )
    return metrics, failures


def _camera_metrics(samples: list[dict[str, Any]]) -> dict[str, Any]:
    yaw = samples[0]["yawDeg"]
    expected = _expected_direction(yaw, (0.0, 1.0))
    horizontal_forwards = [
        _normalise_xz((sample["forward"][0], sample["forward"][2])) for sample in samples
    ]
    errors = [_angle_deg(forward, expected) for forward in horizontal_forwards]
    baseline = horizontal_forwards[0]
    drifts = [_angle_deg(forward, baseline) for forward in horizontal_forwards]
    return {
        "expectedForwardXZ": list(expected),
        "yawErrorDegrees": errors,
        "yawDriftFromBaselineDegrees": drifts,
        "maxYawErrorDegrees": max(errors, default=180.0),
        "maxYawDriftDegrees": max(drifts, default=180.0),
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
    if hero_forward_dot < THRESHOLDS["heroForwardDotMin"]:
        failures.append(f"{key}: hero forward dot {hero_forward_dot:.6f} < 0.98")
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
    parser.add_argument("--locomotion-frames", type=int, default=24)
    parser.add_argument("--move-iterations", type=int, default=18)
    parser.add_argument("--stress-iterations", type=int, default=12)
    parser.add_argument(
        "--camera-sample-times",
        default="0.02,0.08,0.20,0.50",
        help="comma-separated seconds after lateral target motion",
    )
    args = parser.parse_args()

    project = args.project.resolve()
    editor = args.editor.resolve()
    if not editor.is_file():
        parser.error(f"editor not found: {editor}")
    if not (project / "Assets" / SCENE_PATH).is_file():
        parser.error(f"required scene not found below project: {SCENE_PATH}")
    if args.locomotion_frames <= 0 or args.move_iterations <= 0 or args.stress_iterations <= 0:
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
        "thresholds": THRESHOLDS,
        "probePolicy": {
            "realKeyboardInjection": False,
            "engineInputInjector": True,
            "productionLocomotionTickExercised": True,
            "locomotionFrames": args.locomotion_frames,
            "freshPlaySessionPerDirection": True,
            "expectedProductionHelperSignature": EXPECTED_HELPER_SIGNATURE,
            "fallback": "YawDeg camera forward/right basis, then CombatMotor + CharacterController",
        },
        "directions": {},
        "errors": [],
    }

    client = EditorMcp(editor, project, args.backend, output, skin_diag=False)
    try:
        client.initialize()
        report["initialState"] = wait_until_ready(client, args.ready_timeout)
        # Start persistent log observation before entering Play.
        client.tool("runtime_logs", {"limit": 1}, timeout=60)

        helper_availability: bool | None = None
        for key, input_xy in KEY_INPUTS.items():
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

                production_samples = _sample_production_locomotion(
                    client, key, args.locomotion_frames, args.ready_timeout
                )
                production_metrics, production_failures = _analyse_production_locomotion(
                    key, production_samples
                )
                entry["productionLocomotionSamples"] = production_samples
                entry["productionLocomotionMetrics"] = production_metrics
                report["errors"].extend(production_failures)
                time.sleep(0.1)

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
                failures = [*production_failures, *isolated_failures]
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
        report["passed"] = not report["errors"]
        (output / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    print(str(output))
    print(json.dumps({"passed": report["passed"], "errors": report["errors"]}, ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
