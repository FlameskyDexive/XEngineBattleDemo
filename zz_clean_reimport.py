#!/usr/bin/env python3
"""Clean reimport: delete ZZZ cache + reimport all FBX with correct importer code."""
import json, subprocess, time, os, shutil, glob, re

EDITOR = r"F:\Git\XEngine\Build\Editor\Debug\net10.0\XEngine.Editor.exe"
PROJECT = r"F:\Git\XEngine\ZonezeroTestProject"
CACHE = os.path.join(PROJECT, "Library", "cache")

# Step 0: Kill any running editor
os.system("taskkill /F /IM XEngine.Editor.exe >nul 2>&1")
time.sleep(2)

# Step 1: Delete cached model assets for ZZZ FBX files
deleted = 0
for f in glob.glob(os.path.join(PROJECT, "Assets", "ZZZ", "**", "*.FBX"), recursive=True):
    meta_path = f + ".meta"
    if not os.path.exists(meta_path): continue
    m = re.search(r'"guid":\s*"([0-9a-f-]{36})"', open(meta_path, encoding="utf-8").read())
    if not m: continue
    guid = m.group(1)
    cache_file = os.path.join(CACHE, guid + ".asset")
    if os.path.exists(cache_file):
        os.remove(cache_file)
        deleted += 1
print(f"[cache] deleted {deleted} cached model assets", flush=True)

# Step 2: Delete the loaded snapshot to force recompile check
loaded_dir = os.path.join(PROJECT, "Library", "ScriptAssemblies", ".loaded")
if os.path.exists(loaded_dir):
    shutil.rmtree(loaded_dir)
    print("[cache] removed .loaded snapshot", flush=True)

# Step 3: Boot editor and reimport
proc = subprocess.Popen([EDITOR, "--serve", "--project", PROJECT],
    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    cwd=os.path.dirname(EDITOR))
import threading
resp = {}
elog = open(r"diag/zz_cleanreio_stdio.log", "ab")
def _drain():
    try:
        for line in proc.stdout:
            elog.write(line); elog.flush()
            s2 = line.decode("utf-8", errors="replace").strip()
            if not s2: continue
            try: m = json.loads(s2)
            except: continue
            if isinstance(m.get("id"), int): resp[m["id"]] = m
    except Exception: pass
threading.Thread(target=_drain, daemon=True).start()

_nid = [0]
def send(mn, pm=None):
    _nid[0] += 1
    msg = {"jsonrpc":"2.0","id":_nid[0],"method":"tools/call","params":{"name":mn,"arguments":pm or {}}}
    proc.stdin.write(json.dumps(msg).encode()+b"\n"); proc.stdin.flush()
    return _nid[0]
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
    if label: print(f"[{label}] {txt[:300]}", flush=True)
    return txt

# Boot: poll until ready
time.sleep(5)
for a in range(60):
    try:
        i = send("runtime_eval", {"code":'return "r";'})
        r = wait(i, 15)
        if r is not None:
            print(f"[editor ready after {5+(a+1)*3}s]", flush=True)
            break
    except OSError:
        print(f"[pipe {a}]", flush=True)
    time.sleep(2)

# Reimport all FBX
fbxs = sorted(glob.glob(os.path.join(PROJECT, "Assets", "ZZZ", "**", "*.FBX"), recursive=True))
guids = []
for f in fbxs:
    m = re.search(r'"guid":\s*"([0-9a-f-]{36})"', open(f + ".meta", encoding="utf-8").read())
    if m: guids.append(m.group(1))
print(f"[fbx] {len(guids)} files", flush=True)

guid_str = ", ".join(f'System.Guid.Parse("{g}")' for g in guids)
ev(f'''
var beT = System.AppDomain.CurrentDomain.GetAssemblies()
    .Select(a => a.GetType("XEngine.Editor.EditorAssetBackend")).First(x2 => x2 != null);
var be = beT.GetProperty("Instance").GetValue(null);
var gs = new System.Guid[] {{ {guid_str} }};
foreach (var g4 in gs)
    beT.GetMethod("Reimport", new[]{{typeof(System.Guid)}}).Invoke(be, new object[]{{g4}});
return "reimported " + gs.Length;''', "REIMPORT-ALL")
time.sleep(2)

# Rebuild prefabs + controllers from the freshly imported models
print("[menu Generate Native Assets]", flush=True)
send("runtime_menu", {"action":"invoke","path":"Zonezero/Generate Native Assets"})
time.sleep(5)

# Verify: check a curve from the Corin Run clip
ev("VERIFY-CURVES", '''
var rf = new XEngine.Runtime.AssetRef<XEngine.Animation.AnimatorController>(
    System.Guid.Parse("58c28ec0-40bc-4ce5-82a7-132f6165880a"));
rf.EnsureLoaded();
var ctrl = rf.Res as XEngine.Animation.AnimatorController;
if (ctrl == null) return "ctrl null";
var runState = ctrl.States.FirstOrDefault(s => s.Name == "Run");
if (runState == null) return "no Run state";
runState.Motion.EnsureLoaded();
var clip = runState.Motion.Res as XEngine.Runtime.AnimationClip;
if (clip == null) return "clip null";
var thigh = clip.Bones.FirstOrDefault(b2 => b2.BoneName.Contains("L Thigh"));
if (thigh == null || thigh.RotX == null) return "no thigh RotX";
var keys = thigh.RotX.Keys;
float mn = float.MaxValue, mx = float.MinValue;
foreach (var k in keys) { if (k.Value < mn) mn = k.Value; if (k.Value > mx) mx = k.Value; }
return "clip=" + clip.Name + " keys=" + keys.Count + " range=[" + mn.ToString("F4") + "," + mx.ToString("F4") + "]";''', 300)

# Build single test scene and verify visible animation
print("[menu Build Anim Single Test]", flush=True)
send("runtime_menu", {"action":"invoke","path":"Zonezero/Build Anim Single Test"})
time.sleep(4)

# Enter play and sample
my = send("runtime_playmode", {"action":"enter", })
wait(my, 300)
time.sleep(8)

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
'     .Append(" nT=").Append(info2.normalizedTime.ToString("F2")).Append("\\n"); }\n'
'return sbP.ToString();')

for s in range(8):
    ev(f"S{s}", HAND, f"S{s}")
    time.sleep(0.3)

# Screenshot
my = send("runtime_screenshot", {"arguments": {"width":1280,"height":720}})
r = wait(my, 240)
try:
    sc = (r or {}).get("result",{}).get("structuredContent") or {}
    pp = sc.get("path")
    if isinstance(pp,str) and pp.endswith(".png"):
        dst = os.path.join(OUT, "final-clean-reimport.png")
        shutil.copyfile(pp, dst)
        print(f"[shot] {dst}", flush=True)
except: pass

# Exit play
try:
    send("runtime_playmode", {"action":"exit"})
except: pass
time.sleep(5)

try:
    proc.stdin.close(); proc.wait(timeout=15)
except Exception:
    proc.kill()
