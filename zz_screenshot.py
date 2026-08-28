#!/usr/bin/env python3
"""Take screenshots during animation playback."""
import json, subprocess, time, os, shutil

EDITOR = r"F:\Git\XEngine\Build\Editor\Debug\net10.0\XEngine.Editor.exe"
PROJECT = r"F:\Git\XEngine\ZonezeroTestProject"
OUT = os.path.join(PROJECT, "diag")

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
for a in range(30):
    try:
        i = send("runtime_eval", {"code":'return "r";'})
        r = wait(i, 15)
        if r is not None: break
    except OSError: pass
    time.sleep(2)
print("[ready]", flush=True)

# Open scene, enter play
i = send("runtime_eval", {"code":'return XEngine.Editor.GUI.SceneView.EditorSceneManager.OpenScene("Scenes/AnimSingleTest.scene");'})
wait(i, 240); time.sleep(3)
i = send("runtime_playmode", {"action":"enter"}); wait(i, 300); time.sleep(6)

# Drive the code-driven one to Run (it has SingleClipPlayer already playing)
# The FSM one defaults to Idle
for s in range(4):
    shoot(f"final-anim-{s}.png")
    time.sleep(0.4)

try:
    i = send("runtime_playmode", {"action":"exit"}); wait(i, 120)
except: pass
time.sleep(3)
proc.kill()
