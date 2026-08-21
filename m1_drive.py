#!/usr/bin/env python3
"""M1 acceptance: copy ZZZ assets, build the 3-character placeholder scene, screenshot GameView."""
import json, subprocess, sys, time, os, shutil, threading

EDITOR = r"F:\Git\XEngine\Build\Editor\Debug\net10.0\XEngine.Editor.exe"
PROJECT = r"F:\Git\XEngine\ZonezeroTestProject"
EVIDENCE = r"F:\Git\XEngine\docs\superpowers\evidence\zonezero"

os.makedirs(EVIDENCE, exist_ok=True)
cwd = os.path.join(os.path.dirname(EDITOR), "m1-cwd")
os.makedirs(cwd, exist_ok=True)

proc = subprocess.Popen(
    [EDITOR, "--serve", "--project", PROJECT],
    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    cwd=cwd, text=True, encoding="utf-8", errors="replace", bufsize=1)

stderr_lines = []

def pump_err():
    for line in proc.stderr:
        stderr_lines.append(line)
        if "Zonezero" in line or "error" in line.lower() or "Error" in line:
            print("[err]", line[:240].rstrip().encode("ascii", "replace").decode("ascii"), flush=True)

threading.Thread(target=pump_err, daemon=True).start()
_id = 0

def send(method, params=None, notify=False):
    global _id
    msg = {"jsonrpc": "2.0", "method": method}
    if params:
        msg["params"] = params
    if not notify:
        _id += 1
        msg["id"] = _id
    proc.stdin.write(json.dumps(msg) + "\n")
    proc.stdin.flush()
    return None if notify else _id

def recv(want_id, timeout=900):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError("editor exited; stderr tail:\n" + "".join(stderr_lines[-40:]))
        line = proc.stdout.readline()
        if not line:
            raise RuntimeError("editor stdout closed; stderr tail:\n" + "".join(stderr_lines[-40:]))
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            safe = line[:180].encode("ascii", "replace").decode("ascii")
            print("[editor]", safe, flush=True)
            continue
        if msg.get("id") == want_id:
            return msg
    raise TimeoutError(f"timeout waiting for id={want_id}")

def call_tool(name, args=None, timeout=900):
    rid = send("tools/call", {"name": name, "arguments": args or {}})
    msg = recv(rid, timeout)
    if "error" in msg:
        raise RuntimeError(f"{name} error: {msg['error']}")
    result = msg["result"]
    sc = result.get("structuredContent") if isinstance(result, dict) else None
    if sc is not None:
        return sc
    for c in (result.get("content") or []) if isinstance(result, dict) else []:
        if c.get("type") == "text":
            try:
                return json.loads(c["text"])
            except json.JSONDecodeError:
                return c["text"]
    return result

def ev(code, timeout=240):
    r = call_tool("runtime_eval", {"code": code}, timeout)
    if isinstance(r, dict):
        ok = r.get("Succeeded") if r.get("Succeeded") is not None else r.get("succeeded")
        if ok:
            return r.get("Result") or r.get("result")
    return r

rid = send("initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                          "clientInfo": {"name": "zonezero-m1", "version": "1.0"}})
init = recv(rid, 300)
print("initialized:", json.dumps(init.get("result", {}).get("serverInfo", {})), flush=True)
send("notifications/initialized", notify=True)
time.sleep(8)

deadline = time.time() + 600
while time.time() < deadline:
    blob = json.dumps(call_tool("runtime_menu", {"action": "list"}, 180))
    if "Zonezero/Copy ZZZ Assets Into Project" in blob:
        print("[menu] zonezero menus ready", flush=True)
        break
    print("[menu] waiting…", flush=True)
    time.sleep(8)
else:
    print("[menu] NEVER REGISTERED", flush=True)
    print("[warnings]", json.dumps(call_tool("runtime_logs", {"minimumSeverity": "Warning"}, 60))[:4000])
    proc.kill()
    sys.exit(3)

print("[copy] invoking…", flush=True)
guid_map = os.path.join(PROJECT, "Assets", "ZZZ", "unity-guid-map.json")
if os.path.isfile(guid_map):
    print("[copy] skipped (unity-guid-map.json already in project)", flush=True)
else:
    print("[copy]", json.dumps(call_tool("runtime_menu", {
        "action": "invoke", "path": "Zonezero/Copy ZZZ Assets Into Project"}, 1200))[:400], flush=True)
print("[refresh] RefreshAll so bumped importers reimport FBX…", flush=True)
print("[refresh]", ev(
    'XEngine.Editor.EditorAssetBackend.Instance.RefreshAll(); return "ok";', 1200), flush=True)
print("[generate] invoking Generate Native Assets…", flush=True)
print("[generate]", json.dumps(call_tool("runtime_menu", {
    "action": "invoke", "path": "Zonezero/Generate Native Assets"}, 1200))[:400], flush=True)
print("[fbxmesh]", ev(r'''
var db = XEngine.Editor.EditorAssetBackend.Instance;
var sb = new System.Text.StringBuilder();
string[] paths = {
    "ZZZ/Arts/PlayerModel/\u5b89\u6bd4/Anbi.FBX",
    "ZZZ/Arts/PlayerModel/\u53ef\u7433/Corin.FBX",
    "ZZZ/Arts/PlayerModel/\u59ae\u53ef/Nostradamus.FBX"
};
foreach (var p in paths) {
    var e = db.GetEntry(p);
    sb.Append(p).Append(" guid=").Append(e==null?"null":e.Guid.ToString());
    if (e?.SubAssets != null) {
        foreach (var s in e.SubAssets) {
            if (s.Type != typeof(XEngine.Runtime.Resources.Mesh)) continue;
            var m = XEngine.Runtime.AssetDatabase.Get(s.Guid) as XEngine.Runtime.Resources.Mesh;
            sb.Append(" [").Append(s.Name).Append(" ").Append(s.Guid);
            if (m != null) {
                var sz = m.bounds.Size;
                float max = System.MathF.Max(sz.X, System.MathF.Max(sz.Y, sz.Z));
                sb.Append(" max=").Append(max.ToString("0.###")).Append(" v=").Append(m.VertexCount);
            }
            sb.Append("]");
        }
    }
    sb.Append("\n");
}
return sb.ToString();
'''), flush=True)
print("[scene] invoking…", flush=True)
print("[scene]", json.dumps(call_tool("runtime_menu", {
    "action": "invoke", "path": "Zonezero/Build Combat Demo Scene"}, 600))[:400], flush=True)
time.sleep(3)

roots = ev(
    'var s = XEngine.Runtime.Resources.Scene.Current; '
    'if (s == null) return "no-scene"; '
    'var names = new System.Collections.Generic.List<string>(); '
    'foreach (var go in s.RootObjects) names.Add(go.Name); '
    'return string.Join(",", names);')
print("[roots]", roots, flush=True)

print("[fwd]", ev(r'''
var s = XEngine.Runtime.Resources.Scene.Current;
var sb = new System.Text.StringBuilder();
void Dump(string name) {
    XEngine.Runtime.GameObject go = null;
    foreach (var r in s.RootObjects) if (r.Name == name) { go = r; break; }
    if (go == null) { sb.Append(name).Append(" missing\n"); return; }
    var f = go.Transform.Forward;
    sb.Append(name).Append(" fwd=").Append(f.X.ToString("0.###")).Append(",").Append(f.Y.ToString("0.###")).Append(",").Append(f.Z.ToString("0.###"));
    sb.Append(" euler=").Append(go.Transform.LocalEulerAngles);
    sb.Append("\n");
}
Dump("Main Camera");
Dump("Anbi");
Dump("Corin");
Dump("Nostradamus");
Dump("Claymore_1");
Dump("Claymore_2");
Dump("Claymore_3");
return sb.ToString();
'''), flush=True)

print("[smr]", ev(r'''
var s = XEngine.Runtime.Resources.Scene.Current;
var sb = new System.Text.StringBuilder();
void DumpRenderer(string root, XEngine.Runtime.GameObject n, XEngine.Runtime.Resources.Mesh mesh, System.Guid meshId, System.Collections.Generic.List<XEngine.Runtime.AssetRef<XEngine.Runtime.Resources.Material>> mats, XEngine.Vector.Quaternion localRot) {
    sb.Append(root).Append("/").Append(n.Name).Append(" mesh=");
    sb.Append(mesh == null ? "null" : (mesh.Name + ":v" + mesh.VertexCount + " id=" + meshId));
    if (mesh != null)
    {
        var wb = mesh.bounds.TransformBy(n.Transform.LocalToWorldMatrix);
        sb.Append(" aabb=").Append(wb.Min).Append("..").Append(wb.Max);
        sb.Append(" h=").Append((wb.Max.Y - wb.Min.Y).ToString("0.###"));
        sb.Append(" y0=").Append(wb.Min.Y.ToString("0.###")).Append(" y1=").Append(wb.Max.Y.ToString("0.###"));
        sb.Append(" uv=").Append(mesh.UV == null ? "no" : "yes");
    }
    sb.Append(" rot=").Append(localRot.X.ToString("0.###")).Append(",").Append(localRot.Y.ToString("0.###")).Append(",").Append(localRot.Z.ToString("0.###")).Append(",").Append(localRot.W.ToString("0.###"));
    sb.Append(" mats=").Append(mats.Count);
    foreach (var ar in mats) {
        var mat = XEngine.Runtime.AssetDatabase.Get(ar.AssetID) as XEngine.Runtime.Resources.Material;
        if (mat == null) { sb.Append(" [null]"); continue; }
        var texRef = mat._properties.GetTextureRef("_MainTex");
        var tex = XEngine.Runtime.AssetDatabase.Get(texRef.AssetID) as XEngine.Runtime.Resources.Texture2D;
        sb.Append(" [").Append(mat.Name);
        sb.Append(" sh=").Append(mat.Shader != null ? mat.Shader.Name : "?");
        sb.Append(" texId=").Append(texRef.AssetID);
        sb.Append(" tex=").Append(tex == null ? "none" : (tex.Name + " " + tex.Width + "x" + tex.Height));
        sb.Append("]");
    }
    sb.Append("\n");
}
void Walk(XEngine.Runtime.GameObject n, string root) {
    var r = n.GetComponent<XEngine.Runtime.SkinnedMeshRenderer>();
    if (r != null) {
        var m = XEngine.Runtime.AssetDatabase.Get(r.SharedMesh.AssetID) as XEngine.Runtime.Resources.Mesh;
        DumpRenderer(root, n, m, r.SharedMesh.AssetID, r.Materials, n.Transform.LocalRotation);
    }
    var mr = n.GetComponent<XEngine.Runtime.MeshRenderer>();
    if (mr != null && r == null) {
        var m = XEngine.Runtime.AssetDatabase.Get(mr.Mesh.AssetID) as XEngine.Runtime.Resources.Mesh;
        DumpRenderer(root, n, m, mr.Mesh.AssetID, mr.Materials, n.Transform.LocalRotation);
    }
    foreach (var c in n.Children) Walk(c, root);
}
foreach (var go in s.RootObjects) Walk(go, go.Name);
return sb.ToString();
'''), flush=True)

print("[loadtex]", ev(r'''
int n = 0, ok = 0;
var s = XEngine.Runtime.Resources.Scene.Current;
void Walk(XEngine.Runtime.GameObject go) {
    var r = go.GetComponent<XEngine.Runtime.SkinnedMeshRenderer>();
    if (r != null) {
        foreach (var ar in r.Materials) {
            var mat = ar.Res;
            if (mat == null) continue;
            var id = mat._properties.GetTextureRef("_MainTex").AssetID;
            if (id == System.Guid.Empty) continue;
            n++;
            if (XEngine.Runtime.AssetDatabase.Get(id) is XEngine.Runtime.Resources.Texture2D)
                ok++;
        }
    }
    foreach (var c in go.Children) Walk(c);
}
foreach (var go in s.RootObjects) Walk(go);
return "tex " + ok + "/" + n;
'''), flush=True)

print("[gv]", json.dumps(call_tool("runtime_menu", {
    "action": "invoke", "path": "Window/General/New Game View"}, 120))[:200], flush=True)
time.sleep(2)

try:
    print("[play]", json.dumps(call_tool("runtime_playmode", {"action": "enter"}, 180))[:200], flush=True)
    time.sleep(6)
except Exception as ex:
    print("[play] skipped:", ex, flush=True)
    time.sleep(2)

shot = call_tool("runtime_screenshot", {}, 300)
print("[shot]", json.dumps(shot)[:400], flush=True)
blob = json.dumps(shot)
paths = [f for f in blob.replace("\\\\", "\\").split('"') if f.lower().endswith(".png")]
out = os.path.join(EVIDENCE, "m1-zonezero-placeholder.png")
copied = False
for p in paths:
    p = p.encode("utf-8").decode("unicode_escape") if "\\u" in p else p
    if os.path.isfile(p):
        shutil.copyfile(p, out)
        print("saved", out, "from", p, "size", os.path.getsize(out), flush=True)
        copied = True
        break
if not copied:
    # structuredContent.Path
    if isinstance(shot, dict):
        p = shot.get("Path") or shot.get("path")
        if p and os.path.isfile(p):
            shutil.copyfile(p, out)
            print("saved", out, "from", p, flush=True)
            copied = True
if not copied:
    print("NO SCREENSHOT FILE", flush=True)

print("[errors]", json.dumps(call_tool("runtime_logs", {"minimumSeverity": "Error"}, 60))[:4000], flush=True)
info = json.dumps(call_tool("runtime_logs", {"minimumSeverity": "Info"}, 60))
for item in info.split("Zonezero"):
    if item:
        print("[zz] Zonezero" + item[:240], flush=True)

try:
    proc.stdin.close()
    proc.wait(timeout=20)
except subprocess.TimeoutExpired:
    proc.kill()

sys.exit(0 if copied else 4)
