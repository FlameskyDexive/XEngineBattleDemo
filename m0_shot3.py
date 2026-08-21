#!/usr/bin/env python3
"""M0 acceptance v3: first capture pumps the panel's OnGUI which starts the async catalog
refresh; wait for completion; select packages via reflection; capture populated shots."""
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

def grab(out_name):
    shot = call_tool("runtime_panel_screenshot", {"panelType": PANEL, "width": 1100, "height": 700}, 240)
    blob = json.dumps(shot)
    paths = [f for f in blob.split('"') if f.endswith(".png")]
    if paths:
        src = max(paths, key=os.path.getmtime)
        dst = os.path.join(EVIDENCE, out_name)
        shutil.copyfile(src, dst)
        print("saved", dst, blob[:160], flush=True)
    else:
        print("no png:", blob[:300], flush=True)

SELECT_TMPL = ('var t = System.Type.GetType("XEngine.Editor.GUI.Panels.PackageManagerPanel, XEngine.Editor");'
               'var panel = XEngine.Editor.Core.EditorApplication.Instance.FindOpenPanel(t);'
               'if (panel == null) "panel-not-open" else {'
               't.GetMethod("SelectPackage",'
               'System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.Instance)'
               '.Invoke(panel, new object[]{ "%s" }); "selected:%s"; }')

def select(pkg):
    res = call_tool("runtime_eval", {"code": SELECT_TMPL % (pkg, pkg)}, 120)
    print("[select]", json.dumps(res)[:260], flush=True)

# Prime the refresh, wait for the async catalog build, then capture.
grab("m0-priming.png")
time.sleep(15)
grab("m0-primed.png")

select("com.xengine.zonezero")
time.sleep(1)
grab("m0-zonezero-installed.png")

select("com.xengine.unityimporter")
time.sleep(1)
grab("m0-unityimporter-installed.png")

logs = call_tool("runtime_logs", {"minimumSeverity": "Error"}, 60)
print("[errors]", json.dumps(logs)[:1200], flush=True)

try:
    proc.stdin.close(); proc.wait(timeout=20)
except subprocess.TimeoutExpired:
    proc.kill()
