#!/usr/bin/env python3
"""Inspect instantiated character bounds in the saved M1 scene."""
import json, subprocess, sys, time, os, threading

EDITOR = r"F:\Git\XEngine\Build\Editor\Debug\net10.0\XEngine.Editor.exe"
PROJECT = r"F:\Git\XEngine\ZonezeroTestProject"
cwd = os.path.join(os.path.dirname(EDITOR), "m1-diag-cwd")
os.makedirs(cwd, exist_ok=True)

proc = subprocess.Popen(
    [EDITOR, "--serve", "--project", PROJECT],
    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    cwd=cwd, text=True, encoding="utf-8", errors="replace", bufsize=1)
stderr_lines = []
threading.Thread(target=lambda: [stderr_lines.append(l) for l in proc.stderr], daemon=True).start()
_id = 0

def send(method, params=None, notify=False):
    global _id
    msg = {"jsonrpc": "2.0", "method": method}
    if params: msg["params"] = params
    if not notify:
        _id += 1; msg["id"] = _id
    proc.stdin.write(json.dumps(msg) + "\n"); proc.stdin.flush()
    return None if notify else _id

def recv(want_id, timeout=300):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError("editor exited")
        line = proc.stdout.readline()
        if not line: raise RuntimeError("stdout closed")
        line = line.strip()
        if not line: continue
        try: msg = json.loads(line)
        except json.JSONDecodeError: continue
        if msg.get("id") == want_id: return msg
    raise TimeoutError()

def call(name, args=None, timeout=300):
    rid = send("tools/call", {"name": name, "arguments": args or {}})
    msg = recv(rid, timeout)
    result = msg.get("result", msg)
    sc = result.get("structuredContent") if isinstance(result, dict) else None
    return sc if sc is not None else result

def ev(code, timeout=180):
    r = call("runtime_eval", {"code": code}, timeout)
    if isinstance(r, dict):
        ok = r.get("Succeeded") if r.get("Succeeded") is not None else r.get("succeeded")
        if ok: return r.get("Result") or r.get("result")
        return r.get("Error") or r.get("error") or r
    return r

rid = send("initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                          "clientInfo": {"name": "zz-bounds", "version": "1.0"}})
recv(rid, 300)
send("notifications/initialized", notify=True)
time.sleep(10)
call("runtime_menu", {"action": "invoke", "path": "Zonezero/Build Combat Demo Scene"}, 180)
time.sleep(2)
print("ROOTS", ev('''
var s = XEngine.Runtime.Resources.Scene.Current;
var sb = new System.Text.StringBuilder();
foreach (var go in s.RootObjects) {
    sb.Append(go.Name).Append(" pos=").Append(go.Transform.Position).Append(" scale=").Append(go.Transform.LossyScale);
    int smr=0; int bones=0; string meshInfo="";
    void Walk(XEngine.Runtime.GameObject n) {
        var r = n.GetComponent<XEngine.Runtime.SkinnedMeshRenderer>();
        if (r != null) {
            smr++;
            var m = r.SharedMesh.Res;
            if (m != null) meshInfo += n.Name + "->" + m.Name + ":v" + m.VertexCount + "/b" + (m.BindPoses==null?0:m.BindPoses.Length) + ";";
            bones += r.BonePaths==null?0:r.BonePaths.Length;
        }
        foreach (var c in n.Children) Walk(c);
    }
    Walk(go);
    sb.Append(" smr=").Append(smr).Append(" bonePaths=").Append(bones).Append(" ").Append(meshInfo).Append("\\n");
}
return sb.ToString();
'''), flush=True)

try:
    proc.stdin.close(); proc.wait(timeout=15)
except subprocess.TimeoutExpired:
    proc.kill()
