#!/usr/bin/env python3
"""Check whether SMR skinning ever initialized: _boneTexture, _skinMatrices, _bones sample."""
import json, subprocess, time, os

EDITOR = r"F:\Git\XEngine\Build\Editor\Debug\net10.0\XEngine.Editor.exe"
PROJECT = r"F:\Git\XEngine\ZonezeroTestProject"

CODE = (
    'var r0 = Scene.Current.RootObjects.Where(r => r.Name.StartsWith("Test_")).First();'
    'XEngine.Runtime.SkinnedMeshRenderer? smr = null;'
    'foreach (var c in r0.GetComponentsInChildren<XEngine.Runtime.SkinnedMeshRenderer>()) { smr = c; break; }'
    'if (smr == null) return "no smr";'
    'var BF = System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.Instance;'
    'var bt = typeof(XEngine.Runtime.SkinnedMeshRenderer).GetField("_boneTexture", BF)?.GetValue(smr);'
    'var sm = typeof(XEngine.Runtime.SkinnedMeshRenderer).GetField("_skinMatrices", BF)?.GetValue(smr) as float[];'
    'var bones = smr.Bones;'
    'var b0 = bones != null && bones.Length > 0 ? bones[0] : null;'
    'var b0y = b0 != null ? b0.Position.Y.ToString("F3") : "-";'
    'var b1 = bones != null && bones.Length > 30 ? bones[30] : null;'
    'var b1n = b1 != null && b1.GameObject != null ? b1.GameObject.Name : "-";'
    'var b1y = b1 != null ? b1.Position.Y.ToString("F3") : "-";'
    'return "tex=" + (bt != null ? "created" : "NULL") + " mats=" + (sm != null ? sm.Length.ToString() : "null")'
    '     + " smrBones=" + (bones != null ? bones.Length.ToString() : "null")'
    '     + " b0=(" + (b0 != null ? b0.GameObject.Name : "-") + " y=" + b0y + ")"'
    '     + " b30=(" + b1n + " y=" + b1y + ")";')

proc = subprocess.Popen([EDITOR, "--serve", "--project", PROJECT],
                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                        cwd=os.path.dirname(EDITOR))
import threading
_responses = {}
elog = open(r"diag/zz_tex_stdio.log", "ab")
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
pm = None
proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 999001, "method": "tools/call",
    "params": {"name": "runtime_playmode", "arguments": {"action": "enter"}}}).encode() + b"\n")
proc.stdin.flush()
time.sleep(8)

print("[TEX]", send0(CODE)[:500], flush=True)

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
