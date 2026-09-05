#!/usr/bin/env python3
"""Exercise production Battle2 Root Motion, collision and visual continuity in a live editor.

Uses real input and the production controllers. Scene changes for the wall case are temporary.
The original in-place diagnostic keeps its defaults; this run explicitly expects Root Motion.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import time
from datetime import datetime, timezone
from pathlib import Path

import battle2_controls_diagnostic as controls
from battle2_skinning_diagnostic import capture, fatal_runtime_logs, git_sha, response_value, runtime_log_entries, wait_until_ready

PROJECT = Path(__file__).resolve().parents[1]
ENGINE = PROJECT.parent

TELEMETRY = r'''
var rows = new System.Collections.Generic.List<object>();
foreach (var root in Scene.Current.RootObjects)
foreach (var component in root.GetComponentsInChildren<XEngine.Runtime.MonoBehaviour>(true, true))
{
    var type = component.GetType();
    if (type.Name != "HeroCombatController" && type.Name != "AllyCombatAI") continue;
    var animator = component.GameObject.GetComponent<XEngine.Runtime.Animator>();
    object Read(string name) => type.GetProperty(name)?.GetValue(component);
    rows.Add(new { actor = component.GameObject.Name, controller = type.Name,
        enabled = component.EnabledInHierarchy, rootMotion = animator.ApplyRootMotion,
        planar = animator.RootMotionPlanar, rotation = animator.ApplyRootMotionRotation,
        requested = Read("RootMotionRequestedDistance"), applied = Read("RootMotionAppliedDistance"),
        steps = Read("RootMotionSteps"), hits = Read("SuccessfulHits") });
}
return System.Text.Json.JsonSerializer.Serialize(rows);
'''

WALL = r'''
XEngine.Runtime.GameObject hero = null;
foreach (var root in Scene.Current.RootObjects) if (root.Name == "Battle_Hero") hero = root;
if (hero == null) return "ERROR|no hero";
var start = hero.Transform.Position;
var direction = hero.Transform.Forward;
float closest = float.MaxValue;
foreach (var root in Scene.Current.RootObjects)
foreach (var component in root.GetComponentsInChildren<XEngine.Runtime.MonoBehaviour>(true, true))
{
    if (component.GetType().Name != "EnemyController") continue;
    var delta = component.Transform.Position - start;
    delta.Y = 0f;
    float distance = XEngine.Vector.Float3.LengthSquared(delta);
    if (distance < closest) { closest = distance; direction = delta; }
}
direction.Y = 0f;
direction = XEngine.Vector.Float3.Normalize(direction);
hero.Transform.Rotation = XEngine.Vector.Quaternion.LookRotation(direction, XEngine.Vector.Float3.UnitY);
var wall = new XEngine.Runtime.GameObject("RootMotionAcceptanceWall");
wall.Transform.Position = start + direction * 1.5f + new XEngine.Vector.Float3(0f, 1.5f, 0f);
wall.Transform.Rotation = hero.Transform.Rotation;
wall.AddComponent<XEngine.Runtime.BoxCollider>().Size = new XEngine.Vector.Float3(12f, 3f, 0.3f);
Scene.Current.Add(wall);
return System.Text.Json.JsonSerializer.Serialize(new { start = new[] {start.X,start.Y,start.Z},
    normal = new[] {direction.X,direction.Y,direction.Z}, centerDistance = 1.5f, halfThickness = 0.15f });
'''

MELEE_SETUP = r'''
XEngine.Runtime.GameObject hero = null;
foreach (var root in Scene.Current.RootObjects) if (root.Name == "Battle_Hero") hero = root;
XEngine.Runtime.GameObject target = null;
float nearest = float.MaxValue;
foreach (var root in Scene.Current.RootObjects)
foreach (var component in root.GetComponentsInChildren<XEngine.Runtime.MonoBehaviour>(true, true))
{
    if (component.GetType().Name != "EnemyController") continue;
    float distance = XEngine.Vector.Float3.LengthSquared(component.Transform.Position - hero.Transform.Position);
    if (distance < nearest) { nearest = distance; target = component.GameObject; }
}
if (target == null) return "ERROR|no melee target";
var direction = target.Transform.Position - hero.Transform.Position;
direction.Y = 0f;
direction = XEngine.Vector.Float3.Normalize(direction);
hero.Transform.Position = target.Transform.Position - direction * 2.5f;
hero.Transform.Rotation = XEngine.Vector.Quaternion.LookRotation(direction, XEngine.Vector.Float3.UnitY);
return target.Name;
'''


def hero_telemetry(rows: list[dict]) -> dict:
    return next(row for row in rows if row["controller"] == "HeroCombatController")


def analyse_action(key: str, frames: list[dict], before: list[dict], after: list[dict], wall: dict | None = None) -> tuple[dict, list[str]]:
    failures: list[str] = []
    states = list(dict.fromkeys(frame["clip"] for frame in frames))
    for state in controls.ACTION_STATES[key]:
        if state not in states:
            failures.append(f"{key}: missing production state {state}")
    active = [frame for frame in frames if frame["activeAction"] != "None"]
    if not active or frames[-1]["activeAction"] != "None":
        failures.append(f"{key}: action did not start and return to locomotion")
    if not all(frame["applyRootMotion"] for frame in frames):
        failures.append(f"{key}: Root Motion was disabled")

    steps = [controls._distance3(a["rootPosition"], b["rootPosition"]) for a, b in zip(frames, frames[1:])]
    net = controls._distance3(frames[0]["rootPosition"], frames[-1]["rootPosition"])
    # This is a discontinuity guard, not a target travel speed: normal authored movement is
    # separately required by telemetry and clip-level unit tests covering different frame rates.
    max_step = max(steps, default=0.0)
    if not math.isfinite(max_step) or max_step > 1.0:
        failures.append(f"{key}: actor moved {max_step:.3f}m in one observed frame")
    heading_changes = [controls._angle_deg(tuple(a["heroForwardXZ"]), tuple(b["heroForwardXZ"]))
                       for a, b in zip(active, active[1:]) if a["clip"] == b["clip"]]
    if max(heading_changes, default=0.0) > 2.0:
        failures.append(f"{key}: cast heading changed while the same skill was running")

    start, end = hero_telemetry(before), hero_telemetry(after)
    requested = end["requested"] - start["requested"]
    applied = end["applied"] - start["applied"]
    hit_count = end["hits"] - start["hits"]
    if key == "J" and hit_count != 1:
        failures.append(f"J: the reachable practice target received {hit_count} hits, expected one")
    if requested < 0.15 or applied < 0.05:
        failures.append(f"{key}: no meaningful animation-driven controller movement ({requested:.3f}/{applied:.3f}m)")
    if not end["rootMotion"] or not end["planar"] or end["rotation"]:
        failures.append(f"{key}: incorrect production root-motion settings")
    if abs(sum(steps) - applied) > 0.12:
        failures.append(f"{key}: actor path {sum(steps):.3f}m does not match CC applied path {applied:.3f}m")

    visual_metrics = {}
    for state in controls.ACTION_STATES[key]:
        state_frames = [frame["visual"] for frame in frames if frame["clip"] == state]
        if state_frames:
            metrics, issues = controls._analyse_actor_state(
                f"rootmotion/{key}", state, state_frames, stationary_root=False,
                expected_root_motion=True, planar_root_motion=True)
            visual_metrics[state] = metrics
            failures.extend(issues)

    recovery = [frame for frame in frames if frame["clip"] == "BigSkill_End"]
    recovery_travel = sum(controls._distance3(a["rootPosition"], b["rootPosition"])
                          for a, b in zip(recovery, recovery[1:]))
    if recovery_travel > 0.30:
        failures.append(f"I: constant-offset recovery displaced the actor {recovery_travel:.3f}m")
    if wall:
        max_plane_distance = max(sum((frame["rootPosition"][i] - wall["start"][i]) * wall["normal"][i]
                                     for i in (0, 2)) for frame in frames)
        if max_plane_distance > wall["centerDistance"] - wall["halfThickness"] + 0.03:
            failures.append("L/wall: actor crossed the wall surface")
        if requested - applied < 0.5:
            failures.append("L/wall: collision did not reject authored travel")
    return {"states": states, "netTravel": net, "pathTravel": sum(steps), "maxFrameStep": max_step,
            "requestedDistance": requested, "appliedDistance": applied, "successfulHits": hit_count,
            "recoveryTravel": recovery_travel, "visualStates": visual_metrics}, failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", choices=("vulkan", "opengl"), default="vulkan")
    parser.add_argument("--editor", type=Path, default=ENGINE / "Build/Editor/Release/net10.0/XEngine.Editor.exe")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--natural-frames", type=int, default=1200)
    parser.add_argument("--cases", default="W,J,K,L,I,L-wall,natural")
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    cases = args.cases.split(",")
    report = {"backend": args.backend, "engineSha": git_sha(ENGINE), "projectSha": git_sha(PROJECT),
              "startedUtc": datetime.now(timezone.utc).isoformat(), "cases": {}, "errors": [], "passed": False,
              "runtimeSha256": hashlib.sha256((args.editor.parent / "XEngine.Runtime.dll").read_bytes()).hexdigest()}
    client = controls.EditorMcp(args.editor.resolve(), PROJECT, args.backend, output, skin_diag=False)
    try:
        client.initialize()
        wait_until_ready(client, 600)
        client.tool("runtime_logs", {"limit": 1})
        client.tool("runtime_menu", {"action": "invoke", "path": "Window/General/New Game View"})
        for case in cases:
            print(f"[RootMotion] {case}", flush=True)
            case_output = output / case
            case_output.mkdir(exist_ok=True)
            try:
                controls._force_open_scene(client)
                client.tool("runtime_playmode", {"action": "enter"}, timeout=300)
                controls._wait_for_playmode(client, True, 300)
                controls._wait_for_runtime_ready(client, 300)
                time.sleep(0.4)
                before = json.loads(client.eval(TELEMETRY))
                if case == "W":
                    samples = controls._sample_production_locomotion(client, "W", 120, 300)
                    metrics, failures = controls._analyse_production_locomotion("W", samples,
                                                                               expected_root_motion=True)
                    after = json.loads(client.eval(TELEMETRY))
                    if abs(hero_telemetry(after)["requested"] - hero_telemetry(before)["requested"]) > 0.001:
                        failures.append("W: locomotion also consumed combat root motion")
                    capture(client, case_output, "game.png")
                    entry = {"samples": samples, "metrics": metrics}
                elif case == "natural":
                    entry = controls._run_natural_observer(client, args.natural_frames, 360, case_output,
                                                          expected_root_motion=True, planar_root_motion=True)
                    failures = list(entry["failures"])
                    after = json.loads(client.eval(TELEMETRY))
                    for row in after:
                        prior = next(item for item in before if item["actor"] == row["actor"])
                        if row["controller"] == "AllyCombatAI" and (row["steps"] <= prior["steps"] or row["requested"] - prior["requested"] < 0.15):
                            failures.append(f"{row['actor']}: AI never consumed skill root motion")
                else:
                    key = case[0]
                    controls._wait_for_action_states(client, key, 300)
                    melee_target = client.eval(MELEE_SETUP) if key == "J" else None
                    if melee_target:
                        time.sleep(0.3)
                    wall = json.loads(client.eval(WALL)) if case == "L-wall" else None
                    frames, captures = controls._run_action_probe(client, key, 600, 8, 300, case_output)
                    after = json.loads(client.eval(TELEMETRY))
                    metrics, failures = analyse_action(key, frames, before, after, wall)
                    entry = {"frames": frames, "captures": captures, "metrics": metrics, "wall": wall,
                             "meleeSetupTarget": melee_target}
                    capture(client, case_output, "recovered.png")
                entry.update({"before": before, "after": after, "errors": failures, "passed": not failures})
                report["cases"][case] = entry
                report["errors"].extend(f"{case}: {failure}" for failure in failures)
                print(json.dumps({"case": case, "passed": not failures, "errors": failures}, ensure_ascii=False), flush=True)
            except Exception as error:
                report["errors"].append(f"{case}: {type(error).__name__}: {error}")
                print(report["errors"][-1], flush=True)
            finally:
                client.eval('XEngine.Runtime.Application.IsPaused = false; return "resumed";')
                client.tool("runtime_playmode", {"action": "exit"}, timeout=300)
                controls._wait_for_playmode(client, False, 300)
        logs = client.tool("runtime_logs", {"limit": 100})
        report["logs"] = response_value(logs)
        report["errors"].extend(fatal_runtime_logs(runtime_log_entries(logs)))
        report["stats"] = response_value(client.tool("runtime_stats"))
        report["passed"] = not report["errors"]
    except Exception as error:
        report["errors"].append(f"{type(error).__name__}: {error}")
    finally:
        client.close()
        report["finishedUtc"] = datetime.now(timezone.utc).isoformat()
        with gzip.open(output / "report.json.gz", "wt", encoding="utf-8") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2)
    print(json.dumps({"passed": report["passed"], "errors": report["errors"]}, ensure_ascii=False), flush=True)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
