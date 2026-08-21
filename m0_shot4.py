#!/usr/bin/env python3
"""M0 acceptance v4: pump the panel's async catalog refresh by repeatedly capturing
(each capture pumps 3 OnGUI frames), then select packages via reflection and capture
the populated list. Also dumps the catalog entry states as text evidence."""
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

def grab(out_name=None):
    shot = call_tool("runtime_panel_screenshot", {"panelType": PANEL, "width": 1100, "height": 700}, 240)
    blob = json.dumps(shot)
    paths = [f for f in blob.split('"') if f.endswith(".png")]
    src = max(paths, key=os.path.getmtime) if paths else None
    if src and out_name:
        dst = os.path.join(EVIDENCE, out_name)
        shutil.copyfile(src, dst)
        print("saved", out_name, flush=True)
    return src

# Text evidence: catalog built synchronously shows both packages + install states.
cat = call_tool("runtime_eval", {"code":
    'var snap = XEngine.Editor.Projects.Packages.PackageCatalog.Build(XEngine.Editor.Projects.Project.Current); '
    'string.Join("|", snap.Entries.Where(e => e.Name.Contains("zonezero") || e.Name.Contains("unityimporter") || e.Name.Contains("genshin")) '
    '.Select(e => e.Name + ":" + e.DisplayName + ":" + e.InstallState))'}, 300)
print("[catalog]", json.dumps(cat)[:900], flush=True)

# Reflection selection (valid C# statement form).
def select(pkg):
    code = ('var t = System.Type.GetType("XEngine.Editor.GUI.Panels.PackageManagerPanel, XEngine.Editor"); '
            'var panel = XEngine.Editor.Core.EditorApplication.Instance.FindOpenPanel(t); '
            'if (panel == null) { return "panel-not-open"; } '
            't.GetMethod("SelectPackage", '
            'System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.Instance) '
            f'.Invoke(panel, new object[]{{ "{pkg}" }}); '
            'return "ok";')
    res = call_tool("runtime_eval", {"code": code}, 120)
    print("[select]", pkg, json.dumps(res)[:260], flush=True)

# Prime refresh (capture pumps OnGUI), wait, then verify populated by re-capture.
grab()
time.sleep(12)
grab()

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
