#!/usr/bin/env python3
"""Verify: is the animator-bound bone transform parented under the visible scene root?"""
import json, subprocess, time, os

EDITOR = r"F:\Git\XEngine\Build\Editor\Debug\net10.0\XEngine.Editor.exe"
PROJECT = r"F:\Git\XEngine\ZonezeroTestProject"

CODE = (
    'var r0 = Scene.Current.RootObjects.Where(r => r.Name == "Test_CodeDriven").First();'
    'var an = (XEngine.Runtime.Animator)r0.GetComponent<XEngine.Runtime.Animator>();'
    'var sk = an.Skeleton;'
    'XEngine.Vector.Transform? hand = null;'
    'for (int i = 0; i < sk.Bones.Length; i++) {'
    '  var b = sk.Bones[i];'
    '  if (b != null && b.GameObject != null && b.GameObject.Name.Contains("R Hand")) { hand = b; break; } }'
    'if (hand == null) return "no hand";'
    'var chain = new System.Text.StringBuilder();'
    'var t = hand.GameObject.Transform;'
    'int depth = 0;'
    'while (t != null && depth < 12) {'
    '  chain.Append(t.GameObject != null ? t.GameObject.Name : "?").Append(" < ");'
    '  t = t.Parent; depth++; }'
    'var hr = hand.GameObject.Transform.Parent != null && hand.GameObject.Transform.Parent.GameObject != null ? hand.GameObject.Transform.Parent.GameObject.Name : "-";'
    'var lr = hand.LocalRotation;'
    'return "parentChain=[ " + chain + "] | lr=(" + lr.X.ToString("F3") + "," + lr.Y.ToString("F3") + "," + lr.Z.ToString("F3") + "," + lr.W.ToString("F3") + ")";')

proc = subprocess.Popen([EDITOR, "--serve", "--project", PROJECT],
                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                        cwd=os.path.dirname(EDITOR))
import threading
_responses = {}
elog = open(r"diag/zz_parent_stdio.log", "ab")
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
rid0 = None
proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 999001, "method": "tools/call",
    "params": {"name": "runtime_playmode", "arguments": {"action": "enter"}}}).encode() + b"\n")
proc.stdin.flush()
time.sleep(8)

print("[CHAIN]", send0(CODE)[:800], flush=True)

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
