#!/usr/bin/env python3
"""Numeric verdict: in PreviewRenderer.SetupForPrefab subject, is the head bone above the
pelvis bone? Also dumps the SMR skinned-bone state for both prefabs."""
import json, subprocess, sys, time, os

EDITOR = r"F:\Git\XEngine\Build\Editor\Debug\net10.0\XEngine.Editor.exe"
PROJECT = r"F:\Git\XEngine\ZonezeroTestProject"

DIAG = r"""
var e = XEngine.Editor.EditorAssetBackend.Instance.GetEntry("ZZZ/Prefab/ANBI.prefab");
var prefab = XEngine.Runtime.AssetDatabase.Get(e.Guid) as XEngine.Runtime.Resources.PrefabAsset;
if (prefab == null) return "not-prefab";
using (var p = new XEngine.Editor.GUI.PreviewRenderer(256, 256))
{
    p.SetupForPrefab(prefab);
    p.Render();
    var g = p.SubjectGameObject;
    if (g == null) return "no-subject";
    var sb = new System.Text.StringBuilder();
    XEngine.Vector.Transform? head = null, pelvis = null;
    var stack = new System.Collections.Generic.Stack<XEngine.Runtime.GameObject>();
    stack.Push(g);
    var heads = new System.Collections.Generic.List<XEngine.Vector.Transform>();
    var pelvises = new System.Collections.Generic.List<XEngine.Vector.Transform>();
    while (stack.Count > 0)
    {
        var cur = stack.Pop();
        if (cur.Name.EndsWith("Head")) heads.Add(cur.Transform);
        if (cur.Name.EndsWith("Pelvis")) pelvises.Add(cur.Transform);
        foreach (var ch in cur.Children) stack.Push(ch);
    }
    sb.Append("rootScale=").Append(g.Transform.LocalScale.ToString()).Append(' ');
    if (heads.Count > 0 && pelvises.Count > 0)
        sb.Append("headY=").Append(heads[0].Position.Y.ToString("F3"))
          .Append(" pelvisY=").Append(pelvises[0].Position.Y.ToString("F3"))
          .Append(" headWorld=").Append(heads[0].Position.ToString())
          .Append(" headLocal=").Append(heads[0].LocalPosition.ToString());
    else
    {
        sb.Append("bones-not-found; sample=");
        int n = 0;
        stack.Push(g);
        while (stack.Count > 0 && n < 12)
        {
            var cur = stack.Pop();
            if (n++ < 12) sb.Append(cur.Name).Append("@Y=").Append(cur.Transform.Position.Y.ToString("F2")).Append(' ');
            foreach (var ch in cur.Children) stack.Push(ch);
        }
    }
    var smr = g.GetComponentInChildren<XEngine.Runtime.SkinnedMeshRenderer>();
    sb.Append(" | smr=").Append(smr != null ? "yes" : "no");
    if (smr != null)
    {
        var an = g.GetComponentInChildren<XEngine.Runtime.Animator>();
        sb.Append(" animator=").Append(an == null ? "none" : (an.Enabled ? "on" : "off"));
    }
    return sb.ToString();
}
"""

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

rid = send("initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                          "clientInfo": {"name": "zz-numeric", "version": "1.0"}})
if recv(rid, 300) is None:
    print("INIT_FAILED"); proc.kill(); sys.exit(2)
send("notifications/initialized", notify=True)
print("init ok", flush=True)
time.sleep(10)

print("[anbi]", json.dumps(call_tool("runtime_eval", {"code": DIAG.replace("ANBI.prefab", "Anbi.prefab")}, 300))[:600], flush=True)
print("[claymore]", json.dumps(call_tool("runtime_eval", {"code": DIAG.replace("ANBI.prefab", "Claymore.prefab")}, 300))[:600], flush=True)

try:
    proc.stdin.close(); proc.wait(timeout=20)
except subprocess.TimeoutExpired:
    proc.kill()
