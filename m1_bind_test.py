#!/usr/bin/env python3
"""Isolate the flip: hero in-scene with Animator playing vs disabled (bind pose)."""
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
    return json.dumps(call_tool("runtime_eval", {"code": code}, timeout))[:320]

rid = send("initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                          "clientInfo": {"name": "zz-bind", "version": "1.0"}})
if recv(rid, 300) is None:
    print("INIT_FAILED"); proc.kill(); sys.exit(2)
send("notifications/initialized", notify=True)
print("init ok", flush=True)
time.sleep(10)

print("[open]", eval_code(
    "XEngine.Editor.GUI.SceneView.EditorSceneManager.OpenScene(\"Scenes/ZonezeroCombat.scene\"); "
    "return \"roots:\" + XEngine.Runtime.Resources.Scene.Current?.RootObjects.Count;"), flush=True)
time.sleep(4)

FIND_HERO = ("var hero = System.Linq.Enumerable.FirstOrDefault("
             "XEngine.Runtime.Resources.Scene.Current.RootObjects, r => r.Name == \"Anbi\"); "
             "if (hero == null) return \"no-hero\"; ")

# Use LookAt via transform math (camera behind + above the hero, facing +Z like the demo).
print("[place]", eval_code(
    "var camGo = XEngine.Runtime.Resources.Scene.Current.RootObjects.FirstOrDefault(r => r.Name == \"Main Camera\"); "
    "var hero = XEngine.Runtime.Resources.Scene.Current.RootObjects.FirstOrDefault(r => r.Name == \"Anbi\"); "
    "if (camGo == null || hero == null) return \"missing\"; "
    "camGo.Transform.Position = hero.Transform.Position + new XEngine.Vector.Float3(0f, 1.2f, -2.8f); "
    "camGo.Transform.LocalEulerAngles = new XEngine.Vector.Float3(8f, 0f, 0f); "
    "return \"placed\";"), flush=True)

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

# State 1: default (animator auto-plays Idle).
time.sleep(2)
shot("bind-test-1-playing.png")

# State 2: disable the hero Animator → bind pose.
print("[disable]", eval_code(FIND_HERO +
    "var an = hero.GetComponent<XEngine.Runtime.Animator>(); "
    "if (an == null) return \"no-animator\"; "
    "an.Enabled = false; return \"disabled\";"), flush=True)
time.sleep(2)
shot("bind-test-2-disabled.png")

# State 3: re-enable and let it play again.
print("[enable]", eval_code(FIND_HERO +
    "var an = hero.GetComponent<XEngine.Runtime.Animator>(); "
    "an.Enabled = true; return \"enabled\";"), flush=True)
time.sleep(3)
shot("bind-test-3-reenabled.png")

try:
    proc.stdin.close(); proc.wait(timeout=20)
except subprocess.TimeoutExpired:
    proc.kill()
