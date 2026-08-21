#!/usr/bin/env python3
"""Final isolation: compare, for the SAME skinned bone, the computed skin matrix
(worldToLocal * boneWorld * bindPose) between prefab.Instantiate and model.Instantiate
of Anbi — inside the identical PreviewRenderer environment. Any 180° delta shows up here."""
import json, subprocess, sys, time, os

EDITOR = r"F:\Git\XEngine\Build\Editor\Debug\net10.0\XEngine.Editor.exe"
PROJECT = r"F:\Git\XEngine\ZonezeroTestProject"

CODE = r"""
var db = XEngine.Editor.EditorAssetBackend.Instance;
var prefabEntry = db.GetEntry("ZZZ/Prefab/Anbi.prefab");
var fbxEntry = db.GetEntry("ZZZ/Arts/PlayerModel/安比/Anbi.fbx");
var prefab = XEngine.Runtime.AssetDatabase.Get(prefabEntry.Guid) as XEngine.Runtime.Resources.PrefabAsset;
var model = XEngine.Runtime.AssetDatabase.Get(fbxEntry.Guid) as XEngine.Runtime.Resources.Model;
if (prefab == null || model == null) return "missing " + (prefab == null) + (model == null);

string Dump(XEngine.Runtime.GameObject g, string tag)
{
    var smr = g.GetComponentInChildren<XEngine.Runtime.SkinnedMeshRenderer>();
    if (smr == null) return tag + ":no-smr";
    var mesh = smr.SharedMesh.Res;
    if (mesh == null) return tag + ":no-mesh";
    var bones = smr.Bones;
    if (bones == null) return tag + ":no-bones";
    var sb = new System.Text.StringBuilder();
    sb.Append(tag).Append(": bones=").Append(bones.Length);
    // Find Bip001 Pelvis bone index by name.
    int idx = -1;
    for (int i = 0; i < bones.Length; i++)
    {
        if (bones[i] != null && bones[i].GameObject.Name == "Bip001 Pelvis") { idx = i; break; }
    }
    sb.Append(" pelvisIdx=").Append(idx);
    if (idx >= 0 && idx < mesh.BindPoses.Length)
    {
        var boneWorld = bones[idx].LocalToWorldMatrix;
        var skin = smr.Transform.WorldToLocalMatrix * boneWorld * mesh.BindPoses[idx];
        var r = XEngine.Vector.Quaternion.FromMatrix(skin);
        float ang = 2f * System.MathF.Acos(System.MathF.Min(1f, System.MathF.Abs(r.W))) * 180f / System.MathF.PI;
        sb.Append(" skinQuat=(").Append(r.X.ToString("F3")).Append(',').Append(r.Y.ToString("F3"))
          .Append(',').Append(r.Z.ToString("F3")).Append(',').Append(r.W.ToString("F3"))
          .Append(") angle=").Append(ang.ToString("F0"))
          .Append(" skinPos=").Append(skin.Translation.ToString())
          .Append(" bindPosePos=").Append(mesh.BindPoses[idx].Translation.ToString())
          .Append(" boneWorldPos=").Append(bones[idx].Position.ToString());
    }
    return sb.ToString();
}

using (var p = new XEngine.Editor.GUI.PreviewRenderer(256, 256))
{
    p.SetupForPrefab(prefab);
    string a = Dump(p.SubjectGameObject, "PREFAB");
    p.Render();
    string a2 = Dump(p.SubjectGameObject, "PREFAB-rendered");
    return a + " || " + a2;
}
"""

CODE2 = r"""
var db = XEngine.Editor.EditorAssetBackend.Instance;
var fbxEntry = db.GetEntry("ZZZ/Arts/PlayerModel/安比/Anbi.fbx");
var model = XEngine.Runtime.AssetDatabase.Get(fbxEntry.Guid) as XEngine.Runtime.Resources.Model;
if (model == null) return "missing-model";
using (var p = new XEngine.Editor.GUI.PreviewRenderer(256, 256))
{
    p.SetupForModel(model);
    var g = p.SubjectGameObject;
    var smr = g.GetComponentInChildren<XEngine.Runtime.SkinnedMeshRenderer>();
    if (smr == null) return "no-smr";
    var mesh = smr.SharedMesh.Res;
    var bones = smr.Bones;
    var sb = new System.Text.StringBuilder("MODEL: bones=").Append(bones?.Length ?? -1);
    int idx = -1;
    for (int i = 0; i < bones.Length; i++)
        if (bones[i] != null && bones[i].GameObject.Name == "Bip001 Pelvis") { idx = i; break; }
    sb.Append(" pelvisIdx=").Append(idx);
    if (idx >= 0 && idx < mesh.BindPoses.Length)
    {
        var skin = smr.Transform.WorldToLocalMatrix * bones[idx].LocalToWorldMatrix * mesh.BindPoses[idx];
        var r = XEngine.Vector.Quaternion.FromMatrix(skin);
        float ang = 2f * System.MathF.Acos(System.MathF.Min(1f, System.MathF.Abs(r.W))) * 180f / System.MathF.PI;
        sb.Append(" skinQuat=(").Append(r.X.ToString("F3")).Append(',').Append(r.Y.ToString("F3"))
          .Append(',').Append(r.Z.ToString("F3")).Append(',').Append(r.W.ToString("F3"))
          .Append(") angle=").Append(ang.ToString("F0"))
          .Append(" skinPos=").Append(skin.Translation.ToString())
          .Append(" bindPosePos=").Append(mesh.BindPoses[idx].Translation.ToString())
          .Append(" boneWorldPos=").Append(bones[idx].Position.ToString());
    }
    p.Render();
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
                          "clientInfo": {"name": "zz-skin", "version": "1.0"}})
if recv(rid, 300) is None:
    print("INIT_FAILED"); proc.kill(); sys.exit(2)
send("notifications/initialized", notify=True)
print("init ok", flush=True)
time.sleep(10)

print("[prefab]", json.dumps(call_tool("runtime_eval", {"code": CODE}, 300))[:700], flush=True)
print("[model]", json.dumps(call_tool("runtime_eval", {"code": CODE2}, 300))[:700], flush=True)

try:
    proc.stdin.close(); proc.wait(timeout=20)
except subprocess.TimeoutExpired:
    proc.kill()
