#!/usr/bin/env python3
"""Diagnose: inject snapshot, read it back, capture, read back again after capture."""
import json, subprocess, sys, time, os

EDITOR = r"F:\Git\XEngine\Build\Editor\Debug\net10.0\XEngine.Editor.exe"
PROJECT = r"F:\Git\XEngine\ZonezeroTestProject"
PANEL = "XEngine.Editor.GUI.Panels.PackageManagerPanel"

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

def eval_code(code, timeout=120):
    r = call_tool("runtime_eval", {"code": code}, timeout)
    return json.dumps(r)[:400]

rid = send("initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                          "clientInfo": {"name": "zz-diag", "version": "1.0"}})
if recv(rid, 300) is None:
    print("INIT_FAILED"); proc.kill(); sys.exit(2)
send("notifications/initialized", notify=True)
print("init ok", flush=True); time.sleep(8)

call_tool("runtime_menu", {"action": "invoke", "path": "Window/Package Management/Package Manager"}, 120)
time.sleep(3)

COMMON = ('var t = System.Type.GetType("XEngine.Editor.GUI.Panels.PackageManagerPanel, XEngine.Editor"); '
          'var panel = XEngine.Editor.Core.EditorApplication.Instance.FindOpenPanel(t); ')
READBACK = COMMON + ('if (panel == null) { return "null"; } '
          'var flags = System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.Instance; '
          'var snap = t.GetField("_snapshot", flags).GetValue(panel); '
          'var refr = t.GetField("_refreshing", flags).GetValue(panel); '
          'var sel = t.GetField("_selectedPackage", flags).GetValue(panel); '
          'return "snap=" + (snap == null ? "null" : "entries:" + snap.GetType().GetProperty("Entries").GetValue(snap).GetType().GetProperty("Count").GetValue(snap.GetType().GetProperty("Entries").GetValue(snap))) + " refreshing=" + refr + " sel=" + sel;')
INJECT = COMMON + ('if (panel == null) { return "null"; } '
          'var snap = XEngine.Editor.Projects.Packages.PackageCatalog.Build(XEngine.Editor.Projects.Project.Current); '
          'var flags = System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.Instance; '
          't.GetField("_snapshot", flags).SetValue(panel, snap); '
          't.GetField("_refreshing", flags).SetValue(panel, false); '
          't.GetMethod("SelectPackage", flags).Invoke(panel, new object[]{ "com.xengine.zonezero" }); '
          'return "injected";')

print("[read1]", eval_code(READBACK), flush=True)
print("[inject]", eval_code(INJECT), flush=True)
print("[read2]", eval_code(READBACK), flush=True)
shot = call_tool("runtime_panel_screenshot", {"panelType": PANEL, "width": 1100, "height": 700}, 240)
print("[shot]", json.dumps(shot)[:200], flush=True)
print("[read3]", eval_code(READBACK), flush=True)

try:
    proc.stdin.close(); proc.wait(timeout=20)
except subprocess.TimeoutExpired:
    proc.kill()
