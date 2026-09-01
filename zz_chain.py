#!/usr/bin/env python3
"""Full bone-chain local TRS dump + skinned bounds: resolve the unit-scale question."""
import json, subprocess, time, os

EDITOR = r"F:\Git\XEngine\Build\Editor\Debug\net10.0\XEngine.Editor.exe"
PROJECT = r"F:\Git\XEngine\ZonezeroTestProject"

CODE = (
    'var r0 = Scene.Current.RootObjects.Where(r => r.Name.StartsWith("Test_")).First();'
    'var sb = new System.Text.StringBuilder();'
    'sb.Append("root pos=").Append(r0.Transform.Position.ToString())'
    '  .Append(" scale=").Append(r0.Transform.LocalScale.ToString()).Append("\\n");'
    'var names = new[]{"Avatar_Female_Size01_Corin_Model","Bip001","Bip001 Pelvis",'
    '"Bip001 L Thigh","Bip001 L Calf","Bip001 L Foot","Bip001 R Hand","Bip001 Head"};'
    'XEngine.Runtime.SkinnedMeshRenderer? smr = null;'
    'foreach (var c in r0.GetComponentsInChildren<XEngine.Runtime.SkinnedMeshRenderer>()) { smr = c; break; }'
    'foreach (var nm in names) {'
    '  XEngine.Vector.Transform? t = null;'
    '  var cur = r0.Transform;'
    '  for (int d = 0; d < 12 && t == null; d++) {'
    '    for (int i = 0; i < cur.ChildCount; i++) {'
    '      var ch = cur.GetChild(i);'
    '      if (ch.GameObject != null && ch.GameObject.Name == nm) { t = ch; break; } }'
    '    if (t == null && cur.ChildCount > 0) cur = cur.GetChild(0); }'
    '  if (t == null) { sb.Append(nm).Append(": not found\\n"); continue; }'
    '  sb.Append(nm).Append(" lp=").Append(t.LocalPosition.ToString())'
    '    .Append(" ls=").Append(t.LocalScale.ToString())'
    '    .Append(" wpos=").Append(t.Position.ToString()).Append("\\n"); }'
    'var BF = System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.Instance;'
    'var bb = typeof(XEngine.Runtime.SkinnedMeshRenderer).GetField("_cachedBounds", BF)?.GetValue(smr);'
    'sb.Append("skinnedBounds=").Append(bb != null ? bb.ToString() : "null").Append("\\n");'
    'return sb.ToString();')

proc = subprocess.Popen([EDITOR, "--serve", "--project", PROJECT],
                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                        cwd=os.path.dirname(EDITOR))
import threading
_responses = {}
elog = open(r"diag/zz_chain_stdio.log", "ab")
def _drain():
    try:
        for line in proc.stdout:
            elog.write(line); elog.flush()
            s2 = line.decode("utf-8", errors="replace").strip()
            if not s2: continue
            try: m = json.loads(s2)
            except json.JSONDecodeError: continue
            if isinstance(m.get("id"), int):
                _responses[m["id"]] = m
    except Exception:
        pass
threading.Thread(target=_drain, daemon=True).start()

_id = 0
def call_eval(code, timeout=240):
    global _id
    _id += 1
    my = _id
    proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": my, "method": "tools/call",
        "params": {"name": "runtime_eval", "arguments": {"code": code}}}).encode() + b"\n")
    proc.stdin.flush()
    dl = time.time() + timeout
    while time.time() < dl:
        if my in _responses:
            m = _responses.pop(my)
            try:
                c = ((m.get("result") or {}).get("content") or [])
                return "".join(x.get("text") or "" for x in c)
            except Exception:
                pass
        time.sleep(0.05)
    return "TIMEOUT"

send0 = call_eval
send0('return "ready";')
print("[open]", send0('return XEngine.Editor.GUI.SceneView.EditorSceneManager.OpenScene("Scenes/AnimSingleTest.scene");')[:60], flush=True)
time.sleep(3)
call_eval('XEngine.Editor.Core.EditorApplication.Instance!.EnterPlayMode(); "entering";')
time.sleep(8)

print("[CHAIN]", send0(CODE)[:1500], flush=True)

try:
    call_eval('XEngine.Editor.Core.EditorApplication.Instance!.ExitPlayMode(); "exiting";')
    time.sleep(3)
except Exception:
    pass
try:
    proc.stdin.close(); proc.wait(timeout=15)
except Exception:
    proc.kill()
