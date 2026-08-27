#!/usr/bin/env python3
"""UI acceptance v2: panel screenshots for Animation toolbar + Inspector-animator-state."""
import json, subprocess, time, os, shutil

EDITOR = r"F:\Git\XEngine\Build\Editor\Debug\net10.0\XEngine.Editor.exe"
PROJECT = r"F:\Git\XEngine\ZonezeroTestProject"
OUT = r"F:\Git\XEngine\ZonezeroTestProject\diag"

proc = subprocess.Popen([EDITOR, "--serve", "--project", PROJECT],
                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                        cwd=os.path.dirname(EDITOR))
_id = 0
def send(method, params=None):
    global _id
    _id += 1
    msg = {"jsonrpc": "2.0", "id": _id, "method": method}
    if params: msg["params"] = params
    proc.stdin.write(json.dumps(msg).encode() + b"\n"); proc.stdin.flush()
    return _id
def recv(want_id, timeout=300):
    dl = time.time() + timeout
    while time.time() < dl:
        raw = proc.stdout.readline()
        if not raw: return None
        line = raw.decode("utf-8", errors="replace").strip()
        if not line: continue
        try: m = json.loads(line)
        except json.JSONDecodeError: continue
        if m.get("id") == want_id: return m
    return None
def call(name, args, timeout=240):
    rid = send("tools/call", {"name": name, "arguments": args})
    r = recv(rid, timeout)
    sc = {}
    try: sc = ((r or {}).get("result") or {}).get("structuredContent") or {}
    except Exception: pass
    txt = json.dumps(sc.get("path") or sc.get("error") or sc)[:200]
    print(f"   -> {txt}")
    return sc.get("path")

def ev(label, code, timeout=180):
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
    print(f"[{label}] {txt[:400]}", flush=True)
    return txt

def pshot(name, panel_type, w=1000, h=800):
    p = call("runtime_panel_screenshot", {"panelType": panel_type, "width": w, "height": h}, 240)
    if isinstance(p, str) and p.endswith(".png"):
        dst = os.path.join(OUT, name)
        shutil.copyfile(p, dst)
        print(f"[shot] {dst} ({os.path.getsize(dst)}B)", flush=True)

send("tools/call", {"name": "runtime_state", "arguments": {}})
proc.stdin.write(b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n'); proc.stdin.flush()
time.sleep(42)

# Animator window picked state -> main Inspector renders it.
ev("SETUP-STATE", '''
var app3 = XEngine.Editor.Core.EditorApplication.Instance!;
app3.OpenPanel(typeof(XEngine.Editor.GUI.Panels.AnimatorWindow));
var aw = app3.FindOpenPanel(typeof(XEngine.Editor.GUI.Panels.AnimatorWindow))
           as XEngine.Editor.GUI.Panels.AnimatorWindow;
if (aw == null) return "no animator window";
var rf3 = new XEngine.Runtime.AssetRef<XEngine.Animation.AnimatorController>(
    System.Guid.Parse("58c28ec0-40bc-4ce5-82a7-132f6165880a"));
rf3.EnsureLoaded();
aw.SetController((XEngine.Animation.AnimatorController)rf3.Res!);
aw.SelectLayer(0);
var f4 = typeof(XEngine.Editor.GUI.Panels.AnimatorWindow).GetField("_selectedStateName",
    System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.Instance);
f4.SetValue(aw, "Attack_Normal_1");
"picked owns(null)=" + aw.OwnsInspectorContext(null)''')
pshot("ui-inspector-state.png", "XEngine.Editor.GUI.Panels.InspectorPanel", 520, 640)

# About modal: invoke via registry root action then assert Modal open.
ev("ABOUT-INVOKE", '''
var root = XEngine.Editor.Core.MenuRegistry.RootMenus.LastOrDefault(m => m.Label.StartsWith("About"));
if (root == null) return "missing";
root.OnClick!.Invoke();
return "clicked"''')
time.sleep(1.0)
ev("MODAL-OPEN?", '"Modal.IsOpen=" + XEngine.OrigamiUI.Modal.IsOpen')
ev("CLOSE-MODAL", 'XEngine.OrigamiUI.Modal.Pop(); "popped"')

try:
    proc.stdin.close(); proc.wait(timeout=20)
except Exception:
    proc.kill()
