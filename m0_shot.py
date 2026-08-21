#!/usr/bin/env python3
"""M0 acceptance: open ZonezeroTestProject in a live editor, capture Package Manager
panel screenshots showing com.xengine.zonezero / com.xengine.unityimporter, then copy
PNGs into docs/superpowers/evidence/zonezero/."""
import json, subprocess, sys, time, os, shutil, glob

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
        except json.JSONDecodeError:
            print("[editor]", line[:140], flush=True); continue
        if msg.get("id") == want_id: return msg
        if "method" in msg: print("[mcp]", str(msg.get("method"))[:70], flush=True)
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

# Let project open + package scripts compile, then inspect package state.
time.sleep(8)
state = call_tool("runtime_eval", {"code":
    "var pm = XEngine.Editor.Projects.Packages.PackageManager.Instance; "
    "string.Join(\"|\", pm.GetAllPackagesSafe().Select(p => p.Name + \":\" + (p.IsInstalled ? \"installed\" : \"available\")))"}, 300)
print("[packages]", json.dumps(state)[:1500], flush=True)

def grab(out_name):
    res = call_tool("runtime_panel_screenshot", {"panelType": PANEL}, 240)
    blob = json.dumps(res)
    paths = [frag for frag in blob.split('"') if frag.endswith(".png")]
    print("[shot]", blob[:300], flush=True)
    if paths:
        src = max(paths, key=os.path.getmtime)
        dst = os.path.join(EVIDENCE, out_name)
        shutil.copyfile(src, dst)
        print("saved", dst, flush=True)
        return True
    return False

grab("m0-zonezero-available.png")

# Ensure zonezero (+ transitively unityimporter) is installed, then capture again.
install = call_tool("runtime_eval", {"code":
    "XEngine.Editor.Projects.Packages.PackageOperations.Install("
    "XEngine.Editor.Projects.Project.Current, \"com.xengine.zonezero\", \"0.1.0\").ToString()"}, 300)
print("[install]", json.dumps(install)[:400], flush=True)
time.sleep(5)

cache = call_tool("runtime_eval", {"code":
    "string.Join(\"|\", System.IO.Directory.GetDirectories("
    "System.IO.Path.Combine(XEngine.Editor.Projects.Project.Current.LibraryPath, \"PackageCache\")).Select(System.IO.Path.GetFileName))"}, 120)
print("[packagecache]", json.dumps(cache)[:400], flush=True)

grab("m0-zonezero-installed.png")

logs = call_tool("runtime_logs", {"minimumSeverity": "Error"}, 60)
print("[errors]", json.dumps(logs)[:1600], flush=True)

try:
    proc.stdin.close(); proc.wait(timeout=20)
except subprocess.TimeoutExpired:
    proc.kill()
