#!/usr/bin/env python3
"""L2 forensics: does animation pose reach the skeleton transforms?"""
import json, subprocess, time, os

EDITOR = r"F:\Git\XEngine\Build\Editor\Debug\net10.0\XEngine.Editor.exe"
PROJECT = r"F:\Git\XEngine\ZonezeroTestProject"

MESHCHK = (
'var r0 = Scene.Current.RootObjects.Where(r => r.Name.StartsWith("Battle_Ally")).First();'
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
'return "mesh=" + mesh.Name + " hasBI=" + mesh.HasBoneIndices + "(" + biN + ") hasBW=" + mesh.HasBoneWeights + "(" + bwN + ")"
     + " bindPoses=" + (bp != null ? bp.Length : -1)
     + " smrBones=" + (smrBones != null ? smrBones.Length : -1) + " skBones=" + skBones.Length
     + " sameFirstBone=" + (sameFirst ? 1 : 0);')

proc = subprocess.Popen([EDITOR, "--serve", "--project", PROJECT],
                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                        cwd=os.path.dirname(EDITOR))
_id = 0
def send(method, params=None):
    global _id
    _id += 1
    msg = {"jsonrpc": "2.0", "id": _id, "method": method}
    if params: msg["params"] = params
    proc.stdin.write(json.dumps(msg).encode() + b"\n"); proc.stdin.flush()
    return _id
def recv(want_id, timeout=600):
    dl = time.time() + timeout
    while time.time() < dl:
        raw = proc.stdout.readline()
        if not raw: return None
        line = raw.decode("utf-8", errors="replace").strip()
        if not line: continue
        try: m = json.loads(line)
        except json.JSONDecodeError: continue
        if m.get("id") == want_id: return m
    return None
def ev(label, code, timeout=240):
    rid = send("tools/call", {"name": "runtime_eval", "arguments": {"code": code}})
    r = recv(rid, timeout)
    txt = ""
    try:
        c = ((r or {}).get("result") or {}).get("content") or []
        txt = "".join(x.get("text") or "" for x in c)
    except Exception:
        pass
    err = (r or {}).get("error")
    if not txt and err: txt = "ERR " + json.dumps(err)[:250]
    print(f"[{label}] {txt[:400]}", flush=True)
    return txt

send("tools/call", {"name": "runtime_state", "arguments": {}})
proc.stdin.write(b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n'); proc.stdin.flush()
time.sleep(42)

ev("OPEN", 'return XEngine.Editor.GUI.SceneView.EditorSceneManager.OpenScene("Scenes/ZonezeroBattle.scene");', 240)
time.sleep(3)
rid0 = send("tools/call", {"name": "runtime_playmode", "arguments": {"action": "enter"}})
recv(rid0, 300); time.sleep(6)

prev = None
for i in range(8):
    out = ev(f"B{i}", BONE)
    import re
    mm = re.search(r"lp=\((-?[\d.]+),(-?[\d.]+),(-?[\d.]+)\)", out)
    moved = ""
    if mm and prev:
        vals = [float(mm.group(j)) for j in range(1, 4)]
        d = max(abs(vals[j] - prev[j]) for j in range(3)) if len(prev) == 3 else 0
        moved = f"  Δpelvis={d:.5f}" + (" MOVING" if d > 1e-4 else " FROZEN")
        # overwrite: prettify only on diff lines
        print(moved, flush=True)
    if mm: prev = [float(mm.group(j)) for j in range(1, 4)]
    time.sleep(0.45)

ev("SMR", SMR)

try:
    ridx = send("tools/call", {"name": "runtime_playmode", "arguments": {"action": "exit"}})
    recv(ridx, 120)
except Exception as e:
    print("[exit]", e)
finally:
    try:
        proc.stdin.close(); proc.wait(timeout=15)
    except Exception:
        proc.kill()
