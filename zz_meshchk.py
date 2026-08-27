#!/usr/bin/env python3
"""Check mesh skin vertex data on the ally's skinned mesh."""
import json, subprocess, time, os

EDITOR = r"F:\Git\XEngine\Build\Editor\Debug\net10.0\XEngine.Editor.exe"
PROJECT = r"F:\Git\XEngine\ZonezeroTestProject"

MESHCHK = (
    'var r0 = Scene.Current.RootObjects.Where(r => r.Name.StartsWith("Battle_Ally")).FirstOrDefault();'
    'if (r0 == null) return "no ally root; scene=" + Scene.Current.Name + " roots=" + string.Join(",", Scene.Current.RootObjects.Select(r2 => r2.Name).Take(20));'
    'XEngine.Runtime.SkinnedMeshRenderer? smr = null;'
    'foreach (var c in r0.GetComponentsInChildren<XEngine.Runtime.SkinnedMeshRenderer>()) { smr = c; break; }'
    'var mesh = smr!.SharedMesh.Res!;'
    'var BF = System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.Public;'
    'var bi = mesh.GetType().GetField("boneIndices", BF)?.GetValue(mesh);'
    'var bw = mesh.GetType().GetField("boneWeights", BF)?.GetValue(mesh);'
    'int biN = bi == null ? 0 : ((System.Array)bi).Length;'
    'int bwN = bw == null ? 0 : ((System.Array)bw).Length;'
    'var bp = mesh.BindPoses;'
    'var smrBones = smr.Bones;'
    'var an = (XEngine.Runtime.Animator)r0.GetComponent<XEngine.Runtime.Animator>();'
    'var skBones = an.Skeleton!.Bones;'
    'bool sameFirst = smrBones != null && smrBones.Length > 0 && skBones.Length > 0 && ReferenceEquals(smrBones[0], skBones[0]);'
    'return "scene=" + Scene.Current.Name + " mesh=" + mesh.Name + " hasBI=" + mesh.HasBoneIndices + "(" + biN + ") hasBW=" + mesh.HasBoneWeights + "(" + bwN + ")"'
    '     + " bindPoses=" + (bp != null ? bp.Length : -1)'
    '     + " smrBones=" + (smrBones != null ? smrBones.Length : -1) + " skBones=" + skBones.Length'
    '     + " sameFirstBone=" + (sameFirst ? 1 : 0);')

proc = subprocess.Popen([EDITOR, "--serve", "--project", PROJECT],
                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                        cwd=os.path.dirname(EDITOR))
editor_log = open(r"diag/zz_editor_stdio.log", "ab")
import threading
def _drain():
    try:
        for line in proc.stdout:
            editor_log.write(line); editor_log.flush()
            line = line.decode("utf-8", errors="replace").strip()
            if not line: continue
            try: m = json.loads(line)
            except json.JSONDecodeError: continue
            if isinstance(m.get("id"), int):
                _responses[m["id"]] = m
    except Exception:
        pass
threading.Thread(target=_drain, daemon=True).start()
def on_proc_exit():
    print("[EDITOR PROCESS DIED] pid", proc.pid, flush=True)
import threading
def watch():
    proc.wait()
    on_proc_exit()
threading.Thread(target=watch, daemon=True).start()
_id = 0
def send(method, params=None):
    global _id
    _id += 1
    msg = {"jsonrpc": "2.0", "id": _id, "method": method}
    if params: msg["params"] = params
    proc.stdin.write(json.dumps(msg).encode() + b"\n"); proc.stdin.flush()
    return _id
import queue
_responses = {}
def recv(want_id, timeout=600):
    dl = time.time() + timeout
    while time.time() < dl:
        if want_id in _responses:
            return _responses.pop(want_id)
        time.sleep(0.05)
    return None

send("tools/call", {"name": "runtime_state", "arguments": {}})
proc.stdin.write(b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n'); proc.stdin.flush()
# Poll until the editor answers a trivial eval (project fully initialized).
for attempt in range(40):
    time.sleep(2)
    try:
        st = send("tools/call", {"name": "runtime_state", "arguments": {}})
        rs = recv(st, 30)
        if rs is not None:
            print(f"[project ready after {(attempt+1)*5}s]", flush=True)
            break
    except OSError:
        print(f"[boot race {attempt}]", flush=True)

op = send("tools/call", {"name": "runtime_eval", "arguments": {"code": 'return XEngine.Editor.GUI.SceneView.EditorSceneManager.OpenScene("Scenes/ZonezeroBattle.scene");'}})
r_open = recv(op, 240)
print("[open]", json.dumps(r_open)[:150], flush=True)
time.sleep(3)
# verify scene actually opened
op2 = send("tools/call", {"name": "runtime_eval", "arguments": {"code": 'return Scene.Current.Name;'}})
print("[scene]", json.dumps(recv(op2, 60))[:150], flush=True)

pm = send("tools/call", {"name": "runtime_playmode", "arguments": {"action": "enter"}})
recv(pm, 300)
time.sleep(6)

ev = send("tools/call", {"name": "runtime_eval", "arguments": {"code": MESHCHK}})
r = recv(ev, 240)
txt = ""
try:
    c = ((r or {}).get("result") or {}).get("content") or []
    txt = "".join(x.get("text") or "" for x in c)
except Exception:
    pass
err = (r or {}).get("error")
print("[MESHCHK]", txt[:500] if txt else json.dumps(err)[:300], flush=True)

try:
    proc.stdin.close(); proc.wait(timeout=15)
except Exception:
    proc.kill()
try:
    px = send("tools/call", {"name": "runtime_playmode", "arguments": {"action": "exit"}})
    recv(px, 120)
except Exception as e:
    print("[exit]", e)
finally:
    try:
        proc.stdin.close(); proc.wait(timeout=15)
    except Exception:
        proc.kill()
