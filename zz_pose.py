#!/usr/bin/env python3
"""Decisive: inspect the playable graph outputs + mixer pose directly."""
import json, subprocess, time, os

EDITOR = r"F:\Git\XEngine\Build\Editor\Debug\net10.0\XEngine.Editor.exe"
PROJECT = r"F:\Git\XEngine\ZonezeroTestProject"

CODE2 = (
    'var r0 = Scene.Current.RootObjects.Where(r => r.Name == "Test_FsmDriven").First();'
    'var an = (XEngine.Runtime.Animator)r0.GetComponent<XEngine.Runtime.Animator>();'
    'var BF = System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.Public;'
    'var outF = typeof(XEngine.Runtime.Animator).GetField("_output", BF);'
    'var output = outF.GetValue(an) as XEngine.Animation.AnimationPlayableOutput;'
    'if (output == null) return "output NULL";'
    'var srcPlayable = output.Source as XEngine.Animation.AnimationPlayable;'
    'if (srcPlayable == null) return "source null/not animatable: " + (output.Source != null ? output.Source.GetType().FullName : "null");'
    'var pose = srcPlayable.Output;'
    'if (pose == null) return "pose NULL";'
    'int setCount = 0; int pelvisIdx = -1;'
    'var sk = an.Skeleton;'
    'for (int i = 0; i < pose.Bones.Length && i < sk.Bones.Length; i++) {'
    '  if (pose.Bones[i].Channels != XEngine.Animation.PoseChannel.None) setCount++;'
    '  var g = sk.Bones[i];'
    '  if (g != null && g.GameObject != null && g.GameObject.Name == "Bip001 Pelvis") pelvisIdx = i; }'
    'var lps = pelvisIdx >= 0 ? pose.Bones[pelvisIdx].LocalPosition.ToString() : "no-pelvis";'
    'var lrr = pelvisIdx >= 0 ? pose.Bones[pelvisIdx].LocalRotation.ToString() : "-";'
    'return "srcType=" + output.Source.GetType().Name + " poseSet=" + setCount + "/" + pose.Bones.Length'
    '     + " pelvisPoseLP=" + lps + " pelvisPoseLR=" + lrr;')

proc = subprocess.Popen([EDITOR, "--serve", "--project", PROJECT],
                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                        cwd=os.path.dirname(EDITOR))
import threading
_responses = {}
elog = open(r"diag/zz_pose_stdio.log", "ab")
def _drain():
    try:
        for line in proc.stdout:
            elog.write(line); elog.flush()
            s2 = line.decode("utf-8", errors="replace").strip()
            if not s2: continue
            try: m = json.loads(s2)
            except json.JSONDecodeError: continue
            if isinstance(m.get("id"), int):
                _responses[m["id"]] = m
    except Exception:
        pass
threading.Thread(target=_drain, daemon=True).start()

_id = 0
def call_eval(code, timeout=240):
    global _id
    _id += 1
    my = _id
    proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": my, "method": "tools/call",
        "params": {"name": "runtime_eval", "arguments": {"code": code}}}).encode() + b"\n")
    proc.stdin.flush()
    dl = time.time() + timeout
    while time.time() < dl:
        if my in _responses:
            m = _responses.pop(my)
            try:
                c = ((m.get("result") or {}).get("content") or [])
                return "".join(x.get("text") or "" for x in c)
            except Exception:
                pass
        time.sleep(0.05)
    return "TIMEOUT"

send0 = call_eval
send0('return "ready";')
print("[open]", send0('return XEngine.Editor.GUI.SceneView.EditorSceneManager.OpenScene("Scenes/AnimSingleTest.scene");')[:60], flush=True)
time.sleep(3)
proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 999001, "method": "tools/call",
    "params": {"name": "runtime_playmode", "arguments": {"action": "enter"}}}).encode() + b"\n")
proc.stdin.flush()
time.sleep(8)

print("[GRAPH1]", send0(CODE2)[:400], flush=True)
time.sleep(0.5)
print("[GRAPH2]", send0(CODE2)[:400], flush=True)

try:
    proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 999002, "method": "tools/call",
        "params": {"name": "runtime_playmode", "arguments": {"action": "exit"}}}).encode() + b"\n")
    proc.stdin.flush()
except Exception:
    pass
time.sleep(5)
try:
    proc.stdin.close(); proc.wait(timeout=15)
except Exception:
    proc.kill()
