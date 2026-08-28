#!/usr/bin/env python3
"""M3 toon-complete acceptance, split sessions:
  A: gen -> bake -> build -> feature-on (rim/spec/shrink + persisted outline material) -> SaveAs
  GL: OpenGL screenshot of the saved scene
  VK: Vulkan screenshot of the saved scene"""
import json, subprocess, sys, time, os, shutil, argparse

EDITOR = r"F:\Git\XEngine\Build\Editor\Debug\net10.0\XEngine.Editor.exe"
PROJECT = r"F:\Git\XEngine\ZonezeroTestProject"
OUT = r"F:\Git\XEngine\ZonezeroTestProject\diag"

BAKE_EVAL = r"""
var db = XEngine.Editor.EditorAssetBackend.Instance;
int fixedCount = 0, skipped = 0;
foreach (var entry in db.GetAllEntries())
{
    bool isZzzFbx = entry.Path != null && entry.Path.StartsWith("ZZZ/") && entry.Path.EndsWith(".FBX", System.StringComparison.OrdinalIgnoreCase);
    if (!isZzzFbx || entry.SubAssets == null) continue;
    foreach (var sub in entry.SubAssets)
    {
        if (XEngine.Runtime.AssetDatabase.Get(sub.Guid) is not XEngine.Runtime.Resources.Mesh mesh || mesh.IsDisposed) continue;
        var normals = mesh.Normals; var vertices = mesh.Vertices;
        if (normals.Length == 0 || vertices.Length == 0) { skipped++; continue; }
        var sums = new System.Collections.Generic.Dictionary<long, XEngine.Vector.Float3>();
        var keys = new long[vertices.Length];
        for (int v = 0; v < vertices.Length; v++)
        {
            var p = vertices[v];
            long x = (long)System.MathF.Round(p.X * 16384f), y = (long)System.MathF.Round(p.Y * 16384f), z = (long)System.MathF.Round(p.Z * 16384f);
            long key = (x * 73856093L) ^ (y * 19349663L) ^ (z * 83492791L);
            keys[v] = key;
            sums.TryGetValue(key, out var sum);
            sums[key] = sum + normals[v];
        }
        var colors = new XEngine.Vector.Color[vertices.Length];
        for (int v = 0; v < vertices.Length; v++)
        {
            var s = sums[keys[v]];
            float lenSq = XEngine.Vector.Float3.LengthSquared(s);
            var n = lenSq > 1e-12f ? s * (1f / System.MathF.Sqrt(lenSq)) : XEngine.Vector.Float3.UnitY;
            colors[v] = new XEngine.Vector.Color(n.X, n.Y, n.Z, 1f);
        }
        mesh.Colors = colors;
        db.SaveAsset(mesh);
        fixedCount++;
    }
}
return "baked=" + fixedCount + " skipped=" + skipped;
"""

FEATURE_EVAL = r"""
var sb = new System.Text.StringBuilder();
var db = XEngine.Editor.EditorAssetBackend.Instance;
int tweaked = 0;
foreach (var e in db.GetAllEntries())
{
    if (e.Path == null || !e.Path.StartsWith("ZZZ/Arts/PlayerModel/") || !e.Path.EndsWith(".mat")) continue;
    if (!e.Path.Contains("Anbi")) continue;
    if (XEngine.Runtime.AssetDatabase.Get(e.Guid) is not XEngine.Runtime.Resources.Material mat) continue;
    if (mat.Shader == null || mat.Shader.Name == null || !mat.Shader.Name.Contains("Toon") || mat.Shader.Name.Contains("Outline")) continue;
    mat.SetColor("_RimColor", new XEngine.Vector.Color(0.9f, 0.85f, 0.95f, 0.6f));
    mat.SetFloat("_RimEnabled", 1f);
    mat.SetFloat("_MainLightSpecular", 1f);
    mat.SetFloat("_SpecularMidPoint", 0.86f);
    mat.SetFloat("_SpecularSmoothness", 0.03f);
    mat.SetFloat("_ShrinkSize", -0.15f);
    db.SaveAsset(mat);
    tweaked++;
}
sb.Append("tweaked=").Append(tweaked);
bool saved = XEngine.Editor.GUI.SceneView.EditorSceneManager.SaveAs("ZZZ/Scenes/M3Demo.scene");
sb.Append(" sceneSaved=").Append(saved);
return sb.ToString();
"""

def start_editor(extra_args):
    proc = subprocess.Popen([EDITOR, "--serve", "--project", PROJECT] + extra_args,
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            cwd=os.path.dirname(EDITOR))
    state = {"id": 0}
    def send(method, params=None, notify=False):
        msg = {"jsonrpc": "2.0", "method": method}
        if params: msg["params"] = params
        if not notify:
            state["id"] += 1; msg["id"] = state["id"]
        proc.stdin.write(json.dumps(msg).encode("utf-8") + b"\n"); proc.stdin.flush()
        return state["id"] if not notify else None
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
                              "clientInfo": {"name": "zz-m3", "version": "1"}})
    if recv(rid, 300) is None:
        raise RuntimeError("INIT_FAILED")
    send("notifications/initialized", notify=True)
    return proc, call_tool

def stop_editor(proc):
    try:
        proc.stdin.close(); proc.wait(timeout=25)
    except subprocess.TimeoutExpired:
        proc.kill()

def session_a():
    proc, call = start_editor([])
    try:
        time.sleep(40)
        print("[A][gen]", json.dumps(call("runtime_menu", {"action": "invoke", "path": "Zonezero/Generate Native Assets"}, 1200))[:120], flush=True)
        time.sleep(2)
        print("[A][bake]", json.dumps(call("runtime_eval", {"code": BAKE_EVAL}, 900))[:150], flush=True)
        time.sleep(2)
        print("[A][build]", json.dumps(call("runtime_menu", {"action": "invoke", "path": "Zonezero/Build Combat Demo Scene"}, 900))[:120], flush=True)
        time.sleep(3)
        print("[A][feat]", json.dumps(call("runtime_eval", {"code": FEATURE_EVAL}, 900))[:250], flush=True)
        logs = call("runtime_logs", {"count": 400, "level": "Error"}, 120)
        print("[A][errors]", json.dumps(logs)[:250], flush=True)
    finally:
        stop_editor(proc)

BACKEND_EVAL = r"""
var dev = XEngine.Runtime.Graphics.Device;
string inner = "null";
if (dev != null)
{
    var innerProp = dev.GetType().GetProperty("Inner");
    var innerDev = innerProp != null ? innerProp.GetValue(dev) : null;
    inner = innerDev != null ? innerDev.GetType().Name : "none";
}
return "preferred=" + XEngine.Runtime.RHI.GraphicsBackendSelection.Preferred + " device=" + (dev != null ? dev.GetType().Name : "null") + " inner=" + inner;
"""

def session_shot(tag, extra_args, out_name):
    proc, call = start_editor(extra_args)
    try:
        time.sleep(40)
        if extra_args:
            try:
                info = json.dumps(call("runtime_eval", {"code": BACKEND_EVAL}, 120))[:160]
                print(f"[{tag}][backend]", info, flush=True)
            except Exception as ex:
                print(f"[{tag}][backend-probe-failed] {ex}", flush=True)
        opened = call("runtime_menu", {"action": "invoke", "path": "Window/General/New Game View"}, 120)
        print(f"[{tag}][gameview]", json.dumps(opened)[:100], flush=True)
        time.sleep(5)
        shot = call("runtime_screenshot", {"width": 1280, "height": 720}, 300)
        blob = json.dumps(shot)
        print(f"[{tag}][shot-raw]", blob[:160], flush=True)
        paths = [f for f in blob.split('"') if f.endswith(".png")]
        if paths:
            src = max(paths, key=os.path.getmtime)
            dst = os.path.join(OUT, out_name)
            shutil.copyfile(src, dst)
            print(f"[{tag}] saved {out_name} {os.path.getsize(dst)}B", flush=True)
        logs = call("runtime_logs", {"count": 200, "level": "Error"}, 120)
        print(f"[{tag}][errors]", json.dumps(logs)[:250], flush=True)
    finally:
        stop_editor(proc)

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", choices=["a", "gl", "vk"], required=True)
    args = ap.parse_args()
    if args.stage == "a":
        session_a()
    elif args.stage == "gl":
        session_shot("gl", [], "m3-toon-complete-gl.png")
    else:
        session_shot("vk", ["--graphics=vulkan"], "m3-toon-complete-vk.png")
