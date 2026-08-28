#!/usr/bin/env python3
"""Final lean validation: build single test, play, sample hand positions."""
import json, subprocess, time, os

EDITOR = r"F:\Git\XEngine\Build\Editor\Debug\net10.0\XEngine.Editor.exe"
PROJECT = r"F:\Git\XEngine\ZonezeroTestProject"

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
def send(mn, pm):
    _n[0] += 1
    msg = {"jsonrpc":"2.0","id":_n[0],"method":"tools/call","params":{"name":mn,"arguments":pm}}
    proc.stdin.write(json.dumps(msg).encode()+b"\n"); proc.stdin.flush()
    return _n[0]

def wait(i, to=300):
    dl=time.time()+to
    while time.time()<dl:
        if i in resp: return resp.pop(i)
        time.sleep(0.05)
    return None

def ev(code, label=""):
    i = send("runtime_eval", {"code":code})
    r = wait(i)
    txt = ""
    try:
        txt = "".join(x.get("text") or "" for x in (r.get("result") or {}).get("content") or [])
    except: pass
    if not txt:
        try: txt = json.dumps((r or {}).get("error") or r)[:200]
        except: txt = str(r)[:200]
    if label: print(f"[{label}] {txt[:250]}", flush=True)
    return txt

def menu(p):
    i = send("runtime_menu", {"action":"invoke","path":p})
    wait(i, 900)
    print(f"[menu {p}]", flush=True)

# Boot
time.sleep(10)
for a in range(30):
    try:
        i = send("runtime_eval", {"code":'return "r";'})
        r = wait(i, 15)
        if r: break
    except: pass
    time.sleep(3)
print("[editor ready]", flush=True)

# Reimport all ZZZ FBX with the fixed unitScale bone hierarchy scaling
import glob, re as _re
fbxs = sorted(glob.glob(r"F:/Git/XEngine/ZonezeroTestProject/Assets/ZZZ/**/*.FBX", recursive=True))
fbx_guids = []
for f in fbxs:
    m2 = _re.search(r'"guid":\s*"([0-9a-f-]{36})"', open(f + ".meta", encoding="utf-8").read())
    if m2: fbx_guids.append(m2.group(1))
print(f"[reimport] {len(fbx_guids)} FBX files", flush=True)
ev('''
var beT = System.AppDomain.CurrentDomain.GetAssemblies()
    .Select(a => a.GetType("XEngine.Editor.EditorAssetBackend")).First(x2 => x2 != null);
var be = beT.GetProperty("Instance").GetValue(null);
var gs = new System.Guid[] { ''' + ", ".join(f'System.Guid.Parse("{g}")' for g in fbx_guids) + ''' };
foreach (var g3 in gs)
    beT.GetMethod("Reimport", new[]{typeof(System.Guid)}).Invoke(be, new object[]{g3});
return "reimported " + gs.Length;''', "REIMPORT")
time.sleep(2)

# Build
menu("Zonezero/Build Anim Single Test"); time.sleep(4)

# Play
i = send("runtime_playmode", {"action":"enter"}); wait(i, 300); time.sleep(8)

# Drive both to Run
drive_code = (
'var sbF = new System.Text.StringBuilder();\n'
'foreach (var rn2 in new[]{"Test_CodeDriven","Test_FsmDriven"}) {\n'
'  var rr = Scene.Current.RootObjects.Where(r3 => r3.Name == rn2).First();\n'
'  var aa = (XEngine.Runtime.Animator)rr.GetComponent<XEngine.Runtime.Animator>();\n'
'  if (rn2 == "Test_CodeDriven") {\n'
'    var c2 = (XEngine.Runtime.AnimationClip)aa.CurrentClip!;\n'
'    aa.Play(c2, 0f);\n'
'  } else {\n'
'    aa.Runtime!.Play(XEngine.Animation.AnimationNameHash.Hash("Run"), 0f);\n'
'  }\n'
'  sbF.Append(rn2).Append(" ok\\n"); }\n'
'return sbF.ToString();')
ev("DRIVE", drive_code)

# Sample hand positions
HAND = (
'var sbP = new System.Text.StringBuilder();\n'
'foreach (var rn3 in new[]{"Test_CodeDriven","Test_FsmDriven"}) {\n'
'  var rr = Scene.Current.RootObjects.Where(r4 => r4.Name == rn3).First();\n'
'  var aa = (XEngine.Runtime.Animator)rr.GetComponent<XEngine.Runtime.Animator>();\n'
'  var sk3 = aa.Skeleton;\n'
'  XEngine.Vector.Transform? hp = null;\n'
'  for (int i3 = 0; i3 < sk3.Bones.Length; i3++) {\n'
'    var b3 = sk3.Bones[i3];\n'
'    if (b3 != null && b3.GameObject != null && b3.GameObject.Name.Contains("R Hand")) { hp = b3; break; } }\n'
'  var w3 = hp != null ? hp.Position : new XEngine.Vector.Float3(999,999,999);\n'
'  var info2 = aa.GetCurrentAnimatorStateInfo();\n'
'  sbP.Append(rn3.Substring(5,4)).Append(" handY=").Append(w3.Y.ToString("F4"))\n'
'     .Append(" handZ=").Append(w3.Z.ToString("F4"))\n'
'     .Append(" nT=").Append(info2.normalizedTime.ToString("F2")).Append("\\n"); }\n'
'return sbP.ToString();')

print("--- pose sampling", flush=True)
for s in range(8):
    ev(HAND, f"S{s}")
    if s in (2, 5):
        my = _n[0] + 1
        proc.stdin.write(json.dumps({"jsonrpc":"2.0","id":my,"method":"tools/call",
            "params":{"name":"runtime_screenshot","arguments":{"width":1280,"height":720}}}).encode()+b"\n")
        time.sleep(2)
    time.sleep(0.3)

# Exit
i = send("runtime_playmode", {"action":"exit"}); wait(i, 120)
proc.kill()
