#!/usr/bin/env python3
"""Final validation: reimport FBX (fixed unitScale), rebuild prefabs+scene, verify bones animate."""
import json, subprocess, time, os, shutil, re, glob

EDITOR = r"F:\Git\XEngine\Build\Editor\Debug\net10.0\XEngine.Editor.exe"
PROJECT = r"F:\Git\XEngine\ZonezeroTestProject"
OUT = r"F:\Git\XEngine\ZonezeroTestProject\diag"

fbxs = sorted(glob.glob(r"F:/Git/XEngine/ZonezeroTestProject/Assets/ZZZ/**/*.FBX", recursive=True))
guids = []
for f in fbxs:
    m = re.search(r'"guid":\s*"([0-9a-f-]{36})"', open(f + ".meta", encoding="utf-8").read())
    if m: guids.append(m.group(1))
print(f"[offline] {len(fbxs)} FBX files", flush=True)

proc = subprocess.Popen([EDITOR, "--serve", "--project", PROJECT],
                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                        cwd=os.path.dirname(EDITOR))
import threading
_responses = {}
elog = open(r"diag/zz_final_stdio.log", "ab")
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

_id = 0
def call_eval(code, timeout=600):
    global _id
    _id += 1
    my = _id
    try:
        proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": my, "method": "tools/call",
            "params": {"name": "runtime_eval", "arguments": {"code": code}}}).encode() + b"\n")
        proc.stdin.flush()
    except Exception as e:
        return f"PIPE {e}"
    dl = time.time() + timeout
    while time.time() < dl:
        if my in _responses:
            m = _responses.pop(my)
            try:
                c = ((m.get("result") or {}).get("content") or [])
                return "".join(x.get("text") or "" for x in c)
            except Exception:
                pass
        time.sleep(0.05)
    return "TIMEOUT"

def call_menu(path, timeout=900):
    my = _id + 1
    proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": my, "method": "tools/call",
        "params": {"name": "runtime_menu", "arguments": {"action": "invoke", "path": path}}}).encode() + b"\n")
    proc.stdin.flush()
    r = None
    dl = time.time() + timeout
    while time.time() < dl:
        if my in _responses:
            r = _responses.pop(my); break
        time.sleep(0.1)
    print(f"[menu {path}]", flush=True)

def shoot(name):
    pid = send("tools/call", {"name": "runtime_screenshot", "arguments": {"width": 1280, "height": 720}})
    r = recv(pid, 240)
    try:
        sc = ((r or {}).get("result") or {}).get("structuredContent") or {}
        pp = sc.get("path")
        if isinstance(pp, str) and pp.endswith(".png"):
            dst = os.path.join(OUT, name)
            shutil.copyfile(pp, dst)
            print(f"[shot] {dst} ({os.path.getsize(dst)}B)", flush=True)
    except Exception as e:
        print("shot err", e)

def ev(label, code, timeout=300):
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

send("tools/call", {"name": "runtime_state", "arguments": {}})
proc.stdin.write(b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n'); proc.stdin.flush()
for attempt in range(40):
    time.sleep(3)
    my = _id + 1
    proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": my, "method": "tools/call",
        "params": {"name": "runtime_eval", "arguments": {"code": 'return "ready";'}}}).encode() + b"\n")
    proc.stdin.flush()
    r = None
    dl = time.time() + 30
    while time.time() < dl:
        raw = proc.stdout.readline()
        if not raw: break
        line = raw.decode("utf-8", errors="replace").strip()
        if not line: continue
        try: m = json.loads(line)
        except json.JSONDecodeError: continue
        if m.get("id") == my:
            r = m; break
    if r is not None:
        print(f"[ready after {(attempt+1)*3}s]", flush=True)
        break

call_menu("Zonezero/Build Anim Single Test")
time.sleep(4)

# enter play
my = _id + 1
proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": my, "method": "tools/call",
    "params": {"name": "runtime_playmode", "arguments": {"action": "enter"}}}).encode() + b"\n")
proc.stdin.flush()
time.sleep(8)

# Drive both to Run via FSM (right) and code (left)
ev("DRIVE", '''
var sbF = new System.Text.StringBuilder();
foreach (var rn in new[]{"Test_CodeDriven","Test_FsmDriven"}) {
  var rr = Scene.Current.RootObjects.Where(r2 => r2.Name == rn).First();
  var aa = (XEngine.Runtime.Animator)rr.GetComponent<XEngine.Runtime.Animator>();
  if (rn == "Test_CodeDriven") {
    var c = (XEngine.Runtime.AnimationClip)aa.CurrentClip!;
    aa.Play(c, 0f);
  } else {
    aa.Runtime!.Play(XEngine.Animation.AnimationNameHash.Hash("Run"), 0f);
  }
  sbF.Append(rn).Append(" ok\\n"); }
return sbF.ToString();''')

print("--- pose sampling", flush=True)
for i in range(8):
    ev(f"P{i}", '''
var sbP = new System.Text.StringBuilder();
foreach (var rn in new[]{"Test_CodeDriven","Test_FsmDriven"}) {
  var rr = Scene.Current.RootObjects.Where(r2 => r2.Name == rn).First();
  var aa = (XEngine.Runtime.Animator)rr.GetComponent<XEngine.Runtime.Animator>();
  var sk2 = aa.Skeleton;
  XEngine.Vector.Transform? pel = null;
  for (int i2 = 0; i2 < sk2.Bones.Length; i2++) {
    var b2 = sk2.Bones[i2];
    if (b2 != null && b2.GameObject != null && b2.GameObject.Name == "Bip001 Pelvis") { pel = b2; break; } }
  var info = aa.GetCurrentAnimatorStateInfo();
  sbP.Append(rn.Substring(5, 4)).Append(" nT=").Append(info.normalizedTime.ToString("F2"))
     .Append(" pelvisY=").Append(pel != null ? pel.Position.Y.ToString("F3") : "-").Append("\\n"); }
return sbP.ToString();''')
    if i == 3:
        shoot("final-anim.png")
    time.sleep(0.5)

shoot("final-end.png")

# exit play
my = _id + 1
proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": my, "method": "tools/call",
    "params": {"name": "runtime_playmode", "arguments": {"action": "exit"}}}).encode() + b"\n")
proc.stdin.flush()
time.sleep(5)
try:
    proc.stdin.close(); proc.wait(timeout=15)
except Exception:
    proc.kill()
