#!/usr/bin/env python3
"""Force Toon shader reimport, then validate animation visibility."""
import json, subprocess, time, os, re, shutil

EDITOR = r"F:\Git\XEngine\Build\Editor\Debug\net10.0\XEngine.Editor.exe"
PROJECT = r"F:\Git\XEngine\ZonezeroTestProject"
OUT = os.path.join(PROJECT, "diag")

proc = subprocess.Popen([EDITOR, "--serve", "--project", PROJECT],
    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    cwd=os.path.dirname(EDITOR))
import threading
resp = {}
elog = open(r"diag/zz_reimport_stdio.log", "ab")
def drain():
    try:
        for line in proc.stdout:
            elog.write(line); elog.flush()
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
    if not txt and err: txt = "ERR " + json.dumps(err)[:250]
    if label: print(f"[{label}] {txt[:400]}", flush=True)
    return txt
def shoot(name):
    i = send("runtime_screenshot", {"width":1280,"height":720})
    r = wait(i, 240)
    try:
        sc = (r or {}).get("result",{}).get("structuredContent") or {}
        p = sc.get("path")
        if isinstance(p,str) and p.endswith(".png"):
            dst = os.path.join(OUT, name)
            shutil.copyfile(p, dst)
            print(f"[shot] {dst}", flush=True)
    except Exception as e: print("shot err", e)

time.sleep(8)
for a in range(40):
    try:
        i = send("runtime_eval", {"code":'return "r";'})
        r = wait(i, 15)
        if r is not None: break
    except OSError: pass
    time.sleep(2)
print("[ready]", flush=True)

# Force reimport Toon + ToonOutline shaders
ev('''
var beT = System.AppDomain.CurrentDomain.GetAssemblies()
    .Select(a => a.GetType("XEngine.Editor.EditorAssetBackend")).First(x2 => x2 != null);
var be = beT.GetProperty("Instance").GetValue(null);
var ri = beT.GetMethod("Reimport", new[]{typeof(System.Guid)});
ri.Invoke(be, new object[]{System.Guid.Parse("24fb7ce8-7a2b-f354-8fb6-cc00130c521a")});
return "Toon reimported";''', "REIMPORT-TOON")
time.sleep(3)

# Verify shader loads and has SKINNED keyword support
ev('''
var shader = XEngine.Runtime.Resources.Shader.Load("Zonezero/Toon");
if (shader == null) return "shader null";
var sh = (XEngine.Runtime.Resources.Shader)shader;
return "shader=" + sh.Name + " passes=" + sh.PassCount;''', "SHADER-CHECK")

# Open scene and enter play
ev('return XEngine.Editor.GUI.SceneView.EditorSceneManager.OpenScene("Scenes/AnimSingleTest.scene");', "OPEN")
time.sleep(3)
i = send("runtime_playmode", {"action":"enter"}); wait(i, 300); time.sleep(6)

# Screenshot burst
for s in range(4):
    shoot(f"reimported-{s}.png")
    time.sleep(0.4)

# Check keyword state on the material at runtime
ev('''
var r0 = Scene.Current.RootObjects.Where(r => r.Name == "Test_CodeDriven").First();
XEngine.Runtime.SkinnedMeshRenderer? smr = null;
foreach (var c in r0.GetComponentsInChildren<XEngine.Runtime.SkinnedMeshRenderer>()) { smr = c; break; }
if (smr == null) return "no smr";
var mat = smr.Materials[0].Res;
if (mat == null) return "mat null";
var BF = System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.Instance;
object kw = "-";
foreach (var f in mat.GetType().GetFields(BF))
    if (f.Name.ToLower().Contains("keyword")) { kw = f.GetValue(mat); break; }
string kws = kw is System.Collections.IEnumerable e2 && kw is not string
    ? string.Join("|", e2.Cast<object>().Where(x2 => x2.ToString().Contains("True")).Select(x2 => x2.ToString()))
    : kw.ToString();
return "mat=" + mat.Name + " shader=" + (mat.Shader != null ? mat.Shader.Name : "null") + " trueKeywords=[" + kws + "]";''', "MAT-KW")

try:
    i = send("runtime_playmode", {"action":"exit"}); wait(i, 120)
except: pass
time.sleep(3)
proc.kill()
