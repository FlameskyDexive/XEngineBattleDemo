#!/usr/bin/env python3
"""Battle Arena v2 gameplay validation (reflection-safe probes)."""
import json, subprocess, time, os, shutil, re, re

EDITOR = r"F:\Git\XEngine\Build\Editor\Debug\net10.0\XEngine.Editor.exe"
PROJECT = r"F:\Git\XEngine\ZonezeroTestProject"
OUT = r"F:\Git\XEngine\ZonezeroTestProject\diag"

HERO = (
    'var hero = Scene.Current.RootObjects.Where(r => r.Name == "Battle_Hero").First();'
    'var anim = (XEngine.Runtime.Animator)hero.GetComponent<XEngine.Runtime.Animator>();'
    'XEngine.Runtime.MonoBehaviour? hc = null;'
    'foreach (var c in hero.GetComponents()) if (c is XEngine.Runtime.MonoBehaviour mb && mb.GetType().Name == "HeroCombatController") hc = mb;'
    'object combo = "-"; object jatk = "-";'
    'if (hc != null) {'
    '  var BF = System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.Instance;'
    '  combo = hc.GetType().GetField("_comboStage", BF)!.GetValue(hc);'
    '  jatk = hc.GetType().GetField("_jAttacking", BF)!.GetValue(hc); }'
    'var clip = anim.CurrentClip != null ? anim.CurrentClip.Name : "-";'
    'var P = hero.Transform.Position;'
    'return "p=" + P.X.ToString("F2") + "," + P.Y.ToString("F2") + "," + P.Z.ToString("F2")'
    '     + " combo=" + combo + " jatk=" + jatk + " clip=" + clip;')

ALLY = (
    'var sb9 = new System.Text.StringBuilder();'
    'foreach (var r in Scene.Current.RootObjects.Where(r => r.Name.StartsWith("Battle_Ally"))) {'
    '  XEngine.Runtime.MonoBehaviour? ai = null;'
    '  foreach (var c in r.GetComponents()) if (c is XEngine.Runtime.MonoBehaviour mb && mb.GetType().Name == "AllyCombatAI") ai = mb;'
    '  if (ai == null) continue;'
    '  string phase = (string)ai.GetType().GetProperty("AiPhase")!.GetValue(ai)!;'
    '  string step = (string)ai.GetType().GetProperty("AiStep")!.GetValue(ai)!;'
    '  bool dbgWin = (bool)ai.GetType().GetProperty("DbgInWindow")!.GetValue(ai)!;'
    '  float dbgDist = (float)ai.GetType().GetProperty("DbgDist")!.GetValue(ai)!;'
'  var tgt = ai.GetType().GetField("_target", System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.Instance)!.GetValue(ai);'
'  int polls = (int)ai.GetType().GetProperty("DbgPollCount")!.GetValue(ai)!;'
'  int ups = (int)ai.GetType().GetProperty("DbgUpdateCount")!.GetValue(ai)!;'
'  string lerr = (string)ai.GetType().GetProperty("DbgLastError")!.GetValue(ai)!;'
'  string tstate = (string)ai.GetType().GetProperty("DbgTargetState")!.GetValue(ai)!;'
'  string tgtS = tgt == null ? "null" : (((XEngine.Runtime.EngineObject)tgt).IsDisposed ? ((XEngine.Runtime.GameObject)tgt).Name + "(dead)" : ((XEngine.Runtime.GameObject)tgt).Name);'
    '  var P = r.Transform.Position;'
    '  sb9.Append(r.Name.Substring(r.Name.Length - 5)).Append(\' \').Append(phase).Append(\'/\').Append(step)'
    '     .Append(" win=").Append(dbgWin ? 1 : 0).Append(" u=").Append(ups).Append(" polls=").Append(polls)'
    '     .Append(" err=").Append(lerr).Append(" ts=").Append(tstate)'
    '     .Append(" d=").Append(dbgDist.ToString("F1")).Append(" t=").Append(tgtS)'
    '     .Append(" p=").Append(P.X.ToString("F1")).Append(\',\').Append(P.Z.ToString("F1")).Append("\\n"); }'
    'return sb9.Length == 0 ? "no allies" : sb9.ToString();')

HITS = (
    'var sb8 = new System.Text.StringBuilder();'
    'foreach (var r in Scene.Current.RootObjects.Where(r => r.Name.StartsWith("Dummy_"))) {'
    '  XEngine.Runtime.MonoBehaviour? ec = null;'
    '  foreach (var c in r.GetComponents()) if (c is XEngine.Runtime.MonoBehaviour mb && mb.GetType().Name == "EnemyController") ec = mb;'
    '  int n = (int)ec!.GetType().GetProperty("HitCount")!.GetValue(ec)!;'
    '  sb8.Append(r.Name).Append(\'=\').Append(n).Append(\' \'); }'
    'return sb8.ToString();')


TARGETPROBE = (
'var hero2 = Scene.Current.RootObjects.Where(r => r.Name == "Battle_Hero").First();'
'var btT = System.AppDomain.CurrentDomain.GetAssemblies()'
'    .Select(a => a.GetType("XEngine.Zonezero.Combat.BattleTargets")).First(t => t != null)!;'
'var mi = btT.GetMethod("FindNearest", System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.Static);'
'object? found = mi!.Invoke(null, new object[] { hero2.Transform.Position, 40f });'
'return "findNearest=" + (found == null ? "NULL" : ((XEngine.Runtime.GameObject)found).Name);')


MEMBERSPROBE = (
'var r0 = Scene.Current.RootObjects.Where(r => r.Name.StartsWith("Battle_Ally")).First();'
'XEngine.Runtime.MonoBehaviour? ai = null;'
'foreach (var c in r0.GetComponents()) if (c is XEngine.Runtime.MonoBehaviour mb && mb.GetType().Name == "AllyCombatAI") ai = mb;'
'var t = ai!.GetType();'
'var BF = System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.Public;'
'var methods = string.Join(",", t.GetMethods(BF).Where(m => m.DeclaringType == t).Select(m => m.Name).OrderBy(x => x));'
'var fields = string.Join(",", t.GetFields(BF).Select(f => f.Name).OrderBy(x => x));'
'var asmLoc = t.Assembly.Location;'
'return "asm=" + asmLoc + " || methods=[" + methods + "] || fields=[" + fields + "]";')

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
    if not txt and err: txt = "ERR " + json.dumps(err)[:220]
    print(f"[{label}] {txt[:320]}", flush=True)
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

ev("OPEN-BATTLE", 'return XEngine.Editor.GUI.SceneView.EditorSceneManager.OpenScene("Scenes/ZonezeroBattle.scene");', 240)
time.sleep(3)
rid0 = send("tools/call", {"name": "runtime_playmode", "arguments": {"action": "enter"}})
recv(rid0, 300); time.sleep(6)
for i in range(4):
    ev(f"a{i}", ALLY); time.sleep(0.8)
lgjson2 = json.dumps(call("runtime_logs", {"count": 400}, 180))
hits_probe = [m for m in re.findall(r'"message": "(.*?)"', lgjson2) if "play-probe" in m]
print("[PLAY-PROBE COUNT]", len(hits_probe), hits_probe[:3])

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
