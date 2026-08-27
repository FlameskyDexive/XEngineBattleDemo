#!/usr/bin/env python3
"""A/B: same bone-rotation sampling on the OLD combat scene."""
import json, subprocess, time, os

EDITOR = r"F:\Git\XEngine\Build\Editor\Debug\net10.0\XEngine.Editor.exe"
PROJECT = r"F:\Git\XEngine\ZonezeroTestProject"

HAND = (
'var r0 = Scene.Current.RootObjects.Where(r => r.Name == "Corin").First();'
'var an = (XEngine.Runtime.Animator)r0.GetComponent<XEngine.Runtime.Animator>();'
'var sk = an.Skeleton;'
'XEngine.Vector.Transform? hd = null;'
'for (int i = 0; i < sk.Bones.Length; i++) {'
'  var b = sk.Bones[i];'
'  if (b != null && b.GameObject != null && b.GameObject.Name.Contains("R Hand")) { hd = b; break; } }'
'var w = hd != null ? hd.Position : new XEngine.Vector.Float3(999,999,999);'
'var rq = hd != null ? hd.LocalRotation : new XEngine.Vector.Quaternion(0,0,0,1);'
'var info = an.GetCurrentAnimatorStateInfo();'
'bool parented = hd != null && hd.GameObject.Transform.Parent != null;'
'var pq = hd != null && hd.GameObject.Transform.Parent != null ? hd.GameObject.Transform.Parent : null;'
'return "fsm=" + (an.Runtime != null) + " nT=" + info.normalizedTime.ToString("F3")'
'     + " parented=" + (parented ? 1 : 0) + " parentName=" + (pq != null ? pq.GameObject.Name : "-")'
'     + " rq=(" + rq.X.ToString("F3") + "," + rq.Y.ToString("F3") + "," + rq.Z.ToString("F3") + "," + rq.W.ToString("F3") + ")"'
'     + " wpos=(" + w.X.ToString("F3") + "," + w.Y.ToString("F3") + "," + w.Z.ToString("F3") + ")";')

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
    print(f"[{label}] {txt[:260]}", flush=True)
    return txt

send("tools/call", {"name": "runtime_state", "arguments": {}})
proc.stdin.write(b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n'); proc.stdin.flush()
time.sleep(60)

ev("OPEN-OLD", 'return XEngine.Editor.GUI.SceneView.EditorSceneManager.OpenScene("Scenes/ZonezeroCombat.scene");', 240)
time.sleep(3)
rid0 = send("tools/call", {"name": "runtime_playmode", "arguments": {"action": "enter"}})
recv(rid0, 300); time.sleep(6)

for i in range(6):
    ev(f"old{i}", HAND)
    time.sleep(0.5)

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
