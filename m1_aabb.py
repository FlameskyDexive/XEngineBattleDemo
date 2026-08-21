#!/usr/bin/env python3
"""Final numeric: skinned-vertex AABB (CPU evaluation of worldToLocal*boneWorld*bindPose*V)
for the preview subject; positive-Y-dominated AABB = upright, negative = flipped."""
import json, subprocess, sys, time, os

EDITOR = r"F:\Git\XEngine\Build\Editor\Debug\net10.0\XEngine.Editor.exe"
PROJECT = r"F:\Git\XEngine\ZonezeroTestProject"

CODE = r"""
var db = XEngine.Editor.EditorAssetBackend.Instance;
var fbxEntry = db.GetEntry("ZZZ/Arts/PlayerModel/安比/Anbi.fbx");
var model = XEngine.Runtime.AssetDatabase.Get(fbxEntry.Guid) as XEngine.Runtime.Resources.Model;
if (model == null) return "missing-model";
using (var p = new XEngine.Editor.GUI.PreviewRenderer(256, 256))
{
    p.SetupForModel(model);
    p.Render();
    var g = p.SubjectGameObject;
    var smr = g.GetComponentInChildren<XEngine.Runtime.SkinnedMeshRenderer>();
    var mesh = smr.SharedMesh.Res;
    var bones = smr.Bones;
    // World matrix of the SMR node.
    var smrWorld = smr.Transform.LocalToWorldMatrix;
    float minY = float.MaxValue, maxY = float.MinValue, minX = float.MaxValue, maxX = float.MinValue, minZ = float.MaxValue, maxZ = float.MinValue;
    var verts = mesh.Vertices;
    int n = verts.Length;
    for (int v = 0; v < n; v += 7)   // sample every 7th vertex
    {
        // First-bone approximation: use bone 2 (pelvis region) for a coarse orientation check.
        var bw = bones[2].LocalToWorldMatrix;
        var skin = smr.Transform.WorldToLocalMatrix * bw * mesh.BindPoses[2];
        var world = smrWorld * skin;
        var pos = XEngine.Vector.Float4x4.TransformPoint(verts[v], world);
        if (pos.Y < minY) minY = pos.Y; if (pos.Y > maxY) maxY = pos.Y;
        if (pos.X < minX) minX = pos.X; if (pos.X > maxX) maxX = pos.X;
        if (pos.Z < minZ) minZ = pos.Z; if (pos.Z > maxZ) maxZ = pos.Z;
    }
    return "skinnedAABB X=(" + minX.ToString("F2") + "," + maxX.ToString("F2") + ") Y=(" +
           minY.ToString("F2") + "," + maxY.ToString("F2") + ") Z=(" + minZ.ToString("F2") + "," + maxZ.ToString("F2") + ")" +
           " | meshAABB " + mesh.bounds.ToString() +
           " | smrNodeRot " + smr.Transform.LocalRotation.ToString();
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
                          "clientInfo": {"name": "zz-aabb", "version": "1.0"}})
if recv(rid, 300) is None:
    print("INIT_FAILED"); proc.kill(); sys.exit(2)
send("notifications/initialized", notify=True)
print("init ok", flush=True)
time.sleep(10)

print("[aabb]", json.dumps(call_tool("runtime_eval", {"code": CODE}, 300))[:600], flush=True)

try:
    proc.stdin.close(); proc.wait(timeout=20)
except subprocess.TimeoutExpired:
    proc.kill()
