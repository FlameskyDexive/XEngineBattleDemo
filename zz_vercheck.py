#!/usr/bin/env python3
"""Check RecomputeSkinning is actually running: read _lastSkeletonVersion and boneTexture state."""
import json, subprocess, time, os

EDITOR = r"F:\Git\XEngine\Build\Editor\Debug\net10.0\XEngine.Editor.exe"
PROJECT = r"F:\Git\XEngine\ZonezeroTestProject"

CODE = (
    'var r0 = Scene.Current.RootObjects.Where(r => r.Name == "Test_CodeDriven").First();'
    'var an = (XEngine.Runtime.Animator)r0.GetComponent<XEngine.Runtime.Animator>();'
    'var sk = an.Skeleton;'
    'XEngine.Runtime.SkinnedMeshRenderer? smr = null;'
    'foreach (var c in r0.GetComponentsInChildren<XEngine.Runtime.SkinnedMeshRenderer>()) { smr = c; break; }'
    'if (smr == null) return "no smr";'
    'var BF = System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.Instance;'
    'var lastVerF = typeof(XEngine.Runtime.SkinnedMeshRenderer).GetField("_lastSkeletonVersion", BF);'
    'ulong lastVer = lastVerF != null ? (ulong)lastVerF.GetValue(smr)! : 0;'
    'ulong currentVer = 0;'
    'var bones = smr.Bones;'
    'for (int i = 0; i < bones.Length; i++) { if (bones[i] != null) currentVer += bones[i].Version; }'
    'for (var t = smr.Transform; t != null; t = t.Parent) currentVer += t.Version;'
    'var texF = typeof(XEngine.Runtime.SkinnedMeshRenderer).GetField("_boneTexture", BF);'
    'var tex = texF?.GetValue(smr);'
    'var info = an.GetCurrentAnimatorStateInfo();'
    'XEngine.Vector.Float3 handPos = new XEngine.Vector.Float3(0,0,0);'
    'for (int i = 0; i < sk.Bones.Length; i++) { var b = sk.Bones[i]; if (b?.GameObject != null && b.GameObject.Name.Contains("R Hand")) { handPos = b.Position; break; } }'
    'return "lastVer=" + lastVer + " curVer=" + currentVer + " tex=" + (tex != null ? "EXISTS" : "NULL")'
    '     + " nT=" + info.normalizedTime.ToString("F2") + " handZ=" + handPos.Z.ToString("F2");')

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
    err = (r or {}).get("error")
    if not txt and err: txt = "ERR " + json.dumps(err)[:250]
    if label: print(f"[{label}] {txt[:400]}", flush=True)
    return txt

time.sleep(8)
for a in range(30):
    try:
        i = send("runtime_eval", {"code":'return "r";'})
        r = wait(i, 15)
        if r is not None: break
    except OSError: pass
    time.sleep(2)

ev('return XEngine.Editor.GUI.SceneView.EditorSceneManager.OpenScene("Scenes/AnimSingleTest.scene");', "OPEN")
time.sleep(3)
send("runtime_playmode", {"action":"enter"})
time.sleep(6)

for s in range(3):
    ev(CODE, f"V{s}")
    time.sleep(0.5)

proc.kill()
