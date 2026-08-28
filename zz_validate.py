#!/usr/bin/env python3
"""Validate animation: build single test, play, sample hand Y/Z."""
import json, subprocess, time, os, glob, re

EDITOR = r"F:\Git\XEngine\Build\Editor\Debug\net10.0\XEngine.Editor.exe"
PROJECT = r"F:\Git\XEngine\ZonezeroTestProject"
CACHE = os.path.join(PROJECT, "Library", "cache")

deleted = 0
for f in glob.glob(os.path.join(PROJECT, "Assets", "ZZZ", "**", "*.FBX"), recursive=True):
    mp = f + ".meta"
    if not os.path.exists(mp): continue
    m = re.search(r'"guid":\s*"([0-9a-f-]{36})"', open(mp, encoding="utf-8").read())
    if not m: continue
    cf = os.path.join(CACHE, m.group(1) + ".asset")
    if os.path.exists(cf): os.remove(cf); deleted += 1
print(f"[cache] deleted {deleted}", flush=True)

proc = subprocess.Popen([EDITOR, "--serve", "--project", PROJECT],
    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    cwd=os.path.dirname(EDITOR))
import threading
resp = {}
def drain():
    try:
        for line in proc.stdout:
            s2 = line.decode("utf-8", errors="replace").strip()
            if not s2: continue
            try: m = json.loads(s2)
            except: continue
            if isinstance(m.get("id"), int): resp[m["id"]] = m
    except: pass
threading.Thread(target=drain, daemon=True).start()
_n = [0]
def send(mn, pm=None):
    _n[0] += 1
    msg = {"jsonrpc":"2.0","id":_n[0],"method":"tools/call","params":{"name":mn,"arguments":pm or {}}}
    proc.stdin.write(json.dumps(msg).encode()+b"\n"); proc.stdin.flush()
    return _n[0]
def wait(i, to=600):
    dl=time.time()+to
    while time.time()<dl:
        if i in resp: return resp.pop(i)
        time.sleep(0.1)
    return None

def ev(code, label=""):
    i = send("runtime_eval", {"code":code})
    r = wait(i)
    txt = ""
    try:
        txt = "".join(x.get("text") or "" for x in (r.get("result") or {}).get("content") or [])
    except: pass
    err = (r or {}).get("error")
    if not txt and err: txt = "ERR " + json.dumps(err)[:200]
    if label: print(f"[{label}] {txt[:350]}", flush=True)
    return txt

def menu(p):
    i = send("runtime_menu", {"action":"invoke","path":p})
    wait(i, 900)
    print(f"[menu {p}]", flush=True)

time.sleep(8)
for a in range(30):
    try:
        i = send("runtime_eval", {"code":'return "r";'})
        r = wait(i, 15)
        if r is not None: break
    except OSError: pass
    time.sleep(2)
print("[ready]", flush=True)

menu("Zonezero/Generate Native Assets"); time.sleep(5)
menu("Zonezero/Build Anim Single Test"); time.sleep(4)

i = send("runtime_playmode", {"action":"enter"}); wait(i, 300); time.sleep(8)

HAND = (
    'var sbP = new System.Text.StringBuilder();'
    'foreach (var rn3 in new[]{"Test_CodeDriven","Test_FsmDriven"}) {'
    '  var rr = Scene.Current.RootObjects.Where(r4 => r4.Name == rn3).First();'
    '  var aa = (XEngine.Runtime.Animator)rr.GetComponent<XEngine.Runtime.Animator>();'
    '  var sk3 = aa.Skeleton;'
    '  XEngine.Vector.Transform? hp = null;'
    '  for (int i3 = 0; i3 < sk3.Bones.Length; i3++) {'
    '    var b3 = sk3.Bones[i3];'
    '    if (b3 != null && b3.GameObject != null && b3.GameObject.Name.Contains("R Hand")) { hp = b3; break; } }'
    '  var w3 = hp != null ? hp.Position : new XEngine.Vector.Float3(999,999,999);'
    '  var info2 = aa.GetCurrentAnimatorStateInfo();'
    '  sbP.Append(rn3.Substring(5,4)).Append(" handY=").Append(w3.Y.ToString("F4"))'
    '     .Append(" handZ=").Append(w3.Z.ToString("F4"))'
    '     .Append(" nT=").Append(info2.normalizedTime.ToString("F2")).Append("; "); }'
    'return sbP.ToString();')

for s in range(8):
    ev(HAND, f"S{s}")
    time.sleep(0.35)

try:
    i = send("runtime_playmode", {"action":"exit"}); wait(i, 120)
except: pass
time.sleep(3)
proc.kill()
