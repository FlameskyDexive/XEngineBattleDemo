#!/usr/bin/env python3
"""Check if SMR bones and Animator skeleton share the same Transform instances."""
import json, subprocess, time, os

EDITOR = r"F:\Git\XEngine\Build\Editor\Debug\net10.0\XEngine.Editor.exe"
PROJECT = r"F:\Git\XEngine\ZonezeroTestProject"

CODE = (
    'var r0 = Scene.Current.RootObjects.Where(r => r.Name == "Test_CodeDriven").First();'
    'var an = (XEngine.Runtime.Animator)r0.GetComponent<XEngine.Runtime.Animator>();'
    'var sk = an.Skeleton;'
    'var sb = new System.Text.StringBuilder();'
    'sb.Append("animatorSkeletonBones=").Append(sk.Bones.Length).Append(" | ");'
    'int smrBoneTotal = 0; int sameInstance = 0; int diffInstance = 0;'
    'foreach (var c in r0.GetComponentsInChildren<XEngine.Runtime.SkinnedMeshRenderer>()) {'
    '  var bones = c.Bones;'
    '  if (bones == null) { sb.Append(c.GameObject.Name).Append("(no bones) "); continue; }'
    '  smrBoneTotal += bones.Length;'
    '  foreach (var b2 in bones) {'
    '    if (b2 == null || b2.GameObject == null) continue;'
    '    bool found = false;'
    '    for (int i = 0; i < sk.Bones.Length; i++) {'
    '      if (ReferenceEquals(sk.Bones[i], b2)) { found = true; break; } }'
    '    if (found) sameInstance++; else diffInstance++; } }'
    'sb.Append("smrBones=").Append(smrBoneTotal)'
    '  .Append(" same=").Append(sameInstance)'
    '  .Append(" diff=").Append(diffInstance);'
    'return sb.ToString();')

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
print("[ready]", flush=True)

ev('return XEngine.Editor.GUI.SceneView.EditorSceneManager.OpenScene("Scenes/AnimSingleTest.scene");', "OPEN")
time.sleep(3)
send("runtime_playmode", {"action":"enter"})
time.sleep(8)

ev(CODE, "BONE-REF")

# Also check: are there TWO hierarchies? Count Bip001 roots
ev('''
var r0 = Scene.Current.RootObjects.Where(r => r.Name == "Test_CodeDriven").First();
int bipCount = 0;
foreach (var c in r0.GetComponentsInChildren<XEngine.Runtime.Transform>()) _ = c;
var all = r0.GetComponentsInChildren(includeInactive: true);
foreach (var o in all) if (o.Name == "Bip001") bipCount++;
return "Bip001 root count in Test_CodeDriven: " + bipCount;''', "HIERARCHY-COUNT")

proc.kill()
