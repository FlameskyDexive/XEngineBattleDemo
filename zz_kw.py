#!/usr/bin/env python3
"""Read the runtime material keywords on the ally's skinned mesh material."""
import json, subprocess, time, os

EDITOR = r"F:\Git\XEngine\Build\Editor\Debug\net10.0\XEngine.Editor.exe"
PROJECT = r"F:\Git\XEngine\ZonezeroTestProject"

CODE = (
    'var r0 = Scene.Current.RootObjects.Where(r => r.Name.StartsWith("Battle_Ally")).First();'
    'XEngine.Runtime.SkinnedMeshRenderer? smr = null;'
    'foreach (var c in r0.GetComponentsInChildren<XEngine.Runtime.SkinnedMeshRenderer>()) { smr = c; break; }'
    'var mesh = smr!.SharedMesh.Res!;'
    'var mat = smr.Materials[0].Res!;'
    'var BF = System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.Public;'
    'object kw = "-";'
    'foreach (var f in mat.GetType().GetFields(BF)) {'
    '  if (f.Name.ToLower().Contains("keyword")) { kw = f.GetValue(mat); break; } }'
    'string kws = kw is System.Collections.IEnumerable e && kw is not string'
    '     ? string.Join("|", e.Cast<object>().Select(x2 => x2.ToString())) : kw.ToString();'
    'return "mat=" + mat.Name + " shader=" + mat.Shader.Name + " keywords=[" + kws + "]";')

proc = subprocess.Popen([EDITOR, "--serve", "--project", PROJECT],
                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                        cwd=os.path.dirname(EDITOR))
import threading, queue
_responses = {}
editor_log = open(r"diag/zz_kw_stdio.log", "ab")
def _drain():
    try:
        for line in proc.stdout:
            editor_log.write(line); editor_log.flush()
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
r_open = call_eval('return XEngine.Editor.GUI.SceneView.EditorSceneManager.OpenScene("Scenes/ZonezeroBattle.scene");')
print("[open]", r_open[:80], flush=True)
time.sleep(2)
pm = send0('return "entering";')
import threading as _t
def _enter():
    rid = None
    proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 999001, "method": "tools/call",
        "params": {"name": "runtime_playmode", "arguments": {"action": "enter"}}}).encode() + b"\n")
    proc.stdin.flush()
_t2 = _t.Thread(target=_enter, daemon=True); _t2.start()
time.sleep(10)

out = call_eval(CODE)
print("[KW]", out[:500], flush=True)

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
