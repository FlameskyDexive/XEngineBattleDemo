#!/usr/bin/env python3
"""Single-test: code-driven vs FSM-driven animation on two identical Corin instances."""
import json, subprocess, time, os, shutil

EDITOR = r"F:\Git\XEngine\Build\Editor\Debug\net10.0\XEngine.Editor.exe"
PROJECT = r"F:\Git\XEngine\ZonezeroTestProject"
OUT = r"F:\Git\XEngine\ZonezeroTestProject\diag"

import threading, queue
_responses = {}
proc = subprocess.Popen([EDITOR, "--serve", "--project", PROJECT],
                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                        cwd=os.path.dirname(EDITOR))
elog = open(r"diag/zz_single_stdio.log", "ab")
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
        if want_id in _responses:
            return _responses.pop(want_id)
        time.sleep(0.05)
    return None
def call(name, args, timeout=900):
    rid = send("tools/call", {"name": name, "arguments": args})
    r = recv(rid, timeout)
    sc = {}
    try: sc = ((r or {}).get("result") or {}).get("structuredContent") or {}
    except Exception: pass
    txt = json.dumps(sc.get("path") or sc.get("result") or sc.get("error") or sc)[:180]
    return txt, sc.get("path")
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
    print(f"[{label}] {txt[:300]}", flush=True)
    return txt
def shoot(name):
    rid = send("tools/call", {"name": "runtime_screenshot", "arguments": {"width": 1280, "height": 720}})
    r = recv(rid, 240)
    try:
        sc = ((r or {}).get("result") or {}).get("structuredContent") or {}
        p = sc.get("path")
        if isinstance(p, str) and p.endswith(".png"):
            dst = os.path.join(OUT, name)
            shutil.copyfile(p, dst)
            print(f"[shot] {dst}", flush=True)
    except Exception as e:
        print("shot err", e)

send("tools/call", {"name": "runtime_state", "arguments": {}})
proc.stdin.write(b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n'); proc.stdin.flush()
for attempt in range(40):
    time.sleep(3)
    st = send("tools/call", {"name": "runtime_state", "arguments": {}})
    if recv(st, 30) is not None:
        print(f"[ready after {(attempt+1)*3}s]", flush=True)
        break

ok, _ = call("runtime_menu", {"action": "invoke", "path": "Zonezero/Build Anim Single Test"})
print("[build]", ok[:120], flush=True)
time.sleep(4)

rid = send("tools/call", {"name": "runtime_playmode", "arguments": {"action": "enter"}})
recv(rid, 300); time.sleep(5)

# Drive BOTH sides to Run.
ev("CODE-RUN", '''
var r0 = Scene.Current.RootObjects.Where(r => r.Name == "Test_CodeDriven").First();
var an = (XEngine.Runtime.Animator)r0.GetComponent<XEngine.Runtime.Animator>();
var clip = an.CurrentClip;
an.Controller = default;
an.Wrap = XEngine.Runtime.AnimationWrapMode.Loop;
var c = (XEngine.Runtime.AnimationClip)an.CurrentClip!;
an.Play(c, 0f);
"code playing " + c.Name''')
ev("FSM-RUN", '''
var r1 = Scene.Current.RootObjects.Where(r => r.Name == "Test_FsmDriven").First();
var an1 = (XEngine.Runtime.Animator)r1.GetComponent<XEngine.Runtime.Animator>();
var rt = an1.Runtime;
rt == null ? "fsm null" : (rt.Play(XEngine.Animation.AnimationNameHash.Hash("Run"), 0f) ? "fsm playing Run" : "fsm Run missing")''')

HAND = (
'var sb4 = new System.Text.StringBuilder();'
'foreach (var rn in new[]{"Test_CodeDriven","Test_FsmDriven"}) {'
'  var rr = Scene.Current.RootObjects.Where(r2 => r2.Name == rn).First();'
'  var aa = (XEngine.Runtime.Animator)rr.GetComponent<XEngine.Runtime.Animator>();'
'  var sk2 = aa.Skeleton;'
'  XEngine.Vector.Transform? hd = null;'
'  for (int i2 = 0; i2 < sk2.Bones.Length; i2++) {'
'    var b2 = sk2.Bones[i2];'
'    if (b2 != null && b2.GameObject != null && b2.GameObject.Name.Contains("R Hand")) { hd = b2; break; } }'
'  var w2 = hd != null ? hd.Position : new XEngine.Vector.Float3(999,999,999);'
'  var info = aa.GetCurrentAnimatorStateInfo();'
'  sb4.Append(rn.Substring(5, 4)).Append(" nT=").Append(info.normalizedTime.ToString("F2"))'
'     .Append(" handY=").Append(w2.Y.ToString("F3")).Append(" handZ=").Append(w2.Z.ToString("F3")).Append("\\n"); }'
'return sb4.ToString();')

print("--- pose sampling (hand world Y/Z should swing while running)", flush=True)
for i in range(10):
    ev(f"s{i}", HAND)
    if i in (1, 5):
        shoot(f"single-{i}.png")
    time.sleep(0.45)

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
