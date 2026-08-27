#!/usr/bin/env python3
"""Animation visibility regression: burst screenshots + hero pose samples after Play-debounce fix."""
import json, subprocess, time, os, shutil

EDITOR = r"F:\Git\XEngine\Build\Editor\Debug\net10.0\XEngine.Editor.exe"
PROJECT = r"F:\Git\XEngine\ZonezeroTestProject"
OUT = r"F:\Git\XEngine\ZonezeroTestProject\diag"

HERO_POSE = (
'var hero = Scene.Current.RootObjects.Where(r => r.Name == "Battle_Hero").First();'
'var an = (XEngine.Runtime.Animator)hero.GetComponent<XEngine.Runtime.Animator>();'
'var sk = an.Skeleton;'
'XEngine.Vector.Transform? hand = null;'
'for (int i = 0; i < sk.Bones.Length; i++) {'
'  var b = sk.Bones[i];'
'  if (b != null && b.GameObject != null && b.GameObject.Name.Contains("R Hand")) { hand = b; break; } }'
'var w = hand != null ? hand.Position : new XEngine.Vector.Float3(999,999,999);'
'var info = an.GetCurrentAnimatorStateInfo();'
'return "nT=" + info.normalizedTime.ToString("F3") + " handW=(" + w.X.ToString("F3") + "," + w.Y.ToString("F3") + "," + w.Z.ToString("F3") + ")";')

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
def recv(want_id, timeout=600):
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
def ev(label, code, timeout=240):
    rid = send("tools/call", {"name": "runtime_eval", "arguments": {"code": code}})
    r = recv(rid, timeout)
    txt = ""
    try:
        c = ((r or {}).get("result") or {}).get("content") or []
        txt = "".join(x.get("text") or "" for x in c)
    except Exception:
        pass
    print(f"[{label}] {txt[:200]}", flush=True)
    return txt
def shoot(name):
    rid = send("tools/call", {"name": "runtime_screenshot", "arguments": {"width": 1280, "height": 720}})
    r = recv(rid, 240)
    try:
        sc = ((r or {}).get("result") or {}).get("structuredContent") or {}
        p = sc.get("path")
        if isinstance(p, str) and p.endswith(".png"):
            dst = os.path.join(OUT, name)
            shutil.copyfile(p, dst)
            print(f"[shot] {dst} ({os.path.getsize(dst)}B)", flush=True)
    except Exception as e:
        print("shot err", e)

send("tools/call", {"name": "runtime_state", "arguments": {}})
proc.stdin.write(b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n'); proc.stdin.flush()
time.sleep(60)

ev("OPEN", 'return XEngine.Editor.GUI.SceneView.EditorSceneManager.OpenScene("Scenes/ZonezeroBattle.scene");', 240)
time.sleep(3)
rid0 = send("tools/call", {"name": "runtime_playmode", "arguments": {"action": "enter"}})
recv(rid0, 300); time.sleep(6)

INJ = 'XEngine.Runtime.InputInjector.{fn}(XEngine.Runtime.KeyCode.{k}); "o"'
def key(k, down): ev(f"k{k}", INJ.format(fn="Press" if down else "Release", k=k))

print("--- idle burst: hero right-hand world pos (should oscillate/shift while idle breathing)", flush=True)
for i in range(5):
    ev(f"idle{i}", HERO_POSE)
    time.sleep(0.4)

print("--- run burst: hold W, hand should swing", flush=True)
key('W', True)
for i in range(5):
    ev(f"run{i}", HERO_POSE)
    shoot(f"run-{i}.png")
    time.sleep(0.45)
key('W', False)

print("--- ally burst: Corin hand pos during her program", flush=True)
ALLY_HAND = (
'var ally = Scene.Current.RootObjects.Where(r => r.Name.StartsWith("Battle_Ally")).First();'
'var an2 = (XEngine.Runtime.Animator)ally.GetComponent<XEngine.Runtime.Animator>();'
'var sk2 = an2.Skeleton;'
'XEngine.Vector.Transform? hd = null;'
'for (int i = 0; i < sk2.Bones.Length; i++) {'
'  var b2 = sk2.Bones[i];'
'  if (b2 != null && b2.GameObject != null && b2.GameObject.Name.Contains("R Hand")) { hd = b2; break; } }'
'var w2 = hd != null ? hd.Position : new XEngine.Vector.Float3(999,999,999);'
'var i2 = an2.GetCurrentAnimatorStateInfo();'
'return "nT=" + i2.normalizedTime.ToString("F3") + " handW=(" + w2.X.ToString("F3") + "," + w2.Y.ToString("F3") + "," + w2.Z.ToString("F3") + ")";')
for i in range(6):
    ev(f"ally{i}", ALLY_HAND)
    time.sleep(0.4)

shoot("after-final.png")

try:
    ridx = send("tools/call", {"name": "runtime_playmode", "arguments": {"action": "exit"}})
    recv(ridx, 120)
except Exception as e:
    print("[exit]", e)
finally:
    try:
        proc.stdin.close(); proc.wait(timeout=15)
    except Exception:
        proc.kill()
