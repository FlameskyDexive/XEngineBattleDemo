#!/usr/bin/env python3
"""Reproduce and measure Battle2 skinning failures through the live editor MCP server.

The driver is intentionally project-local and path independent.  It launches an editor we own,
opens ZonezeroBattle2, captures an overview plus one close-up per hero, and records the exact CPU
skinning matrices that were uploaded for every SkinnedMeshRenderer.  It never edits the scene.
"""

from __future__ import annotations

import argparse
import json
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
          .Append(root.Transform.Position.X.ToString("R", inv)).Append('|')
          .Append(root.Transform.Position.Y.ToString("R", inv)).Append('|')
          .Append(root.Transform.Position.Z.ToString("R", inv)).Append('\n');
    }
    else
    {
        sb.Append("ANIM|").Append(rootName).Append("|<none>|0|0|0|0\n");
    }

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
        text = self.tool("runtime_eval", {"code": code}, timeout=timeout).text
        value = unwrap_json_envelope(text)
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


def parse_metrics(text: str, sample_time: float, phase: str | None = None) -> list[dict[str, Any]]:
    parsed: list[dict[str, Any]] = []
    for line in text.splitlines():
        fields = line.split("|")
        if not fields:
            continue
        if fields[0] == "ANIM" and len(fields) == 7:
            parsed.append(
                {
                    "kind": "animator",
                    "time": sample_time,
                    "root": fields[1],
                    "clip": fields[2],
                    "normalizedTime": float(fields[3]),
                    "position": [float(fields[4]), float(fields[5]), float(fields[6])],
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
        help="exercise Idle, Run, Attack_Normal_1, and the two intervening cross-fades",
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
            identifiers: dict[str, str] = {}
            for hero in HERO_NAMES:
                identifier = client.eval(HERO_IDENTIFIER_TEMPLATE.replace("__HERO__", hero)).strip()
                if not identifier:
                    report["errors"].append(f"state matrix could not resolve {hero} identifier")
                else:
                    identifiers[hero] = identifier

            def read_state(identifier: str) -> Any:
                return response_value(
                    client.tool("runtime_animator_state", {"identifier": identifier}, timeout=120)
                )

            def play_state(state_name: str, fade_duration: float) -> tuple[dict[str, Any], dict[str, Any]]:
                results: dict[str, Any] = {}
                immediate_states: dict[str, Any] = {}
                for hero, identifier in identifiers.items():
                    response = client.tool(
                        "runtime_animator_play",
                        {
                            "identifier": identifier,
                            "stateName": state_name,
                            "layer": 0,
                            "fadeDuration": fade_duration,
                        },
                        timeout=120,
                    )
                    results[hero] = response_value(response)
                    if response.is_error:
                        report["errors"].append(
                            f"{hero} could not play {state_name}: {response.text or response.raw}"
                        )
                    # Short non-looping clips can finish a fade before three serial RPC reads
                    # complete. Snapshot each hero immediately after its own play command.
                    immediate_states[hero] = read_state(identifier)
                return results, immediate_states

            def read_states() -> dict[str, Any]:
                states: dict[str, Any] = {}
                for hero, identifier in identifiers.items():
                    states[hero] = read_state(identifier)
                return states

            def sample_state_phase(
                phase: str,
                closeups: bool,
                validation_states: dict[str, Any] | None = None,
            ) -> None:
                states = validation_states if validation_states is not None else read_states()
                client.eval(OVERVIEW_CAMERA_CODE)
                time.sleep(0.15)
                overview = capture(client, output, f"state-{phase}-overview.png")
                report["screenshots"].append(overview)
                elapsed = time.monotonic() - play_started
                metric_text = client.eval(SAMPLE_CODE, timeout=300)
                (output / f"state-{phase}-metrics.txt").write_text(metric_text, encoding="utf-8")
                phase_metrics = parse_metrics(metric_text, elapsed, phase)
                report["metrics"].extend(phase_metrics)
                states_after_capture = read_states()
                captures = [overview]
                if closeups:
                    for hero in HERO_NAMES:
                        client.eval(CLOSEUP_CAMERA_TEMPLATE.replace("__HERO__", hero))
                        time.sleep(0.15)
                        image = capture(client, output, f"state-{phase}-{hero}.png")
                        report["screenshots"].append(image)
                        captures.append(image)
                report["stateMatrix"].append(
                    {
                        "phase": phase,
                        "elapsed": elapsed,
                        "states": states,
                        "statesAfterCapture": states_after_capture,
                        "screenshots": captures,
                    }
                )

            report["statePlayIdle"], _ = play_state("Idle", 0.0)
            time.sleep(0.2)
            sample_state_phase("idle", closeups=True)
            report["statePlayRunTransition"], run_transition_states = play_state("Run", 5.0)
            time.sleep(0.03)
            sample_state_phase("idle-to-run", closeups=False, validation_states=run_transition_states)
            report["statePlayRun"], _ = play_state("Run", 0.0)
            time.sleep(0.2)
            sample_state_phase("run", closeups=True)
            report["statePlayAttackTransition"], attack_transition_states = play_state(
                "Attack_Normal_1", 5.0
            )
            time.sleep(0.03)
            sample_state_phase(
                "run-to-attack", closeups=False, validation_states=attack_transition_states
            )
            report["statePlayAttack"], _ = play_state("Attack_Normal_1", 0.0)
            time.sleep(0.2)
            sample_state_phase("attack-normal-1", closeups=True)

            expected_states = {
                "idle": ("Idle", False),
                "idle-to-run": ("Run", True),
                "run": ("Run", False),
                "run-to-attack": ("Attack_Normal_1", True),
                "attack-normal-1": ("Attack_Normal_1", False),
            }
            for state_sample in report["stateMatrix"]:
                expected_name, expected_transition = expected_states[state_sample["phase"]]
                expected_hash = animation_name_hash(expected_name)
                for hero in HERO_NAMES:
                    state = state_sample["states"].get(hero)
                    layers = state.get("layers") if isinstance(state, dict) else None
                    layer = layers[0] if isinstance(layers, list) and layers else {}
                    actual_name = layer.get("stateName")
                    actual_hash = layer.get("nameHash")
                    if actual_name and actual_name != expected_name:
                        report["errors"].append(
                            f"{hero} expected state {expected_name} during {state_sample['phase']}: {layer}"
                        )
                    elif not actual_name and actual_hash != expected_hash:
                        report["errors"].append(
                            f"{hero} expected state hash {expected_hash} ({expected_name}) "
                            f"during {state_sample['phase']}: {layer}"
                        )
                    if bool(layer.get("isInTransition")) != expected_transition:
                        report["errors"].append(
                            f"{hero} transition flag mismatch during {state_sample['phase']}: {layer}"
                        )

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

        by_root: dict[str, list[float]] = {name: [] for name in HERO_NAMES}
        for metric in report["metrics"]:
            if metric.get("kind") == "animator" and metric.get("root") in by_root:
                by_root[metric["root"]].append(metric["normalizedTime"])
        for root, values in by_root.items():
            if len(values) < 2 or max(values) - min(values) < 0.001:
                report["errors"].append(f"{root} animator time did not advance: {values}")
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
