#!/usr/bin/env python3
"""Upright diagnosis: open ZonezeroCombat.scene, screenshot the hero with the Animator
disabled (bind pose) vs enabled (plays Idle) to isolate the flip layer."""
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
    return json.dumps(call_tool("runtime_eval", {"code": code}, timeout))[:400]

rid = send("initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                          "clientInfo": {"name": "zz-upright", "version": "1.0"}})
if recv(rid, 300) is None:
    print("INIT_FAILED"); proc.kill(); sys.exit(2)
send("notifications/initialized", notify=True)
print("init ok", flush=True)
time.sleep(10)

print("[open]", eval_code(
    "XEngine.Editor.GUI.SceneView.EditorSceneManager.OpenScene(\"Scenes/ZonezeroCombat.scene\"); "
    "\"roots:\" + XEngine.Runtime.Scene.Current?.RootObjects.Count"), flush=True)
time.sleep(5)

# Frame the hero: move the template camera close & level at the Anbi root.
print("[find]", eval_code(
    "var s = XEngine.Runtime.Scene.Current; var sb = new System.Text.StringBuilder(); "
    "foreach (var r in s.RootObjects) sb.Append(r.Name).Append(','); sb.ToString()"), flush=True)

print("[cam]", eval_code(
    "var cam = XEngine.Runtime.GameObject.Find(\"Main Camera\"); "
    "var hero = XEngine.Runtime.GameObject.Find(\"Anbi\"); "
    "if (hero == null) { var s = XEngine.Runtime.Scene.Current; "
    "foreach (var r in s.RootObjects) if (r.Name.Contains(\"Anbi\")) hero = r; } "
    "if (hero != null && cam != null) { "
    "cam.Transform.Position = hero.Transform.Position + new XEngine.Vector.Float3(0f, 1.0f, -2.5f); "
    "cam.Transform.LookAt(hero.Transform.Position + new XEngine.Vector.Float3(0f, 0.9f, 0f)); "
    "\"framed\" } else \"hero-or-cam-missing\""), flush=True)

# Disable the hero's Animator → bind pose.
print("[anim-off]", eval_code(
    "var hero = XEngine.Runtime.GameObject.Find(\"Anbi\") "
    "?? System.Linq.Enumerable.FirstOrDefault(XEngine.Runtime.Scene.Current.RootObjects, r => r.Name.Contains(\"Anbi\")); "
    "if (hero == null) return \"no-hero\"; "
    "var an = hero.GetComponent<XEngine.Animation.Animator>(); "
    "if (an == null) return \"no-animator\"; "
    "an.Enabled = false; \"animator-off\""), flush=True)
time.sleep(2)

def shot(name):
    call_tool("runtime_menu", {"action": "invoke", "path": "Window/General/New Game View"}, 120)
    time.sleep(2)
    res = call_tool("runtime_screenshot", {}, 300)
    blob = json.dumps(res)
    paths = [f for f in blob.split('"') if f.endswith(".png")]
    if paths:
        src = max(paths, key=os.path.getmtime)
        shutil.copyfile(src, os.path.join(OUT, name))
        print("saved", name, flush=True)

shot("diag-bind.png")

print("[anim-on]", eval_code(
    "var hero = System.Linq.Enumerable.FirstOrDefault(XEngine.Runtime.Scene.Current.RootObjects, r => r.Name.Contains(\"Anbi\")); "
    "var an = hero?.GetComponent<XEngine.Animation.Animator>(); "
    "if (an == null) return \"no-animator\"; "
    "an.Enabled = true; an.Play(\"Idle\"); \"playing-idle\""), flush=True)
time.sleep(3)
shot("diag-idle.png")

logs = call_tool("runtime_logs", {"minimumSeverity": "Warning"}, 60)
print("[warnings]", json.dumps(logs)[:2000], flush=True)

try:
    proc.stdin.close(); proc.wait(timeout=20)
except subprocess.TimeoutExpired:
    proc.kill()
