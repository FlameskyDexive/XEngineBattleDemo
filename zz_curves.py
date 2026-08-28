#!/usr/bin/env python3
"""Dump the actual curve keys of the Run clip to see if the data itself is constant."""
import json, subprocess, time, os

EDITOR = r"F:\Git\XEngine\Build\Editor\Debug\net10.0\XEngine.Editor.exe"
PROJECT = r"F:\Git\XEngine\ZonezeroTestProject"

CODE = (
    'var r0 = Scene.Current.RootObjects.Where(r => r.Name == "Test_FsmDriven").First();'
    'var an = (XEngine.Runtime.Animator)r0.GetComponent<XEngine.Runtime.Animator>();'
    'var clip = an.CurrentClip!;'
    'var sb = new System.Text.StringBuilder();'
    'sb.Append("clip=").Append(clip.Name).Append(" dur=").Append(clip.Duration.ToString("F2")).Append(" bones=").Append(clip.Bones.Count).Append("\\n");'
    'int dumped = 0;'
    'foreach (var b in clip.Bones) {'
    '  bool hasRot = b.RotX != null;'
    '  if (!hasRot || dumped >= 4) continue;'
    '  dumped++;'
    '  sb.Append(b.BoneName).Append(": RotX keys=");'
    '  var keys = b.RotX.Keys;'
    '  sb.Append(keys.Count).Append(" [");'
    '  int n2 = Math.Min(keys.Count, 3);'
    '  for (int k = 0; k < n2; k++) sb.Append(keys[k].Position.ToString("F2")).Append(":").Append(keys[k].Value.ToString("F3")).Append(" ");'
    '  var last = keys[keys.Count - 1];'
    '  sb.Append("... ").Append(last.Position.ToString("F2")).Append(":").Append(last.Value.ToString("F3")).Append("]\\n"); }'
    'return sb.ToString();')

proc = subprocess.Popen([EDITOR, "--serve", "--project", PROJECT],
                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                        cwd=os.path.dirname(EDITOR))
import threading
_responses = {}
elog = open(r"diag/zz_curves_stdio.log", "ab")
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

print("[CURVES]", send0(CODE)[:1200], flush=True)

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
