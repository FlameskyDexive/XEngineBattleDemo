#!/usr/bin/env python3
"""Capture the inspector PREVIEW for the hero prefab vs the dummy prefab vs the hero FBX
to reproduce the upside-down preview report."""
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

def recv(want_id, timeout=600):
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

def call_tool(name, args, timeout=600):
    rid = send("tools/call", {"name": name, "arguments": args})
    reply = recv(rid, timeout)
    if reply is None: raise TimeoutError(name)
    return reply["result"]

def eval_code(code, timeout=300):
    return json.dumps(call_tool("runtime_eval", {"code": code}, timeout))[:300]

rid = send("initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                          "clientInfo": {"name": "zz-preview", "version": "1.0"}})
if recv(rid, 300) is None:
    print("INIT_FAILED"); proc.kill(); sys.exit(2)
send("notifications/initialized", notify=True)
print("init ok", flush=True)
time.sleep(10)

def select_and_shoot(rel_path, out_name):
    # Select via the asset database entry.
    res = eval_code(
        "var e = XEngine.Editor.EditorAssetBackend.Instance.GetEntry(\"" + rel_path + "\"); "
        "if (e == null) return \"no-entry\"; "
        "var a = XEngine.Runtime.AssetDatabase.Get(e.Guid); "
        "if (a == null) return \"no-asset\"; "
        "XEngine.Editor.Core.Selection.Select(a); "
        "return \"selected:\" + a.Name;")
    print("[select]", rel_path, res, flush=True)
    time.sleep(4)
    shot = call_tool("runtime_panel_screenshot",
                     {"panelType": "XEngine.Editor.GUI.Panels.InspectorPanel", "width": 420, "height": 640}, 240)
    blob = json.dumps(shot)
    paths = [f for f in blob.split('"') if f.endswith(".png")]
    if paths:
        src = max(paths, key=os.path.getmtime)
        shutil.copyfile(src, os.path.join(OUT, out_name))
        print("saved", out_name, flush=True)

# REGEN: re-run the copy+generate menu so meshes reimport with the Clay fix.
    regen = call_tool("runtime_menu", {"action": "invoke", "path": "Zonezero/Copy ZZZ Assets Into Project"}, 1200)
    print("[regen]", json.dumps(regen)[:200], flush=True)
    time.sleep(8)
    build = call_tool("runtime_menu", {"action": "invoke", "path": "Zonezero/Build Combat Demo Scene"}, 600)
    print("[build]", json.dumps(build)[:200], flush=True)
    time.sleep(8)
    select_and_shoot("ZZZ/Prefab/Anbi.prefab", "preview-anbi-prefab.png")
select_and_shoot("ZZZ/Prefab/Claymore.prefab", "preview-claymore-prefab.png")
select_and_shoot("ZZZ/Arts/PlayerModel/安比/Anbi.fbx", "preview-anbi-fbx.png")

try:
    proc.stdin.close(); proc.wait(timeout=20)
except subprocess.TimeoutExpired:
    proc.kill()
