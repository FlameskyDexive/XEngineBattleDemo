#!/usr/bin/env python3
"""Final acceptance: regenerate native assets with the Clay bind-pose fix, rebuild the scene,
capture inspector previews (hero prefab, hero FBX, dummy prefab) and a gameview shot."""
import json, subprocess, sys, time, os, shutil

EDITOR = r"F:\Git\XEngine\Build\Editor\Debug\net10.0\XEngine.Editor.exe"
PROJECT = r"F:\Git\XEngine\ZonezeroTestProject"
OUT = r"F:\Git\XEngine\ZonezeroTestProject\diag"
os.makedirs(OUT, exist_ok=True)

proc = subprocess.Popen([EDITOR, "--serve", "--project", PROJECT],
                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                        cwd=os.path.dirname(EDITOR))
_id = 0

def send(method, params=None, notify=False):
    global _id
    msg = {"jsonrpc": "2.0", "method": method}
    if params: msg["params"] = params
    if not notify:
        _id += 1; msg["id"] = _id
    proc.stdin.write(json.dumps(msg).encode("utf-8") + b"\n"); proc.stdin.flush()
    return _id if not notify else None

def recv(want_id, timeout=900):
    deadline = time.time() + timeout
    while time.time() < deadline:
        raw = proc.stdout.readline()
        if not raw: return None
        line = raw.decode("utf-8", errors="replace").strip()
        if not line: continue
        try: msg = json.loads(line)
        except json.JSONDecodeError: continue
        if msg.get("id") == want_id: return msg
    return None

def call_tool(name, args, timeout=900):
    rid = send("tools/call", {"name": name, "arguments": args})
    reply = recv(rid, timeout)
    if reply is None: raise TimeoutError(name)
    return reply["result"]

rid = send("initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                          "clientInfo": {"name": "zz-final", "version": "1.0"}})
if recv(rid, 300) is None:
    print("INIT_FAILED"); proc.kill(); sys.exit(2)
send("notifications/initialized", notify=True)
print("init ok", flush=True)
time.sleep(30)

print("[copy]", json.dumps(call_tool("runtime_menu", {"action": "invoke", "path": "Zonezero/Copy ZZZ Assets Into Project"}, 1200))[:140], flush=True)
print("[gen]", json.dumps(call_tool("runtime_menu", {"action": "invoke", "path": "Zonezero/Generate Native Assets"}, 1200))[:140], flush=True)
print("[build]", json.dumps(call_tool("runtime_menu", {"action": "invoke", "path": "Zonezero/Build Combat Demo Scene"}, 900))[:140], flush=True)
time.sleep(8)

def select_and_shoot(rel_path, out_name):
    res = call_tool("runtime_eval", {"code":
        'var e = XEngine.Editor.EditorAssetBackend.Instance.GetEntry("' + rel_path + '"); '
        'if (e == null) return "no-entry"; '
        'var a = XEngine.Runtime.AssetDatabase.Get(e.Guid); '
        'if (a == null) return "no-asset"; '
        'XEngine.Editor.Core.Selection.Select(a); '
        'return "ok";'}, 300)
    print("[select]", rel_path, json.dumps(res)[:120], flush=True)
    time.sleep(5)
    shot = call_tool("runtime_panel_screenshot",
                     {"panelType": "XEngine.Editor.GUI.Panels.InspectorPanel", "width": 420, "height": 640}, 240)
    blob = json.dumps(shot)
    paths = [f for f in blob.split('"') if f.endswith(".png")]
    if paths:
        src = max(paths, key=os.path.getmtime)
        shutil.copyfile(src, os.path.join(OUT, out_name))
        print("saved", out_name, os.path.getsize(os.path.join(OUT, out_name)), flush=True)

select_and_shoot("ZZZ/Prefab/Anbi.prefab", "final-anbi-prefab.png")
select_and_shoot("ZZZ/Arts/PlayerModel/安比/Anbi.fbx", "final-anbi-fbx.png")
select_and_shoot("ZZZ/Prefab/Claymore.prefab", "final-claymore-prefab.png")

# GameView of the rebuilt scene.
call_tool("runtime_eval", {"code":
    'XEngine.Editor.GUI.SceneView.EditorSceneManager.OpenScene("Scenes/ZonezeroCombat.scene"); return "opened";'}, 300)
time.sleep(6)
call_tool("runtime_menu", {"action": "invoke", "path": "Window/General/New Game View"}, 120)
time.sleep(5)
res = call_tool("runtime_screenshot", {}, 300)
blob = json.dumps(res)
paths = [f for f in blob.split('"') if f.endswith(".png")]
if paths:
    src = max(paths, key=os.path.getmtime)
    shutil.copyfile(src, os.path.join(OUT, "final-scene.png"))
    print("saved final-scene.png", flush=True)

logs = call_tool("runtime_logs", {"minimumSeverity": "Error"}, 60)
print("[errors]", json.dumps(logs)[:1500], flush=True)

try:
    proc.stdin.close(); proc.wait(timeout=20)
except subprocess.TimeoutExpired:
    proc.kill()
