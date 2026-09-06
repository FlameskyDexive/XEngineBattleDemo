// This file is part of the XEngine Game Engine
// Licensed under the MIT License. See the LICENSE file in the project root for details.

using System;
using System.Collections.Generic;

using XEngine.Runtime;
using XEngine.Runtime.Resources;
using XEngine.Vector;

namespace XEngine.Zonezero.Vfx;

/// <summary>
/// Spawns rpgvfx package effect prefabs at runtime by asset path (e.g.
/// "Packages/com.xengine.rpgvfx/Assets/Prefabs/Magic_circles_Prefabs_Magic_circle_1.prefab").
/// Path→GUID resolution follows the BattleHUD recipe (reflection against the current asset
/// backend, because <c>GetEntry(string)</c> exists only on the editor backend type and GUIDs
/// are machine-local). Every failure path returns null so callers can fall back to the
/// procedural <see cref="ZonezeroVfx"/> effects — including in unit tests, which run without
/// any backend at all.
/// </summary>
public static class RpgVfxSpawner
{
    private static readonly Dictionary<string, Guid> PathGuidCache = new(StringComparer.OrdinalIgnoreCase);

    /// <summary>Total instances spawned since domain start (telemetry for tests/acceptance).</summary>
    public static int SpawnCount { get; private set; }

    /// <summary>
    /// Instantiates the prefab at <paramref name="path"/>, places it at
    /// <paramref name="position"/> facing <paramref name="forward"/>, applies
    /// <paramref name="scale"/>, and schedules disposal after <paramref name="lifetimeSeconds"/>
    /// (looping prefabs need a finite lifetime; one-shots are disposed on the same timer, which
    /// is harmless because they finish earlier). Returns null on any resolution failure.
    /// </summary>
    public static GameObject? Spawn(string path, Float3 position, Float3 forward, float scale = 1f,
        float lifetimeSeconds = 4f)
    {
        if (string.IsNullOrEmpty(path)) return null;

        PrefabAsset? prefab = ResolvePrefab(path);
        if (prefab is null) return null;

        GameObject? instance;
        try
        {
            instance = prefab.Instantiate();
        }
        catch
        {
            return null; // corrupt prefab data → procedural fallback
        }
        if (instance is null) return null;

        Scene.Current?.Add(instance);
        instance.Transform.Position = position;
        if (Float3.LengthSquared(forward) > 1e-6f)
        {
            forward = Float3.Normalize(forward);
            // Hovl effects face +Z; yaw the instance so +Z aligns with the requested forward.
            float yaw = MathF.Atan2(forward.X, forward.Z);
            instance.Transform.LocalRotation = Quaternion.AxisAngle(Float3.UnitY, yaw);
        }
        if (MathF.Abs(scale - 1f) > 1e-4f)
            instance.Transform.LocalScale = new Float3(scale, scale, scale);

        RecycleAfter(instance, MathF.Max(0.1f, lifetimeSeconds));
        SpawnCount++;
        return instance;
    }

    /// <summary>Warm path→prefab resolution (call during battle setup; failures just cache empty).</summary>
    public static void Warmup(IEnumerable<string> paths)
    {
        foreach (string path in paths)
            ResolvePrefab(path);
    }

    /// <summary>Test hook — clears caches and counters.</summary>
    public static void ResetCache()
    {
        PathGuidCache.Clear();
        SpawnCount = 0;
    }

    private static PrefabAsset? ResolvePrefab(string path)
    {
        if (PathGuidCache.TryGetValue(path, out Guid cached))
            return cached == Guid.Empty ? null : AssetDatabase.Get(cached) as PrefabAsset;

        Guid guid = ResolvePathGuid(path);
        PathGuidCache[path] = guid;
        if (guid == Guid.Empty) return null;
        return AssetDatabase.Get(guid) as PrefabAsset;
    }

    /// <summary>Path → asset GUID through the current backend (reflection: editor-only API).</summary>
    private static Guid ResolvePathGuid(string path)
    {
        try
        {
            var backend = AssetDatabase.Current;
            var getEntry = backend?.GetType().GetMethod("GetEntry", new[] { typeof(string) });
            var entry = getEntry?.Invoke(backend, new object[] { path });
            if (entry is null) return Guid.Empty;
            return (Guid?)entry.GetType().GetField("Guid")?.GetValue(entry) ?? Guid.Empty;
        }
        catch
        {
            return Guid.Empty;
        }
    }

    /// <summary>Scene-owned disposal timer — one tracker object per scene, no per-instance coroutines.</summary>
    private static void RecycleAfter(GameObject instance, float seconds)
    {
        var tracker = TrackerRuntime.Ensure();
        tracker?.Track(instance, seconds);
    }

    private sealed class TrackerRuntime : MonoBehaviour
    {
        private readonly List<GameObject> _instances = new();
        private readonly List<float> _deadlines = new();

        private static TrackerRuntime? _current;

        public static TrackerRuntime? Ensure()
        {
            var scene = Scene.Current;
            if (scene == null) return null;
            if (_current is { IsDisposed: false }) return _current;
            var go = new GameObject("RpgVfxTracker");
            _current = go.AddComponent<TrackerRuntime>();
            scene.Add(go);
            return _current;
        }

        public void Track(GameObject instance, float seconds)
        {
            _instances.Add(instance);
            _deadlines.Add(Time.TimeSinceStartup + seconds);
        }

        public override void Update()
        {
            float now = Time.TimeSinceStartup;
            for (int i = _instances.Count - 1; i >= 0; i--)
            {
                if (now < _deadlines[i]) continue;
                var instance = _instances[i];
                _instances.RemoveAt(i);
                _deadlines.RemoveAt(i);
                if (instance is { IsDisposed: false })
                    instance.Dispose();
            }
        }

        public override void OnDisable()
        {
            _instances.Clear();
            _deadlines.Clear();
            if (ReferenceEquals(_current, this))
                _current = null;
            base.OnDisable();
        }
    }
}
