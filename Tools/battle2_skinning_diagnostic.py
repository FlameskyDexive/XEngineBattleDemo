#!/usr/bin/env python3
"""Reproduce and measure Battle2 skinning failures through the live editor MCP server.

The driver is intentionally project-local and path independent.  It launches an editor we own,
opens ZonezeroBattle2, captures an overview plus one close-up per hero, and records the exact CPU
skinning matrices that were uploaded for every SkinnedMeshRenderer.  It never edits the scene.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


HERO_NAMES = ("Battle_Hero", "Battle_Ally_Corin", "Battle_Ally_Nike")

FATAL_LOG_PATTERNS = (
    re.compile(r"\[(?:error|fatal)\]", re.IGNORECASE),
    re.compile(r"\b(?:Unhandled Exception|VUID-[A-Z0-9_-]+)\b", re.IGNORECASE),
)


SAMPLE_CODE = r'''
var names = new[] { "Battle_Hero", "Battle_Ally_Corin", "Battle_Ally_Nike" };
var keyBoneSuffixes = new[] {
    "Bip001 Pelvis", "Bip001 Spine", "Bip001 Spine1", "Bip001 Spine2",
    "Bip001 L Thigh", "Bip001 R Thigh", "Bip001 L Calf", "Bip001 R Calf",
    "Bip001 L UpperArm", "Bip001 R UpperArm", "Bip001 L Forearm", "Bip001 R Forearm",
    "Bip001 L Hand", "Bip001 R Hand", "Bip001 L Foot", "Bip001 R Foot"
};
var includeSkin = true;
var sb = new System.Text.StringBuilder();
var inv = System.Globalization.CultureInfo.InvariantCulture;
var flags = System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.Instance;
var skinField = typeof(XEngine.Runtime.SkinnedMeshRenderer).GetField("_skinMatrices", flags);
var texField = typeof(XEngine.Runtime.SkinnedMeshRenderer).GetField("_boneTexture", flags);
foreach (string rootName in names)
{
    XEngine.Runtime.GameObject? root = null;
    foreach (var candidate in Scene.Current.RootObjects)
        if (candidate.Name == rootName) { root = candidate; break; }
    if (root == null)
    {
        sb.Append("MISSING|").Append(rootName).Append('\n');
        continue;
    }

    var animator = root.GetComponent<XEngine.Runtime.Animator>();
    if (animator != null)
    {
        var state = animator.GetCurrentAnimatorStateInfo();
        sb.Append("ANIM|").Append(rootName).Append('|')
          .Append(animator.CurrentClip != null ? animator.CurrentClip.Name : "<null>").Append('|')
          .Append(state.normalizedTime.ToString("R", inv)).Append('|')
          .Append(state.nameHash).Append('|')
          .Append(animator.GetLayerRuntime(0)?.IsInTransition == true ? "1" : "0").Append('|')
          .Append(root.Transform.Position.X.ToString("R", inv)).Append('|')
          .Append(root.Transform.Position.Y.ToString("R", inv)).Append('|')
          .Append(root.Transform.Position.Z.ToString("R", inv)).Append('\n');

        var clip = animator.CurrentClip;
        var skeleton = animator.Skeleton;
        if (clip != null && skeleton != null)
        {
            int matched = 0, sourceReferences = 0, validSourceReferences = 0;
            int positionTracks = 0, rotationTracks = 0, scaleTracks = 0, partialTracks = 0;
            foreach (var clipBone in clip.Bones)
            {
                if (skeleton.IndexOfBonePath(clipBone.BoneName) >= 0) matched++;
                if (clipBone.HasSourceReferencePose)
                {
                    sourceReferences++;
                    var p = clipBone.SourcePosition;
                    var r = clipBone.SourceRotation;
                    var s = clipBone.SourceScale;
                    float rNorm = r.X * r.X + r.Y * r.Y + r.Z * r.Z + r.W * r.W;
                    if (float.IsFinite(p.X) && float.IsFinite(p.Y) && float.IsFinite(p.Z)
                        && float.IsFinite(r.X) && float.IsFinite(r.Y) && float.IsFinite(r.Z) && float.IsFinite(r.W)
                        && float.IsFinite(s.X) && float.IsFinite(s.Y) && float.IsFinite(s.Z)
                        && rNorm > 1e-8f && System.MathF.Abs(s.X) > 1e-8f
                        && System.MathF.Abs(s.Y) > 1e-8f && System.MathF.Abs(s.Z) > 1e-8f)
                        validSourceReferences++;
                }
                bool anyPos = clipBone.PosX != null || clipBone.PosY != null || clipBone.PosZ != null;
                bool anyRot = clipBone.RotX != null || clipBone.RotY != null || clipBone.RotZ != null || clipBone.RotW != null;
                bool anyScale = clipBone.ScaleX != null || clipBone.ScaleY != null || clipBone.ScaleZ != null;
                bool fullPos = clipBone.PosX?.Keys.Count > 0 && clipBone.PosY?.Keys.Count > 0 && clipBone.PosZ?.Keys.Count > 0;
                bool fullRot = clipBone.RotX?.Keys.Count > 0 && clipBone.RotY?.Keys.Count > 0
                    && clipBone.RotZ?.Keys.Count > 0 && clipBone.RotW?.Keys.Count > 0;
                bool fullScale = clipBone.ScaleX?.Keys.Count > 0 && clipBone.ScaleY?.Keys.Count > 0
                    && clipBone.ScaleZ?.Keys.Count > 0;
                if (fullPos) positionTracks++;
                if (fullRot) rotationTracks++;
                if (fullScale) scaleTracks++;
                if ((anyPos && !fullPos) || (anyRot && !fullRot) || (anyScale && !fullScale)) partialTracks++;
            }
            sb.Append("CLIP|").Append(rootName).Append('|').Append(clip.Name).Append('|')
              .Append(state.nameHash).Append('|').Append(state.normalizedTime.ToString("R", inv)).Append('|')
              .Append(clip.Duration.ToString("R", inv)).Append('|').Append(clip.Bones.Count).Append('|')
              .Append(matched).Append('|').Append(sourceReferences).Append('|').Append(validSourceReferences).Append('|')
              .Append(positionTracks).Append('|').Append(rotationTracks).Append('|').Append(scaleTracks).Append('|')
              .Append(partialTracks).Append('\n');

            var playable = animator.GetState(clip);
            float clipTime = playable != null ? (float)playable.Time : state.normalizedTime * clip.Duration;

            foreach (var clipBone in clip.Bones)
            {
                bool isKeyBone = false;
                foreach (string suffix in keyBoneSuffixes)
                    if (clipBone.BoneName.EndsWith(suffix, System.StringComparison.OrdinalIgnoreCase))
                    {
                        isKeyBone = true;
                        break;
                    }
                if (!isKeyBone) continue;

                int boneIndex = skeleton.IndexOfBonePath(clipBone.BoneName);
                if (boneIndex < 0 || boneIndex >= skeleton.Bones.Length) continue;
                var boneTransform = skeleton.Bones[boneIndex];
                if (boneTransform == null) continue;
                var reference = skeleton.ReferencePose[boneIndex];
                var position = boneTransform.LocalPosition;
                var rotation = boneTransform.LocalRotation;
                var scale = boneTransform.LocalScale;
                var sourceReferencePosition = clipBone.SourcePosition;
                var sourceReferenceRotation = clipBone.SourceRotation;
                var sourceReferenceScale = clipBone.SourceScale;
                var sourceSamplePosition = clipBone.EvaluatePositionAt(clipTime);
                var sourceSampleRotation = clipBone.EvaluateRotationAt(clipTime);
                var sourceSampleScale = clipBone.EvaluateScaleAt(clipTime);
                float localLength = XEngine.Vector.Float3.Length(position);
                float referenceLength = XEngine.Vector.Float3.Length(reference.LocalPosition);
                sb.Append("BONE|").Append(rootName).Append('|').Append(clip.Name).Append('|')
                  .Append(clipBone.BoneName).Append('|')
                  .Append(position.X.ToString("R", inv)).Append('|')
                  .Append(position.Y.ToString("R", inv)).Append('|')
                  .Append(position.Z.ToString("R", inv)).Append('|')
                  .Append(rotation.X.ToString("R", inv)).Append('|')
                  .Append(rotation.Y.ToString("R", inv)).Append('|')
                  .Append(rotation.Z.ToString("R", inv)).Append('|')
                  .Append(rotation.W.ToString("R", inv)).Append('|')
                  .Append(reference.LocalPosition.X.ToString("R", inv)).Append('|')
                  .Append(reference.LocalPosition.Y.ToString("R", inv)).Append('|')
                  .Append(reference.LocalPosition.Z.ToString("R", inv)).Append('|')
                  .Append(reference.LocalRotation.X.ToString("R", inv)).Append('|')
                  .Append(reference.LocalRotation.Y.ToString("R", inv)).Append('|')
                  .Append(reference.LocalRotation.Z.ToString("R", inv)).Append('|')
                  .Append(reference.LocalRotation.W.ToString("R", inv)).Append('|')
                  .Append(scale.X.ToString("R", inv)).Append('|')
                  .Append(scale.Y.ToString("R", inv)).Append('|')
                  .Append(scale.Z.ToString("R", inv)).Append('|')
                  .Append(reference.LocalScale.X.ToString("R", inv)).Append('|')
                  .Append(reference.LocalScale.Y.ToString("R", inv)).Append('|')
                  .Append(reference.LocalScale.Z.ToString("R", inv)).Append('|')
                  .Append(sourceReferenceRotation.X.ToString("R", inv)).Append('|')
                  .Append(sourceReferenceRotation.Y.ToString("R", inv)).Append('|')
                  .Append(sourceReferenceRotation.Z.ToString("R", inv)).Append('|')
                  .Append(sourceReferenceRotation.W.ToString("R", inv)).Append('|')
                  .Append(sourceSampleRotation.X.ToString("R", inv)).Append('|')
                  .Append(sourceSampleRotation.Y.ToString("R", inv)).Append('|')
                  .Append(sourceSampleRotation.Z.ToString("R", inv)).Append('|')
                  .Append(sourceSampleRotation.W.ToString("R", inv)).Append('|')
                  .Append(sourceReferencePosition.X.ToString("R", inv)).Append('|')
                  .Append(sourceReferencePosition.Y.ToString("R", inv)).Append('|')
                  .Append(sourceReferencePosition.Z.ToString("R", inv)).Append('|')
                  .Append(sourceSamplePosition.X.ToString("R", inv)).Append('|')
                  .Append(sourceSamplePosition.Y.ToString("R", inv)).Append('|')
                  .Append(sourceSamplePosition.Z.ToString("R", inv)).Append('|')
                  .Append(sourceReferenceScale.X.ToString("R", inv)).Append('|')
                  .Append(sourceReferenceScale.Y.ToString("R", inv)).Append('|')
                  .Append(sourceReferenceScale.Z.ToString("R", inv)).Append('|')
                  .Append(sourceSampleScale.X.ToString("R", inv)).Append('|')
                  .Append(sourceSampleScale.Y.ToString("R", inv)).Append('|')
                  .Append(sourceSampleScale.Z.ToString("R", inv)).Append('|')
                  .Append(localLength.ToString("R", inv)).Append('|')
                  .Append(referenceLength.ToString("R", inv)).Append('\n');
            }
        }
        else
        {
            sb.Append("CLIP|").Append(rootName).Append("|<null>|0|0|0|0|0|0|0|0|0|0|0\n");
        }
    }
    else
    {
        sb.Append("ANIM|").Append(rootName).Append("|<none>|0|0|0|0|0|0\n");
    }

    if (includeSkin)
    foreach (var smr in root.GetComponentsInChildren<XEngine.Runtime.SkinnedMeshRenderer>(true, true))
    {
        var mesh = smr.SharedMesh.Res;
        var bones = smr.Bones;
        var skin = skinField?.GetValue(smr) as XEngine.Vector.Float4x4[];
        var tex = texField?.GetValue(smr) as XEngine.Runtime.Resources.Texture2D;
        if (mesh == null || bones == null || skin == null)
        {
            sb.Append("SMR|").Append(rootName).Append('|').Append(smr.GameObject.Name)
              .Append("|NOT_READY\n");
            continue;
        }

        var vertices = mesh.Vertices;
        var indices = mesh.BoneIndices;
        var weights = mesh.BoneWeights;
        int count = System.Math.Min(vertices.Length, System.Math.Min(indices.Length, weights.Length));
        int badIndex = 0, badWeight = 0, nonFinite = 0, nullBones = 0;
        for (int b = 0; b < bones.Length; b++) if (bones[b] == null) nullBones++;

        float minX = float.MaxValue, minY = float.MaxValue, minZ = float.MaxValue;
        float maxX = float.MinValue, maxY = float.MinValue, maxZ = float.MinValue;
        float maxMatrixTranslation = 0f;
        var skinnedPositions = new XEngine.Vector.Float3[count];
        for (int b = 0; b < skin.Length; b++)
        {
            var m = skin[b];
            float mt = System.MathF.Sqrt(m.c3.X * m.c3.X + m.c3.Y * m.c3.Y + m.c3.Z * m.c3.Z);
            if (!float.IsFinite(mt)) nonFinite++;
            else if (mt > maxMatrixTranslation) maxMatrixTranslation = mt;
        }

        for (int v = 0; v < count; v++)
        {
            var p = vertices[v];
            var bi = indices[v];
            var bw = weights[v];
            float weightSum = bw.X + bw.Y + bw.Z + bw.W;
            if (!float.IsFinite(weightSum) || System.MathF.Abs(weightSum - 1f) > 0.02f) badWeight++;
            float ax = 0f, ay = 0f, az = 0f;
            for (int k = 0; k < 4; k++)
            {
                float weight = k == 0 ? bw.X : k == 1 ? bw.Y : k == 2 ? bw.Z : bw.W;
                float rawIndex = k == 0 ? bi.X : k == 1 ? bi.Y : k == 2 ? bi.Z : bi.W;
                int oneBased = (int)System.MathF.Round(rawIndex);
                if (weight <= 0f) continue;
                if (oneBased <= 0 || oneBased > skin.Length)
                {
                    badIndex++;
                    continue;
                }
                var m = skin[oneBased - 1];
                ax += (m.c0.X * p.X + m.c1.X * p.Y + m.c2.X * p.Z + m.c3.X) * weight;
                ay += (m.c0.Y * p.X + m.c1.Y * p.Y + m.c2.Y * p.Z + m.c3.Y) * weight;
                az += (m.c0.Z * p.X + m.c1.Z * p.Y + m.c2.Z * p.Z + m.c3.Z) * weight;
            }
            if (!float.IsFinite(ax) || !float.IsFinite(ay) || !float.IsFinite(az))
            {
                nonFinite++;
                continue;
            }
            skinnedPositions[v] = new XEngine.Vector.Float3(ax, ay, az);
            minX = System.MathF.Min(minX, ax); maxX = System.MathF.Max(maxX, ax);
            minY = System.MathF.Min(minY, ay); maxY = System.MathF.Max(maxY, ay);
            minZ = System.MathF.Min(minZ, az); maxZ = System.MathF.Max(maxZ, az);
        }

        float extentX = count > 0 ? maxX - minX : 0f;
        float extentY = count > 0 ? maxY - minY : 0f;
        float extentZ = count > 0 ? maxZ - minZ : 0f;
        float maxTriangleEdge = 0f;
        int edgesOver025m = 0;
        var triangles = mesh.Indices;
        for (int t = 0; t + 2 < triangles.Length; t += 3)
        {
            int ia = (int)triangles[t], ib = (int)triangles[t + 1], ic = (int)triangles[t + 2];
            if ((uint)ia >= (uint)count || (uint)ib >= (uint)count || (uint)ic >= (uint)count)
                continue;
            var a = skinnedPositions[ia]; var b = skinnedPositions[ib]; var c = skinnedPositions[ic];
            float ab = XEngine.Vector.Float3.Length(a - b);
            float bc = XEngine.Vector.Float3.Length(b - c);
            float ca = XEngine.Vector.Float3.Length(c - a);
            maxTriangleEdge = System.MathF.Max(maxTriangleEdge, System.MathF.Max(ab, System.MathF.Max(bc, ca)));
            if (ab > 0.25f) edgesOver025m++;
            if (bc > 0.25f) edgesOver025m++;
            if (ca > 0.25f) edgesOver025m++;
        }
        uint texHandle = tex != null ? (uint)tex.Handle.Handle : 0u;
        sb.Append("SMR|").Append(rootName).Append('|').Append(smr.GameObject.Name).Append('|')
          .Append(mesh.Name).Append('|').Append(smr.InstanceID).Append('|').Append(count).Append('|')
          .Append(bones.Length).Append('|').Append(nullBones).Append('|').Append(badIndex).Append('|')
          .Append(badWeight).Append('|').Append(nonFinite).Append('|')
          .Append(extentX.ToString("R", inv)).Append('|').Append(extentY.ToString("R", inv)).Append('|')
          .Append(extentZ.ToString("R", inv)).Append('|')
          .Append(maxMatrixTranslation.ToString("R", inv)).Append('|').Append(texHandle).Append('|')
          .Append(maxTriangleEdge.ToString("R", inv)).Append('|').Append(edgesOver025m).Append('\n');
    }
}
return sb.ToString();
'''

POSE_SAMPLE_CODE = SAMPLE_CODE.replace("var includeSkin = true;", "var includeSkin = false;")


OVERVIEW_CAMERA_CODE = r'''
var names = new[] { "Battle_Hero", "Battle_Ally_Corin", "Battle_Ally_Nike" };
var center = new XEngine.Vector.Float3(0f, 0f, 0f);
int found = 0;
foreach (string name in names)
    foreach (var root in Scene.Current.RootObjects)
        if (root.Name == name) { center += root.Transform.Position; found++; break; }
if (found == 0) return "no-heroes";
center /= found;
XEngine.Runtime.Camera? camera = null;
foreach (var root in Scene.Current.RootObjects)
{
    var candidate = root.GetComponentInChildren<XEngine.Runtime.Camera>(true, true);
    if (candidate != null) { camera = candidate; if (root.Name.Contains("CameraRig")) break; }
}
if (camera == null) return "no-camera";
foreach (var root in Scene.Current.RootObjects)
    foreach (var behaviour in root.GetComponentsInChildren<XEngine.Runtime.MonoBehaviour>(true, true))
        if (behaviour.GetType().Name == "BattleFollowCamera") behaviour.Enabled = false;
camera.Transform.Position = center + new XEngine.Vector.Float3(0f, 8.5f, -9.5f);
camera.Transform.LookAt(center + new XEngine.Vector.Float3(0f, 0.9f, 0f));
return "overview:" + center.ToString();
'''


CLOSEUP_CAMERA_TEMPLATE = r'''
XEngine.Runtime.GameObject? hero = null;
foreach (var root in Scene.Current.RootObjects)
    if (root.Name == "__HERO__") { hero = root; break; }
if (hero == null) return "no-hero";
XEngine.Runtime.Camera? camera = null;
foreach (var root in Scene.Current.RootObjects)
{
    var candidate = root.GetComponentInChildren<XEngine.Runtime.Camera>(true, true);
    if (candidate != null) { camera = candidate; if (root.Name.Contains("CameraRig")) break; }
}
if (camera == null) return "no-camera";
var target = hero.Transform.Position + new XEngine.Vector.Float3(0f, 0.95f, 0f);
camera.Transform.Position = target + new XEngine.Vector.Float3(0f, 0.35f, -3.2f);
camera.Transform.LookAt(target);
return "closeup:" + hero.Transform.Position.ToString();
'''


HERO_IDENTIFIER_TEMPLATE = r'''
foreach (var root in Scene.Current.RootObjects)
    if (root.Name == "__HERO__") return root.Identifier.ToString();
return "";
'''


DISABLE_HERO_GAMEPLAY_CODE = r'''
var names = new[] { "Battle_Hero", "Battle_Ally_Corin", "Battle_Ally_Nike" };
int disabled = 0;
foreach (string name in names)
    foreach (var root in Scene.Current.RootObjects)
        if (root.Name == name)
        {
            foreach (var behaviour in root.GetComponents<XEngine.Runtime.MonoBehaviour>())
                if (behaviour is not XEngine.Runtime.Animator && behaviour.Enabled)
                {
                    behaviour.Enabled = false;
                    disabled++;
                }
            break;
        }
return "disabled:" + disabled;
'''


PLAY_ALL_STATES_TEMPLATE = r'''
var names = new[] { "Battle_Hero", "Battle_Ally_Corin", "Battle_Ally_Nike" };
var stateNames = new[] { __STATE_NAMES__ };
var fadeDurations = new[] { __FADE_DURATIONS__ };
var sb = new System.Text.StringBuilder();
var inv = System.Globalization.CultureInfo.InvariantCulture;
XEngine.Runtime.Application.IsPaused = false;
foreach (string rootName in names)
{
    XEngine.Runtime.GameObject? root = null;
    foreach (var candidate in Scene.Current.RootObjects)
        if (candidate.Name == rootName) { root = candidate; break; }
    var animator = root?.GetComponent<XEngine.Runtime.Animator>();
    if (animator == null)
    {
        sb.Append("PLAY|").Append(rootName).Append("|NO_ANIMATOR|0|0|0\n");
        continue;
    }
    for (int i = 0; i < stateNames.Length; i++)
    {
        if (fadeDurations[i] > 0f)
            animator.CrossFade(stateNames[i], fadeDurations[i], 0);
        else
            animator.Play(stateNames[i], 0);
    }
    var state = animator.GetCurrentAnimatorStateInfo();
    sb.Append("PLAY|").Append(rootName).Append('|')
      .Append(animator.CurrentClip != null ? animator.CurrentClip.Name : "<null>").Append('|')
      .Append(state.nameHash).Append('|')
      .Append(state.normalizedTime.ToString("R", inv)).Append('|')
      .Append(animator.GetLayerRuntime(0)?.IsInTransition == true ? "1" : "0").Append('\n');
}
return sb.ToString();
'''


WAIT_AND_PAUSE_PHASE_TEMPLATE = r'''
var names = new[] { "Battle_Hero", "Battle_Ally_Corin", "Battle_Ally_Nike" };
float targetNormalizedTime = __NORMALIZED_TIME__f;
int expectedHash = __STATE_HASH__;
int ready = 0;
var sb = new System.Text.StringBuilder();
var inv = System.Globalization.CultureInfo.InvariantCulture;
foreach (string rootName in names)
{
    XEngine.Runtime.GameObject? root = null;
    foreach (var candidate in Scene.Current.RootObjects)
        if (candidate.Name == rootName) { root = candidate; break; }
    var animator = root?.GetComponent<XEngine.Runtime.Animator>();
    if (animator == null)
    {
        sb.Append("PHASE|").Append(rootName).Append("|NO_ANIMATOR|0|0|0\n");
        continue;
    }
    var state = animator.GetCurrentAnimatorStateInfo();
    bool atTarget = animator.CurrentClip != null
        && state.nameHash == expectedHash
        && state.normalizedTime >= targetNormalizedTime;
    if (atTarget)
    {
        animator.Pause();
        ready++;
    }
    sb.Append("PHASE|").Append(rootName).Append('|')
      .Append(animator.CurrentClip != null ? animator.CurrentClip.Name : "<null>").Append('|')
      .Append(state.nameHash).Append('|')
      .Append(state.normalizedTime.ToString("R", inv)).Append('|')
      .Append(atTarget ? "1" : "0").Append('\n');
}
if (ready == names.Length)
    XEngine.Runtime.Application.IsPaused = true;
sb.Append("SUMMARY|").Append(ready).Append('|').Append(names.Length).Append('\n');
return sb.ToString();
'''


PAUSE_GAME_CODE = r'''
XEngine.Runtime.Application.IsPaused = true;
return "paused";
'''


RESUME_GAME_CODE = r'''
XEngine.Runtime.Application.IsPaused = false;
return "resumed";
'''


TOGGLE_HERO_ROOT_TEMPLATE = r'''
XEngine.Runtime.Application.IsPaused = true;
XEngine.Runtime.GameObject? hero = null;
foreach (var root in Scene.Current.RootObjects)
    if (root.Name == "__HERO__") { hero = root; break; }
if (hero == null) return "missing:__HERO__";
hero.Enabled = false;
hero.Enabled = true;
XEngine.Runtime.Application.IsPaused = false;
return "reenabled:__HERO__";
'''


STATE_AVAILABLE_TEMPLATE = r'''
var names = new[] { "Battle_Hero", "Battle_Ally_Corin", "Battle_Ally_Nike" };
var stateName = "__STATE__";
int ready = 0;
var sb = new System.Text.StringBuilder();
XEngine.Runtime.Application.IsPaused = false;
foreach (string rootName in names)
{
    XEngine.Runtime.GameObject? root = null;
    foreach (var candidate in Scene.Current.RootObjects)
        if (candidate.Name == rootName) { root = candidate; break; }
    var animator = root?.GetComponent<XEngine.Runtime.Animator>();
    bool available = animator != null && animator.HasState(stateName);
    if (available) ready++;
    sb.Append("AVAILABLE|").Append(rootName).Append('|').Append(available ? "1" : "0").Append('\n');
}
sb.Append("SUMMARY|").Append(ready).Append('|').Append(names.Length).Append('\n');
return sb.ToString();
'''


@dataclass
class RpcResponse:
    raw: dict[str, Any]

    @property
    def text(self) -> str:
        result = self.raw.get("result") or {}
        chunks = result.get("content") or []
        return "".join(str(chunk.get("text") or "") for chunk in chunks if isinstance(chunk, dict))

    @property
    def structured(self) -> dict[str, Any]:
        result = self.raw.get("result") or {}
        value = result.get("structuredContent")
        return value if isinstance(value, dict) else {}

    @property
    def is_error(self) -> bool:
        result = self.raw.get("result") or {}
        return result.get("isError") is True


def unwrap_json_envelope(value: str) -> Any:
    """Unwrap the JSON text envelope emitted by runtime_eval/runtime_logs."""
    current: Any = value
    for _ in range(2):
        if not isinstance(current, str):
            break
        try:
            current = json.loads(current)
        except json.JSONDecodeError:
            break
        if isinstance(current, dict) and current.get("succeeded") is True and "result" in current:
            current = current["result"]
    return current


class EditorMcp:
    def __init__(self, editor: Path, project: Path, backend: str, output: Path, skin_diag: bool):
        env = os.environ.copy()
        if skin_diag:
            env["XENGINE_SKIN_DIAG"] = "1"
        args = [str(editor), "--serve", "--project", str(project), f"--graphics={backend}"]
        self._log = (output / "editor-stdio.log").open("wb")
        self._process = subprocess.Popen(
            args,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=str(editor.parent),
            env=env,
        )
        self._responses: dict[int, dict[str, Any]] = {}
        self._lock = threading.Lock()
        self._next_id = 0
        self._reader = threading.Thread(target=self._drain, daemon=True)
        self._reader.start()

    def _drain(self) -> None:
        assert self._process.stdout is not None
        for raw in self._process.stdout:
            self._log.write(raw)
            self._log.flush()
            try:
                message = json.loads(raw.decode("utf-8", errors="replace"))
            except json.JSONDecodeError:
                continue
            response_id = message.get("id")
            if isinstance(response_id, int):
                with self._lock:
                    self._responses[response_id] = message

    def _send(self, method: str, params: dict[str, Any] | None = None, notification: bool = False) -> int:
        assert self._process.stdin is not None
        message: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = params
        request_id = 0
        if not notification:
            self._next_id += 1
            request_id = self._next_id
            message["id"] = request_id
        self._process.stdin.write(json.dumps(message).encode("utf-8") + b"\n")
        self._process.stdin.flush()
        return request_id

    def request(self, method: str, params: dict[str, Any], timeout: float = 300.0) -> RpcResponse:
        request_id = self._send(method, params)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._process.poll() is not None:
                raise RuntimeError(f"editor exited with code {self._process.returncode}")
            with self._lock:
                response = self._responses.pop(request_id, None)
            if response is not None:
                if "error" in response:
                    raise RuntimeError(json.dumps(response["error"], ensure_ascii=False))
                return RpcResponse(response)
            time.sleep(0.05)
        raise TimeoutError(f"MCP request timed out: {method}")

    def initialize(self) -> None:
        self.request(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "battle2-skinning-diagnostic", "version": "1.0"},
            },
            timeout=300,
        )
        self._send("notifications/initialized", notification=True)

    def tool(self, name: str, arguments: dict[str, Any] | None = None, timeout: float = 300.0) -> RpcResponse:
        return self.request(
            "tools/call",
            {"name": name, "arguments": arguments or {}},
            timeout=timeout,
        )

    def eval(self, code: str, timeout: float = 300.0) -> str:
        response = self.tool("runtime_eval", {"code": code}, timeout=timeout)
        if response.is_error:
            raise RuntimeError(f"runtime_eval tool failure: {response.text or response.raw}")
        value: Any = response.structured or unwrap_json_envelope(response.text)
        if isinstance(value, dict) and value.get("succeeded") is False:
            raise RuntimeError(f"runtime_eval failed: {value.get('error') or value}")
        if isinstance(value, dict) and value.get("succeeded") is True and "result" in value:
            value = value["result"]
        return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)

    def close(self) -> None:
        if self._process.poll() is None:
            try:
                self.tool("runtime_playmode", {"action": "exit"}, timeout=30)
            except Exception:
                pass
            try:
                assert self._process.stdin is not None
                self._process.stdin.close()
                self._process.wait(timeout=20)
            except Exception:
                self._process.terminate()
                try:
                    self._process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    self._process.kill()
        self._log.close()


def wait_until_ready(client: EditorMcp, timeout: float) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last_error = ""
    while time.monotonic() < deadline:
        try:
            state = client.tool("runtime_state", timeout=20)
            if state.structured:
                return state.structured
            if state.text:
                return {"text": state.text}
        except Exception as exc:  # editor may still be importing/compiling
            last_error = str(exc)
        time.sleep(1)
    raise TimeoutError(f"editor did not become ready: {last_error}")


def capture(client: EditorMcp, output: Path, name: str) -> str:
    response = client.tool("runtime_screenshot", timeout=240)
    path_value = response.structured.get("path")
    if not isinstance(path_value, str):
        try:
            decoded = json.loads(response.text)
            path_value = decoded.get("path") if isinstance(decoded, dict) else None
        except json.JSONDecodeError:
            path_value = None
    if not isinstance(path_value, str):
        raise RuntimeError(f"runtime_screenshot returned no path: {response.raw}")
    source = Path(path_value)
    destination = output / name
    shutil.copy2(source, destination)
    return destination.name


def parse_metrics(
    text: str,
    sample_time: float,
    phase: str | None = None,
    expected_state: str | None = None,
    expected_transition: bool | None = None,
) -> list[dict[str, Any]]:
    parsed: list[dict[str, Any]] = []
    for line in text.splitlines():
        fields = line.split("|")
        if not fields:
            continue
        if fields[0] == "ANIM" and len(fields) == 9:
            parsed.append(
                {
                    "kind": "animator",
                    "time": sample_time,
                    "root": fields[1],
                    "clip": fields[2],
                    "normalizedTime": float(fields[3]),
                    "stateHash": int(fields[4]),
                    "inTransition": fields[5] == "1",
                    "position": [float(fields[6]), float(fields[7]), float(fields[8])],
                }
            )
        elif fields[0] == "CLIP" and len(fields) == 14:
            parsed.append(
                {
                    "kind": "clip",
                    "time": sample_time,
                    "root": fields[1],
                    "clip": fields[2],
                    "stateHash": int(fields[3]),
                    "normalizedTime": float(fields[4]),
                    "duration": float(fields[5]),
                    "boneCount": int(fields[6]),
                    "matchedBones": int(fields[7]),
                    "sourceReferenceBones": int(fields[8]),
                    "validSourceReferenceBones": int(fields[9]),
                    "positionTracks": int(fields[10]),
                    "rotationTracks": int(fields[11]),
                    "scaleTracks": int(fields[12]),
                    "partialTracks": int(fields[13]),
                }
            )
        elif fields[0] == "BONE" and len(fields) == 46:
            parsed.append(
                {
                    "kind": "bone",
                    "time": sample_time,
                    "root": fields[1],
                    "clip": fields[2],
                    "path": fields[3],
                    "localPosition": [float(fields[4]), float(fields[5]), float(fields[6])],
                    "localRotation": [float(fields[7]), float(fields[8]), float(fields[9]), float(fields[10])],
                    "referencePosition": [float(fields[11]), float(fields[12]), float(fields[13])],
                    "referenceRotation": [float(fields[14]), float(fields[15]), float(fields[16]), float(fields[17])],
                    "localScale": [float(fields[18]), float(fields[19]), float(fields[20])],
                    "referenceScale": [float(fields[21]), float(fields[22]), float(fields[23])],
                    "sourceReferenceRotation": [float(fields[24]), float(fields[25]), float(fields[26]), float(fields[27])],
                    "sourceSampleRotation": [float(fields[28]), float(fields[29]), float(fields[30]), float(fields[31])],
                    "sourceReferencePosition": [float(fields[32]), float(fields[33]), float(fields[34])],
                    "sourceSamplePosition": [float(fields[35]), float(fields[36]), float(fields[37])],
                    "sourceReferenceScale": [float(fields[38]), float(fields[39]), float(fields[40])],
                    "sourceSampleScale": [float(fields[41]), float(fields[42]), float(fields[43])],
                    "localLength": float(fields[44]),
                    "referenceLength": float(fields[45]),
                }
            )
        elif fields[0] == "SMR" and len(fields) >= 16 and fields[3] != "NOT_READY":
            parsed.append(
                {
                    "kind": "smr",
                    "time": sample_time,
                    "root": fields[1],
                    "renderer": fields[2],
                    "mesh": fields[3],
                    "rendererId": int(fields[4]),
                    "vertexCount": int(fields[5]),
                    "boneCount": int(fields[6]),
                    "nullBones": int(fields[7]),
                    "badIndices": int(fields[8]),
                    "badWeights": int(fields[9]),
                    "nonFinite": int(fields[10]),
                    "extent": [float(fields[11]), float(fields[12]), float(fields[13])],
                    "maxMatrixTranslation": float(fields[14]),
                    "boneTextureHandle": int(fields[15]),
                    "maxTriangleEdge": float(fields[16]) if len(fields) > 16 else None,
                    "edgesOver025m": int(fields[17]) if len(fields) > 17 else None,
                }
            )
        else:
            parsed.append({"kind": "raw", "time": sample_time, "line": line})
    if phase is not None:
        for metric in parsed:
            metric["phase"] = phase
    if expected_state is not None:
        for metric in parsed:
            metric["expectedState"] = expected_state
    if expected_transition is not None:
        for metric in parsed:
            metric["expectedTransition"] = expected_transition
    return parsed


def response_value(response: RpcResponse) -> Any:
    if response.structured:
        return response.structured
    return unwrap_json_envelope(response.text)


def animation_name_hash(name: str) -> int:
    """Match AnimationNameHash.Hash (FNV-1a over .NET UTF-16 code units)."""
    if not name:
        return 0
    value = 2166136261
    encoded = name.encode("utf-16-le")
    for index in range(0, len(encoded), 2):
        value ^= encoded[index] | (encoded[index + 1] << 8)
        value = (value * 16777619) & 0xFFFFFFFF
    return value if value < 0x80000000 else value - 0x100000000


def quaternion_angle_degrees(left: list[float], right: list[float]) -> float:
    """Shortest-arc quaternion angle; input order is x,y,z,w."""
    dot = abs(sum(a * b for a, b in zip(left, right)))
    left_length = math.sqrt(sum(value * value for value in left))
    right_length = math.sqrt(sum(value * value for value in right))
    if left_length <= 1e-8 or right_length <= 1e-8:
        return 180.0
    cosine = max(-1.0, min(1.0, dot / (left_length * right_length)))
    return math.degrees(2.0 * math.acos(cosine))


def vector_delta_length(left: list[float], right: list[float]) -> float:
    return math.sqrt(sum((a - b) * (a - b) for a, b in zip(left, right)))


def scale_ratios(sample: list[float], reference: list[float]) -> list[float] | None:
    if any(abs(value) <= 1e-8 for value in reference):
        return None
    return [value / base for value, base in zip(sample, reference)]


def validate_animation_semantics(report: dict[str, Any]) -> None:
    """Fail clips that are loaded but not meaningfully driving a complete, stable skeleton."""
    emitted: set[str] = set()

    def fail(message: str) -> None:
        if message not in emitted:
            emitted.add(message)
            report["errors"].append(message)

    metrics = report["metrics"]
    clip_samples = [metric for metric in metrics if metric.get("kind") == "clip"]
    unavailable_clips: dict[str, int] = {name: 0 for name in HERO_NAMES}
    loaded_clips: dict[str, int] = {name: 0 for name in HERO_NAMES}
    checked_clips: set[tuple[str, str, int]] = set()
    for metric in clip_samples:
        root = metric["root"]
        if metric["clip"] in {"<null>", "<none>"}:
            if root in unavailable_clips and metric.get("phase") != "edit-baseline":
                unavailable_clips[root] += 1
            continue
        if root in loaded_clips:
            loaded_clips[root] += 1
        identity = (root, metric["clip"], metric["stateHash"])
        if identity in checked_clips:
            continue
        checked_clips.add(identity)
        bone_count = metric["boneCount"]
        if bone_count <= 0:
            fail(
                f"{metric['root']} has no clip bones during {metric.get('phase', metric['time'])}"
            )
            continue
        if metric["matchedBones"] != bone_count:
            fail(
                f"{metric['root']}/{metric['clip']} resolves {metric['matchedBones']}/{bone_count} bone paths "
                f"during {metric.get('phase', metric['time'])}"
            )
        if metric["sourceReferenceBones"] != bone_count:
            fail(
                f"{metric['root']}/{metric['clip']} carries source reference TRS for "
                f"{metric['sourceReferenceBones']}/{bone_count} bones"
            )
        if metric["validSourceReferenceBones"] != bone_count:
            fail(
                f"{metric['root']}/{metric['clip']} has valid source reference TRS for "
                f"{metric['validSourceReferenceBones']}/{bone_count} bones"
            )
        for channel, field in (
            ("position", "positionTracks"),
            ("rotation", "rotationTracks"),
            ("scale", "scaleTracks"),
        ):
            if metric[field] != bone_count:
                fail(
                    f"{metric['root']}/{metric['clip']} has complete {channel} tracks for "
                    f"{metric[field]}/{bone_count} bones"
                )
        if metric["partialTracks"]:
            fail(
                f"{metric['root']}/{metric['clip']} has {metric['partialTracks']} bones with partial TRS tracks"
            )

    expected_animators = [
        metric
        for metric in metrics
        if metric.get("kind") == "animator"
        and metric.get("expectedState")
        and metric.get("expectedTransition") is not None
    ]
    for metric in expected_animators:
        expected_state = metric["expectedState"]
        expected_hash = animation_name_hash(expected_state)
        state_kind = "transition" if metric["expectedTransition"] else "direct"
        if metric["stateHash"] != expected_hash:
            fail(
                f"{metric['root']} {state_kind} {expected_state} sample has state hash "
                f"{metric['stateHash']}, expected {expected_hash} during {metric.get('phase', metric['time'])}"
            )
        if metric["inTransition"] != metric["expectedTransition"]:
            fail(
                f"{metric['root']} {state_kind} {expected_state} transition flag is "
                f"{metric['inTransition']} during {metric.get('phase', metric['time'])}"
            )

    baseline_positions: dict[str, list[float]] = {}
    animator_samples = [metric for metric in metrics if metric.get("kind") == "animator"]
    for metric in animator_samples:
        if metric.get("phase") == "edit-baseline" and metric["root"] in HERO_NAMES:
            baseline_positions[metric["root"]] = metric["position"]
    for root in HERO_NAMES:
        if root not in baseline_positions:
            first = next((metric for metric in animator_samples if metric["root"] == root), None)
            if first is not None:
                baseline_positions[root] = first["position"]

    static_pose_sliding: dict[str, list[dict[str, Any]]] = {name: [] for name in HERO_NAMES}
    for metric in animator_samples:
        root = metric["root"]
        if (
            root not in baseline_positions
            or metric.get("phase") == "edit-baseline"
            or metric["clip"] not in {"<null>", "<none>"}
        ):
            continue
        baseline = baseline_positions[root]
        dx = metric["position"][0] - baseline[0]
        dz = metric["position"][2] - baseline[2]
        horizontal_distance = math.hypot(dx, dz)
        if horizontal_distance > 0.05:
            occurrence = {
                "phase": metric.get("phase", metric["time"]),
                "distance": horizontal_distance,
                "position": metric["position"],
                "baseline": baseline,
            }
            static_pose_sliding[root].append(occurrence)
            fail(
                f"{root} static-pose sliding: CurrentClip is null while horizontal displacement is "
                f"{horizontal_distance:.3f}m during {occurrence['phase']}"
            )

    comparison_summary: dict[tuple[str, str], dict[str, Any]] = {}
    for metric in metrics:
        if (
            metric.get("kind") != "bone"
            or not metric.get("expectedState")
            or metric.get("expectedTransition") is not False
        ):
            continue
        source_rotation = quaternion_angle_degrees(
            metric["sourceReferenceRotation"], metric["sourceSampleRotation"]
        )
        target_rotation = quaternion_angle_degrees(
            metric["referenceRotation"], metric["localRotation"]
        )
        rotation_error = abs(target_rotation - source_rotation)
        rotation_limit = max(2.0, source_rotation * 0.15)

        source_position = vector_delta_length(
            metric["sourceSamplePosition"], metric["sourceReferencePosition"]
        )
        target_position = vector_delta_length(
            metric["localPosition"], metric["referencePosition"]
        )
        position_error = abs(target_position - source_position)
        position_limit = max(0.005, source_position * 0.25)

        source_scale = scale_ratios(
            metric["sourceSampleScale"], metric["sourceReferenceScale"]
        )
        target_scale = scale_ratios(metric["localScale"], metric["referenceScale"])
        scale_error = None
        scale_limit = None
        if source_scale is not None and target_scale is not None:
            scale_error = max(abs(a - b) for a, b in zip(source_scale, target_scale))
            scale_limit = max(0.05, max(abs(value - 1.0) for value in source_scale) * 0.15)

        metric["retargetDelta"] = {
            "sourceRotationDegrees": source_rotation,
            "targetRotationDegrees": target_rotation,
            "rotationErrorDegrees": rotation_error,
            "sourcePositionDelta": source_position,
            "targetPositionDelta": target_position,
            "positionError": position_error,
            "sourceScaleRatio": source_scale,
            "targetScaleRatio": target_scale,
            "scaleRatioError": scale_error,
        }
        key = (metric["root"], metric["expectedState"])
        summary = comparison_summary.setdefault(
            key,
            {
                "sampleCount": 0,
                "rotation": (0.0, 0.0, ""),
                "position": (0.0, 0.0, ""),
                "scale": (0.0, 0.0, ""),
            },
        )
        summary["sampleCount"] += 1
        if rotation_error / rotation_limit > summary["rotation"][0]:
            summary["rotation"] = (rotation_error / rotation_limit, rotation_error, metric["path"])
        if position_error / position_limit > summary["position"][0]:
            summary["position"] = (position_error / position_limit, position_error, metric["path"])
        if scale_error is not None and scale_limit is not None and scale_error / scale_limit > summary["scale"][0]:
            summary["scale"] = (scale_error / scale_limit, scale_error, metric["path"])

    for (root, state), summary in comparison_summary.items():
        for channel, units in (("rotation", "deg"), ("position", "m"), ("scale", "ratio")):
            normalized_error, absolute_error, path = summary[channel]
            if normalized_error > 1.0:
                fail(
                    f"{root}/{state} retarget {channel} delta diverges from the source reference motion "
                    f"at {path}: error {absolute_error:.4f}{units}"
                )

    run_bones: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for metric in metrics:
        if (
            metric.get("kind") != "bone"
            or metric.get("expectedState") != "Run"
            or metric.get("expectedTransition") is not False
            or not str(metric.get("phase", "")).startswith("run-sample-")
        ):
            continue
        run_bones.setdefault((metric["root"], metric["path"]), []).append(metric)

    limb_suffixes = {
        "leftLeg": ("bip001 l thigh", "bip001 l calf", "bip001 l foot"),
        "rightLeg": ("bip001 r thigh", "bip001 r calf", "bip001 r foot"),
        "leftArm": ("bip001 l upperarm", "bip001 l forearm", "bip001 l hand"),
        "rightArm": ("bip001 r upperarm", "bip001 r forearm", "bip001 r hand"),
    }
    limb_motion: dict[str, dict[str, dict[str, Any]]] = {
        root: {
            limb: {"sampledBones": [], "movingBones": [], "maxRotationDegrees": 0.0}
            for limb in limb_suffixes
        }
        for root in HERO_NAMES
    }
    for (root, path), samples in run_bones.items():
        if len(samples) < 2:
            continue
        max_rotation_delta = max(
            quaternion_angle_degrees(samples[i]["localRotation"], samples[j]["localRotation"])
            for i in range(len(samples))
            for j in range(i + 1, len(samples))
        )
        lowered_path = path.lower()
        for limb, suffixes in limb_suffixes.items():
            if any(lowered_path.endswith(suffix) for suffix in suffixes):
                details = limb_motion[root][limb]
                details["sampledBones"].append(path)
                details["maxRotationDegrees"] = max(
                    details["maxRotationDegrees"], max_rotation_delta
                )
                if max_rotation_delta >= 2.0:
                    details["movingBones"].append(path)
                break

    if report.get("stateMatrixRequested"):
        for root in HERO_NAMES:
            for limb, details in limb_motion[root].items():
                if len(details["sampledBones"]) < 2:
                    fail(
                        f"{root} Run exposed only {len(details['sampledBones'])} sampled bones for {limb}"
                    )
                elif not details["movingBones"]:
                    fail(
                        f"{root} Run {limb} stayed static across fixed pose samples "
                        f"(max {details['maxRotationDegrees']:.3f}deg); probable T-pose sliding"
                    )

    report["animationSemantics"] = {
        "clips": {
            root: {
                "loadedSamples": loaded_clips[root],
                "streamingWaitSamples": unavailable_clips[root],
            }
            for root in HERO_NAMES
        },
        "staticPoseSliding": static_pose_sliding,
        "retargetComparisons": {
            f"{root}/{state}": {
                "sampleCount": summary["sampleCount"],
                "worstRotationErrorDegrees": summary["rotation"][1],
                "worstPositionError": summary["position"][1],
                "worstScaleRatioError": summary["scale"][1],
            }
            for (root, state), summary in comparison_summary.items()
        },
        "runLimbMotion": limb_motion,
    }


def build_play_all_states_code(sequence: tuple[tuple[str, float], ...]) -> str:
    state_names = ", ".join(json.dumps(name) for name, _ in sequence)
    fade_durations = ", ".join(f"{duration:.9g}f" for _, duration in sequence)
    return (
        PLAY_ALL_STATES_TEMPLATE.replace("__STATE_NAMES__", state_names)
        .replace("__FADE_DURATIONS__", fade_durations)
    )


def wait_until_normalized_phase(
    client: EditorMcp,
    state_name: str,
    normalized_time: float,
    timeout: float = 15.0,
) -> str:
    code = (
        WAIT_AND_PAUSE_PHASE_TEMPLATE.replace("__NORMALIZED_TIME__", f"{normalized_time:.9g}")
        .replace("__STATE_HASH__", str(animation_name_hash(state_name)))
    )
    deadline = time.monotonic() + timeout
    last = ""
    while time.monotonic() < deadline:
        last = client.eval(code, timeout=120)
        summary = next(
            (line.split("|") for line in last.splitlines() if line.startswith("SUMMARY|")),
            None,
        )
        if summary is not None and len(summary) == 3 and summary[1] == summary[2]:
            return last
        time.sleep(0.01)
    client.eval(PAUSE_GAME_CODE)
    raise TimeoutError(
        f"heroes did not reach {state_name} normalized phase {normalized_time:.3f}: {last}"
    )


def wait_until_state_available(
    client: EditorMcp,
    state_name: str,
    timeout: float = 15.0,
) -> str:
    code = STATE_AVAILABLE_TEMPLATE.replace("__STATE__", state_name)
    deadline = time.monotonic() + timeout
    last = ""
    while time.monotonic() < deadline:
        last = client.eval(code, timeout=120)
        summary = next(
            (line.split("|") for line in last.splitlines() if line.startswith("SUMMARY|")),
            None,
        )
        if summary is not None and len(summary) == 3 and summary[1] == summary[2]:
            return last
        time.sleep(0.02)
    client.eval(PAUSE_GAME_CODE)
    raise TimeoutError(f"heroes did not recover state {state_name} after re-enable: {last}")


def animator_snapshot(metrics: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {
        metric["root"]: {
            "clip": metric["clip"],
            "stateHash": metric["stateHash"],
            "normalizedTime": metric["normalizedTime"],
            "inTransition": metric["inTransition"],
            "position": metric["position"],
        }
        for metric in metrics
        if metric.get("kind") == "animator"
    }


def key_bone_snapshot(
    metrics: list[dict[str, Any]], root: str
) -> dict[str, dict[str, list[float]]]:
    return {
        metric["path"]: {
            "localPosition": metric["localPosition"],
            "localRotation": metric["localRotation"],
            "localScale": metric["localScale"],
            "referencePosition": metric["referencePosition"],
            "referenceRotation": metric["referenceRotation"],
            "referenceScale": metric["referenceScale"],
        }
        for metric in metrics
        if metric.get("kind") == "bone" and metric.get("root") == root
    }


def compare_reference_pose(
    before: dict[str, dict[str, list[float]]],
    after: dict[str, dict[str, list[float]]],
) -> dict[str, Any]:
    paths = sorted(set(before) | set(after))
    missing_before = [path for path in paths if path not in before]
    missing_after = [path for path in paths if path not in after]
    deltas: list[dict[str, Any]] = []
    for path in paths:
        if path not in before or path not in after:
            continue
        old = before[path]
        new = after[path]
        position_delta = vector_delta_length(
            old["referencePosition"], new["referencePosition"]
        )
        rotation_delta = quaternion_angle_degrees(
            old["referenceRotation"], new["referenceRotation"]
        )
        scale_delta = max(
            abs(a - b) for a, b in zip(old["referenceScale"], new["referenceScale"])
        )
        deltas.append(
            {
                "path": path,
                "positionDelta": position_delta,
                "rotationDeltaDegrees": rotation_delta,
                "scaleDelta": scale_delta,
                "drifted": position_delta > 1e-4
                or rotation_delta > 0.05
                or scale_delta > 1e-4,
            }
        )
    return {
        "beforeBoneCount": len(before),
        "afterBoneCount": len(after),
        "missingBefore": missing_before,
        "missingAfter": missing_after,
        "animatedBeforeBones": sum(
            quaternion_angle_degrees(
                pose["referenceRotation"], pose["localRotation"]
            )
            >= 2.0
            for pose in before.values()
        ),
        "driftedBones": [delta for delta in deltas if delta["drifted"]],
        "maxPositionDelta": max((delta["positionDelta"] for delta in deltas), default=0.0),
        "maxRotationDeltaDegrees": max(
            (delta["rotationDeltaDegrees"] for delta in deltas), default=0.0
        ),
        "maxScaleDelta": max((delta["scaleDelta"] for delta in deltas), default=0.0),
    }


def runtime_log_entries(response: RpcResponse) -> list[dict[str, Any]]:
    value: Any = response.structured
    if not value:
        value = unwrap_json_envelope(response.text)
    if isinstance(value, dict) and isinstance(value.get("logs"), list):
        return [entry for entry in value["logs"] if isinstance(entry, dict)]
    return []


def fatal_runtime_logs(entries: list[dict[str, Any]]) -> list[str]:
    failures: list[str] = []
    for entry in entries:
        severity = str(entry.get("severity") or "")
        message = str(entry.get("message") or "")
        if severity.lower() in {"error", "fatal", "exception"} or any(
            pattern.search(message) for pattern in FATAL_LOG_PATTERNS
        ):
            failures.append(message)
    return failures


def git_sha(path: Path) -> str:
    top = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "--show-toplevel"],
        check=False,
        capture_output=True,
        text=True,
    )
    if top.returncode != 0 or Path(top.stdout.strip()).resolve() != path.resolve():
        return "<not-a-git-worktree>"
    result = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() if result.returncode == 0 else "<not-a-git-worktree>"


def main() -> int:
    script = Path(__file__).resolve()
    project = script.parents[1]
    engine = project.parent
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, default=project)
    parser.add_argument("--editor", type=Path, default=engine / "Build/Editor/Release/net10.0/XEngine.Editor.exe")
    parser.add_argument("--backend", choices=("opengl", "vulkan", "d3d12"), default="vulkan")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--times", default="0.5,2,5,10", help="comma-separated seconds after Play")
    parser.add_argument("--ready-timeout", type=float, default=180.0)
    parser.add_argument("--skin-diag", action="store_true")
    parser.add_argument(
        "--state-matrix",
        action="store_true",
        help=(
            "exercise deterministic Idle, Run, Attack_Normal_1 poses, cross-fades, and the "
            "root disable/re-enable lifecycle"
        ),
    )
    parser.add_argument(
        "--visual-failure",
        action="append",
        default=[],
        metavar="LABEL",
        help="record a human-reviewed screenshot failure (repeatable; makes the run fail)",
    )
    args = parser.parse_args()

    project = args.project.resolve()
    editor = args.editor.resolve()
    if not editor.is_file():
        parser.error(f"editor not found: {editor}")
    sample_times = sorted({float(value) for value in args.times.split(",") if value.strip()})
    if not sample_times or sample_times[0] < 0:
        parser.error("--times must contain non-negative seconds")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output = (args.output or project / "diag" / "battle2-skinning" / f"{stamp}-{args.backend}").resolve()
    output.mkdir(parents=True, exist_ok=True)

    report: dict[str, Any] = {
        "startedUtc": datetime.now(timezone.utc).isoformat(),
        "backend": args.backend,
        "editor": str(editor),
        "project": str(project),
        "engineSha": git_sha(engine),
        "projectSha": git_sha(project),
        "sourceProjectSha": git_sha(script.parents[1]),
        "sampleTimes": sample_times,
        "stateMatrixRequested": args.state_matrix,
        "metrics": [],
        "stateMatrix": [],
        "screenshots": [],
        "errors": [],
    }
    client = EditorMcp(editor, project, args.backend, output, args.skin_diag)
    try:
        client.initialize()
        report["initialState"] = wait_until_ready(client, args.ready_timeout)
        opened = client.eval(
            'return XEngine.Editor.GUI.SceneView.EditorSceneManager.OpenScene("Scenes/ZonezeroBattle2.scene");',
            timeout=300,
        )
        report["openScene"] = opened
        time.sleep(2)
        client.tool("runtime_menu", {"action": "invoke", "path": "Window/General/New Game View"}, timeout=120)
        time.sleep(2)
        report["editCameraOverview"] = client.eval(OVERVIEW_CAMERA_CODE)
        time.sleep(0.2)
        report["screenshots"].append(capture(client, output, "edit-overview.png"))
        for hero in HERO_NAMES:
            client.eval(CLOSEUP_CAMERA_TEMPLATE.replace("__HERO__", hero))
            time.sleep(0.2)
            report["screenshots"].append(capture(client, output, f"edit-{hero}.png"))

        edit_baseline = client.eval(POSE_SAMPLE_CODE, timeout=300)
        (output / "metrics-edit-baseline.txt").write_text(edit_baseline, encoding="utf-8")
        report["metrics"].extend(parse_metrics(edit_baseline, 0.0, "edit-baseline"))

        client.tool("runtime_playmode", {"action": "enter"}, timeout=300)
        play_started = time.monotonic()
        for index, sample_time in enumerate(sample_times):
            remaining = sample_time - (time.monotonic() - play_started)
            if remaining > 0:
                time.sleep(remaining)
            report["cameraOverview"] = client.eval(OVERVIEW_CAMERA_CODE)
            time.sleep(0.2)
            tag = str(sample_time).replace(".", "p")
            report["screenshots"].append(capture(client, output, f"play-{tag}s-overview.png"))
            metric_text = client.eval(SAMPLE_CODE, timeout=300)
            (output / f"metrics-{tag}s.txt").write_text(metric_text, encoding="utf-8")
            report["metrics"].extend(parse_metrics(metric_text, sample_time))
            if index == len(sample_times) - 1:
                for hero in HERO_NAMES:
                    client.eval(CLOSEUP_CAMERA_TEMPLATE.replace("__HERO__", hero))
                    time.sleep(0.2)
                    report["screenshots"].append(capture(client, output, f"play-{tag}s-{hero}.png"))

        if args.state_matrix:
            report["disabledHeroGameplay"] = client.eval(DISABLE_HERO_GAMEPLAY_CODE)
            # A cold import activates an Animator controller only after every referenced motion is
            # resident. Wait for that all-or-nothing bind before issuing one-shot state commands;
            # otherwise a command sent during streaming is correctly ignored and the later phase
            # wait can only observe the default Idle state.
            report["controllerStateAvailability"] = {
                state_name: wait_until_state_available(
                    client, state_name, timeout=args.ready_timeout
                )
                for state_name in ("Idle", "Run", "Attack_Normal_1")
            }
            report["stateSampleNormalizedPhases"] = {
                "Idle": [0.10, 0.35, 0.70],
                "Run": [0.10, 0.35, 0.70],
                "Attack_Normal_1": [0.10, 0.35, 0.70],
            }

            def play_all_states(sequence: tuple[tuple[str, float], ...]) -> str:
                result = client.eval(build_play_all_states_code(sequence), timeout=300)
                seen = {
                    fields[1]
                    for line in result.splitlines()
                    if len(fields := line.split("|")) == 6
                    and fields[0] == "PLAY"
                    and fields[2] != "NO_ANIMATOR"
                }
                for hero in HERO_NAMES:
                    if hero not in seen:
                        report["errors"].append(
                            f"same-frame state switch did not resolve {hero}: {result}"
                        )
                return result

            def collect_pose(
                phase: str,
                expected_state: str,
                expected_transition: bool,
            ) -> list[dict[str, Any]]:
                elapsed = time.monotonic() - play_started
                metric_text = client.eval(POSE_SAMPLE_CODE, timeout=300)
                (output / f"state-{phase}-metrics.txt").write_text(
                    metric_text, encoding="utf-8"
                )
                phase_metrics = parse_metrics(
                    metric_text,
                    elapsed,
                    phase,
                    expected_state=expected_state,
                    expected_transition=expected_transition,
                )
                report["metrics"].extend(phase_metrics)
                return phase_metrics

            def sample_pose_series(
                phase: str,
                state_name: str,
                normalized_phases: tuple[float, ...],
            ) -> None:
                series: list[dict[str, Any]] = []
                for normalized_phase in normalized_phases:
                    play_result = play_all_states(((state_name, 0.0),))
                    wait_result = wait_until_normalized_phase(
                        client, state_name, normalized_phase
                    )
                    phase_tag = f"{normalized_phase:.2f}".replace(".", "p")
                    sample_phase = f"{phase}-sample-{phase_tag}n"
                    phase_metrics = collect_pose(sample_phase, state_name, False)
                    series.append(
                        {
                            "requestedNormalizedTime": normalized_phase,
                            "actual": animator_snapshot(phase_metrics),
                            "playResult": play_result,
                            "waitResult": wait_result,
                        }
                    )
                report.setdefault("poseSeries", {})[state_name] = series

            def capture_fixed_state(
                phase: str,
                sequence: tuple[tuple[str, float], ...],
                expected_state: str,
                expected_transition: bool,
                views: tuple[str, ...],
                normalized_phase: float | None = None,
                transition_wait: float | None = None,
            ) -> None:
                for view in views:
                    play_result = play_all_states(sequence)
                    if normalized_phase is not None:
                        wait_result = wait_until_normalized_phase(
                            client, expected_state, normalized_phase
                        )
                    else:
                        time.sleep(transition_wait or 0.0)
                        wait_result = client.eval(PAUSE_GAME_CODE)

                    sample_phase = f"{phase}-capture-{view}"
                    phase_metrics = collect_pose(
                        sample_phase, expected_state, expected_transition
                    )
                    if view == "overview":
                        camera_result = client.eval(OVERVIEW_CAMERA_CODE)
                    else:
                        camera_result = client.eval(
                            CLOSEUP_CAMERA_TEMPLATE.replace("__HERO__", view)
                        )
                    image = capture(client, output, f"state-{phase}-{view}.png")
                    report["screenshots"].append(image)
                    report["stateMatrix"].append(
                        {
                            "phase": phase,
                            "view": view,
                            "expectedState": expected_state,
                            "expectedTransition": expected_transition,
                            "requestedNormalizedTime": normalized_phase,
                            "transitionWaitSeconds": transition_wait,
                            "actual": animator_snapshot(phase_metrics),
                            "playResult": play_result,
                            "waitResult": wait_result,
                            "cameraResult": camera_result,
                            "screenshot": image,
                        }
                    )

            sample_pose_series("idle", "Idle", (0.10, 0.35, 0.70))
            sample_pose_series("run", "Run", (0.10, 0.35, 0.70))
            sample_pose_series(
                "attack-normal-1", "Attack_Normal_1", (0.10, 0.35, 0.70)
            )

            all_views = ("overview", *HERO_NAMES)
            capture_fixed_state(
                "idle", (("Idle", 0.0),), "Idle", False, all_views, normalized_phase=0.35
            )
            capture_fixed_state(
                "idle-to-run",
                (("Idle", 0.0), ("Run", 5.0)),
                "Run",
                True,
                ("overview",),
                transition_wait=0.03,
            )
            capture_fixed_state(
                "run", (("Run", 0.0),), "Run", False, all_views, normalized_phase=0.35
            )
            capture_fixed_state(
                "run-to-attack",
                (("Run", 0.0), ("Attack_Normal_1", 5.0)),
                "Attack_Normal_1",
                True,
                ("overview",),
                transition_wait=0.03,
            )
            capture_fixed_state(
                "attack-normal-1",
                (("Attack_Normal_1", 0.0),),
                "Attack_Normal_1",
                False,
                all_views,
                normalized_phase=0.35,
            )

            lifecycle_play = play_all_states((("Run", 0.0),))
            lifecycle_wait = wait_until_normalized_phase(client, "Run", 0.35)
            lifecycle_baseline_metrics = collect_pose(
                "reenable-baseline", "Run", False
            )
            lifecycle_baselines = {
                hero: key_bone_snapshot(lifecycle_baseline_metrics, hero)
                for hero in HERO_NAMES
            }
            report["reenableLifecycle"] = []
            for hero in HERO_NAMES:
                toggle_result = client.eval(
                    TOGGLE_HERO_ROOT_TEMPLATE.replace("__HERO__", hero), timeout=300
                )
                available_result = wait_until_state_available(client, "Run")
                replay_result = play_all_states((("Run", 0.0),))
                replay_wait = wait_until_normalized_phase(client, "Run", 0.35)
                phase = f"reenable-{hero}"
                after_metrics = collect_pose(phase, "Run", False)
                after_snapshot = key_bone_snapshot(after_metrics, hero)
                comparison = compare_reference_pose(
                    lifecycle_baselines[hero], after_snapshot
                )
                if comparison["beforeBoneCount"] < 8:
                    report["errors"].append(
                        f"{hero} re-enable baseline exposed only "
                        f"{comparison['beforeBoneCount']} key bones"
                    )
                if comparison["animatedBeforeBones"] < 4:
                    report["errors"].append(
                        f"{hero} re-enable baseline was not a non-zero Run pose: only "
                        f"{comparison['animatedBeforeBones']} animated key bones"
                    )
                if comparison["missingBefore"] or comparison["missingAfter"]:
                    report["errors"].append(
                        f"{hero} re-enable key-bone coverage changed: "
                        f"missing before={comparison['missingBefore']}, "
                        f"missing after={comparison['missingAfter']}"
                    )
                if comparison["driftedBones"]:
                    report["errors"].append(
                        f"{hero} reference-pose drift after root Enabled=false->true: "
                        f"{len(comparison['driftedBones'])} key bones changed "
                        f"(position {comparison['maxPositionDelta']:.6f}m, "
                        f"rotation {comparison['maxRotationDeltaDegrees']:.4f}deg, "
                        f"scale {comparison['maxScaleDelta']:.6f})"
                    )

                camera_result = client.eval(
                    CLOSEUP_CAMERA_TEMPLATE.replace("__HERO__", hero)
                )
                image = capture(client, output, f"state-{phase}.png")
                report["screenshots"].append(image)
                lifecycle_entry = {
                    "phase": "reenable",
                    "hero": hero,
                    "requestedNormalizedTime": 0.35,
                    "actual": animator_snapshot(after_metrics),
                    "baselinePlayResult": lifecycle_play,
                    "baselineWaitResult": lifecycle_wait,
                    "toggleResult": toggle_result,
                    "controllerAvailableResult": available_result,
                    "replayResult": replay_result,
                    "replayWaitResult": replay_wait,
                    "referencePoseComparison": comparison,
                    "cameraResult": camera_result,
                    "screenshot": image,
                }
                report["reenableLifecycle"].append(lifecycle_entry)
                report["stateMatrix"].append(lifecycle_entry)
            client.eval(RESUME_GAME_CODE)

        validate_animation_semantics(report)

        client.eval(OVERVIEW_CAMERA_CODE)
        time.sleep(3.0)  # fill the 120-frame history after screenshot/eval acceptance work
        report["runtimeStats"] = response_value(client.tool("runtime_stats", timeout=120))

        logs = client.tool("runtime_logs", {"count": 512, "minimumSeverity": "Warning"}, timeout=120)
        log_entries = runtime_log_entries(logs)
        (output / "runtime-logs.json").write_text(
            json.dumps(logs.raw, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        for message in fatal_runtime_logs(log_entries):
            report["errors"].append(f"runtime log failure: {message}")

        for metric in report["metrics"]:
            if metric.get("kind") != "smr":
                continue
            if metric["nullBones"] or metric["badIndices"] or metric["badWeights"] or metric["nonFinite"]:
                report["errors"].append(
                    f"{metric['root']}/{metric['renderer']} invalid skin data at {metric['time']}s"
                )
            if max(metric["extent"]) > 10.0:
                report["errors"].append(
                    f"{metric['root']}/{metric['renderer']} CPU-skinned extent exceeds 10m at {metric['time']}s: "
                    f"{metric['extent']}"
                )

        # The coarse wall-clock samples can alias a short looping clip at the same normalized
        # phase (for example Nike's 0.483 s Run sampled roughly three cycles apart). The state
        # matrix already performs a stronger multi-phase limb-motion check, so do not let this
        # fallback heuristic turn a verified moving pose into a false stalled-animation failure.
        if not args.state_matrix:
            by_root: dict[str, list[dict[str, Any]]] = {name: [] for name in HERO_NAMES}
            for metric in report["metrics"]:
                if (
                    metric.get("kind") == "animator"
                    and metric.get("root") in by_root
                    and "phase" not in metric
                    and metric.get("clip") not in {"<null>", "<none>"}
                ):
                    by_root[metric["root"]].append(metric)
            for root, samples in by_root.items():
                if len(samples) < 2:
                    continue
                first = samples[0]
                same_state = [
                    sample
                    for sample in samples
                    if sample["clip"] == first["clip"]
                    and sample["stateHash"] == first["stateHash"]
                ]
                if len(same_state) < 2:
                    continue
                normalized_times = [sample["normalizedTime"] for sample in same_state]
                start = same_state[0]["position"]
                end = same_state[-1]["position"]
                horizontal_distance = math.hypot(end[0] - start[0], end[2] - start[2])
                if max(normalized_times) - min(normalized_times) < 0.001 and horizontal_distance > 0.05:
                    report["errors"].append(
                        f"{root} stalled-animation sliding: state {first['stateHash']} stayed at "
                        f"normalized times {normalized_times} while moving {horizontal_distance:.3f}m"
                    )
        for label in args.visual_failure:
            report["errors"].append(f"human-reviewed visual failure: {label}")
    except Exception as exc:
        report["errors"].append(f"driver failure: {type(exc).__name__}: {exc}")
    finally:
        client.close()
        report["finishedUtc"] = datetime.now(timezone.utc).isoformat()
        (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(str(output))
    print(json.dumps({"errors": report["errors"], "screenshots": report["screenshots"]}, ensure_ascii=False))
    return 1 if report["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
