#!/usr/bin/env python3
"""Why don't characters animate in Battle scene? One probe: fsm binding, clip, nT, pelvis motion."""
import json, subprocess, time, os

EDITOR = r"F:\Git\XEngine\Build\Editor\Debug\net10.0\XEngine.Editor.exe"
PROJECT = r"F:\Git\XEngine\ZonezeroTestProject"

PROBE = (
'var sb6 = new System.Text.StringBuilder();'
'foreach (var r in Scene.Current.RootObjects) {'
'  if (!(r.Name.StartsWith("Battle_"))) continue;'
'  var a = r.GetComponent<XEngine.Runtime.Animator>();'
'  if (a == null) { sb6.Append(r.Name).Append(": no-animator\\n"); continue; }'
'  var rt = a.Runtime;'
'  sb6.Append(r.Name).Append(" fsm=").Append(rt != null);'
'  if (rt != null) sb6.Append(" idx=").Append(rt.CurrentStateIndex);'
'  sb6.Append(" clip=").Append(a.CurrentClip != null ? a.CurrentClip.Name : "-");'
'  var info = a.GetCurrentAnimatorStateInfo();'
'  sb6.Append(" nT=").Append(info.normalizedTime.ToString("F2"));'
'  var mix = a.Layer; sb6.Append(" weights=[");'
'  if (mix != null) for (int i = 0; i < mix.InputCount && i < 4; i++) {'
'      var inp = mix.GetInput(i) as XEngine.Animation.AnimationClipPlayable;'
'      sb6.Append(inp != null && inp.Clip != null ? inp.Clip.Name.Substring(0, System.Math.Min(6, inp.Clip.Name.Length)) : "?");'
'      sbt_noop();'
'      sb6.Append(\':\').Append(inp != null ? inp.Weight.ToString("F2") : "0"); }'
'  sb6.Append("]");'
'  var pelvis = r.Transform.Find? null : null;'
'  sb6.Append("\\n"); }'
'return sb6.ToString();')

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
    err = (r or {}).get("error")
    if not txt and err: txt = "ERR " + json.dumps(err)[:300]
    print(f"[{label}] {txt[:700]}", flush=True)
    return txt

send("tools/call", {"name": "runtime_state", "arguments": {}})
proc.stdin.write(b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n'); proc.stdin.flush()
time.sleep(42)

ev("OPEN", 'return XEngine.Editor.GUI.SceneView.EditorSceneManager.OpenScene("Scenes/ZonezeroBattle.scene");', 240)
time.sleep(3)

# EDIT-mode state of animators first.
ev("EDIT-FSM", '''
var sb5 = new System.Text.StringBuilder();
foreach (var r in Scene.Current.RootObjects) {
  if (!r.Name.StartsWith("Battle_Hero") && !r.Name.StartsWith("Battle_Ally")) continue;
  var a = r.GetComponent<XEngine.Runtime.Animator>();
  sb5.Append(r.Name).Append(" ctrl=").Append(a.Controller.AssetID.ToString()[..8])
     .Append(" res=").Append(a.Controller.Res != null)
     .Append(" rt=").Append(a.Runtime != null).Append("\\n"); }
return sb5.ToString();''')

rid = send("tools/call", {"name": "runtime_playmode", "arguments": {"action": "enter"}})
recv(rid, 300); time.sleep(8)

for i in range(6):
    # P = list of animators with fsm/clip/nT, via plain API only.
    ev(f"P{i}", '''
var sb6 = new System.Text.StringBuilder();
foreach (var r in Scene.Current.RootObjects) {
  if (!(r.Name.StartsWith("Battle_Hero") || r.Name.StartsWith("Battle_Ally"))) continue;
  var an = r.GetComponent<XEngine.Runtime.Animator>();
  if (an == null) { sb6.Append(r.Name).Append(": no-anim\\n"); continue; }
  var rt = an.Runtime;
  sb6.Append(r.Name).Append(" fsm=").Append(rt != null);
  if (rt != null) sb6.Append(" idx=").Append(rt.CurrentStateIndex);
  sb6.Append(" clip=").Append(an.CurrentClip != null ? an.CurrentClip.Name : "-");
  var info = an.GetCurrentAnimatorStateInfo();
  sb6.Append(" nT=").Append(info.normalizedTime.ToString("F2"));
  sb6.Append("\\n"); }
return sb6.ToString();''')
    time.sleep(0.6)

# Any 'waiting for controller clips' warnings?
lg = json.dumps((lambda rid: recv(rid, 180))(send("tools/call", {"name": "runtime_logs", "arguments": {"count": 400}})) or {})
msgs = [m for m in __import__("re").findall(r'"message": "(.*?)"', lg) if ("waiting for controller" in m or "[Animator]" in m)]
seen = set()
print("[ANIMATOR WARNINGS]")
for m in msgs[:200]:
    k = m[:60]
    if k in seen: continue
    seen.add(k); print(" *", m[:220])
if not msgs: print(" (none)")

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
