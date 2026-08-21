#!/usr/bin/env python3
"""M0 acceptance v2: open PackageManager as a live docked panel, let the catalog refresh,
select zonezero/unityimporter via reflection, capture populated screenshots."""
import json, subprocess, sys, time, os, shutil

EDITOR = r"F:\Git\XEngine\Build\Editor\Debug\net10.0\XEngine.Editor.exe"
PROJECT = r"F:\Git\XEngine\ZonezeroTestProject"
EVIDENCE = r"F:\Git\XEngine\docs\superpowers\evidence\zonezero"
PANEL = "XEngine.Editor.GUI.Panels.PackageManagerPanel"

os.makedirs(EVIDENCE, exist_ok=True)

proc = subprocess.Popen([EDITOR, "--serve", "--project", PROJECT],
                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                        cwd=os.path.dirname(EDITOR))
_id = 0

def send(method, params=None, notify=False):
    global _id
    msg = {"jsonrpc": "2.0", "method": method}
    if params: msg["params"] = params
    if not notify:
        _id += 1
        msg["id"] = _id
    proc.stdin.write(json.dumps(msg).encode("utf-8") + b"\n")
    proc.stdin.flush()
    return _id if not notify else None

def recv(want_id, timeout=300):
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

def call_tool(name, args, timeout=300):
    rid = send("tools/call", {"name": name, "arguments": args})
    reply = recv(rid, timeout)
    if reply is None: raise TimeoutError(name)
    return reply["result"]

rid = send("initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                          "clientInfo": {"name": "zonezero-m0", "version": "1.0"}})
if recv(rid, 300) is None:
    print("INIT_FAILED"); proc.kill(); sys.exit(2)
send("notifications/initialized", notify=True)
print("MCP initialized", flush=True)
time.sleep(8)

# Open the Package Manager as a live docked panel and give the catalog time to refresh.
print("[menu]", json.dumps(call_tool("runtime_menu", {"action": "invoke", "path": "Window/Package Management/Package Manager"}, 120))[:200], flush=True)
time.sleep(15)

SELECT = ("var app = XEngine.Editor.Core.EditorApplication.Instance; "
          "var panel = app.FindOpenPanel(System.Type.GetType('XEngine.Editor.GUI.Panels.PackageManagerPanel, XEngine.Editor')); "
          "if (panel == null) return \"panel-not-open\"; "
          "var mi = panel.GetType().GetMethod('SelectPackage', "
          "System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.Instance); "
          "mi.Invoke(panel, new object[]{ ARGS }); return \"selected\";")

def select_and_grab(pkg, out_name):
    code = SELECT.replace("ARGS", f"'{pkg}'").replace("return ", "").replace('"panel-not-open"', 'null')
    res = call_tool("runtime_eval", {"code": code}, 120)
    print("[select]", pkg, json.dumps(res)[:220], flush=True)
    time.sleep(2)
    shot = call_tool("runtime_panel_screenshot", {"panelType": PANEL, "width": 1100, "height": 700}, 240)
    blob = json.dumps(shot)
    print("[shot]", blob[:260], flush=True)
    paths = [f for f in blob.split('"') if f.endswith(".png")]
    if paths:
        src = max(paths, key=os.path.getmtime)
        dst = os.path.join(EVIDENCE, out_name)
        shutil.copyfile(src, dst)
        print("saved", dst, flush=True)

select_and_grab("com.xengine.zonezero", "m0-zonezero-installed.png")
select_and_grab("com.xengine.unityimporter", "m0-unityimporter-installed.png")

logs = call_tool("runtime_logs", {"minimumSeverity": "Error"}, 60)
print("[errors]", json.dumps(logs)[:1200], flush=True)

try:
    proc.stdin.close(); proc.wait(timeout=20)
except subprocess.TimeoutExpired:
    proc.kill()
