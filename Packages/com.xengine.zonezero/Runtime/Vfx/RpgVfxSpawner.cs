// This file is part of the XEngine Game Engine
// Licensed under the MIT License. See the LICENSE file in the project root for details.

using System;
using System.Collections.Generic;

using XEngine.Runtime;
using XEngine.Runtime.ParticleSystem;
using XEngine.Runtime.Resources;
using XEngine.Vector;

namespace XEngine.Zonezero.Vfx;

/// <summary>
/// Spawns rpgvfx package effect prefabs at runtime by asset path (e.g.
/// "Packages/com.xengine.rpgvfx/Assets/Prefabs/Magic_circles_Prefabs_Magic_circle_1.prefab").
///
/// Loading goes through the engine's runtime asset path (<see cref="AssetLoader"/> +
/// <see cref="AssetDatabase"/>): in the editor this is the AssetBundle-simulated resolution
/// (the asset database serves the GUID directly, no bundles built); in an AssetBundle-packaged
/// player the same GUID resolves through the collector-built manifests — which is why the
/// rpgvfx prefabs folder is covered by the project's AssetBundle collector setting.
///
/// Frame-smoothing comes from two layers: <see cref="Warmup"/> requests every configured prefab
/// on the background loader thread and pre-instantiates pool instances staggered across frames,
/// and spawned instances are recycled into a per-prefab <see cref="GameObject"/> pool instead of
/// being destroyed, so combat-time spawns are a pool pop + transform reset + particle restart.
/// Path→GUID resolution follows the BattleHUD recipe (reflection against the current asset
/// backend, because <c>GetEntry(string)</c> exists only on the editor backend type and GUIDs
/// are machine-local). Every failure path returns null so callers can fall back to the
/// procedural <see cref="ZonezeroVfx"/> effects — including in unit tests, which run without
/// any backend at all.
/// </summary>
public static class RpgVfxSpawner
{
    private const int PrewarmInstancesPerPath = 2;
    private const int MaxIdlePerPrefab = 8;
    private const int PrewarmInstantiationsPerFrame = 2;

    private static readonly Dictionary<string, Guid> PathGuidCache = new(StringComparer.OrdinalIgnoreCase);

    /// <summary>Total instances spawned since domain start (telemetry for tests/acceptance).</summary>
    public static int SpawnCount { get; private set; }
    /// <summary>Spawns served from the pool (no instantiation on the frame).</summary>
    public static int PoolHits { get; private set; }
    /// <summary>Spawns that had to instantiate (pool empty — cold path).</summary>
    public static int PoolMisses { get; private set; }
    /// <summary>Idle instances currently pooled and reusable.</summary>
    public static int IdlePooled => RuntimeOrNull()?.IdleCount ?? 0;
    /// <summary>Instances currently playing (spawned, not yet recycled).</summary>
    public static int LiveActive => RuntimeOrNull()?.ActiveCount ?? 0;

    /// <summary>
    /// Instantiates (or reuses a pooled instance of) the prefab at <paramref name="path"/>, places
    /// it at <paramref name="position"/> facing <paramref name="forward"/>, applies
    /// <paramref name="scale"/>, and schedules recycling into the pool after
    /// <paramref name="lifetimeSeconds"/>. Returns null on any resolution failure.
    /// </summary>
    public static GameObject? Spawn(string path, Float3 position, Float3 forward, float scale = 1f,
        float lifetimeSeconds = 4f)
    {
        if (string.IsNullOrEmpty(path)) return null;
        var runtime = VfxPoolRuntime.Ensure();
        if (runtime == null) return null;

        Guid guid = ResolvePathGuidCached(path);
        if (guid == Guid.Empty) return null;

        GameObject? instance = runtime.Take(guid);
        if (instance != null)
        {
            PoolHits++;
        }
        else
        {
            PrefabAsset? prefab = XEngine.Zonezero.Config.BattleAssetCatalog.Load()?.LoadEffect(path);
            if (prefab is null) return null;
            try { instance = prefab.Instantiate(); }
            catch { return null; } // corrupt prefab data → procedural fallback
            if (instance is null) return null;
            runtime.RememberAuthoredTransform(guid, instance);
            Scene.Current?.Add(instance);
            PoolMisses++;
        }

        // Reset BEFORE enabling: particle Play() snapshots the emitter transform on restart.
        Quaternion heading = Quaternion.Identity;
        if (Float3.LengthSquared(forward) > 1e-6f)
        {
            Float3 dir = Float3.Normalize(forward);
            // Hovl effects face +Z; yaw the instance so +Z aligns with the requested forward.
            float yaw = MathF.Atan2(dir.X, dir.Z);
            heading = Quaternion.AxisAngle(Float3.UnitY, yaw);
        }
        runtime.Place(instance, guid, position, heading, scale);

        instance.Enabled = true;
        RestartParticles(instance);

        runtime.TrackActive(instance, guid, MathF.Max(0.1f, lifetimeSeconds));
        SpawnCount++;
        if (SpawnCount % 10 == 0)
            LogPoolStats();
        return instance;
    }

    /// <summary>Pool heartbeat (every 10th spawn + after seeding) — soak/acceptance telemetry.</summary>
    public static void LogPoolStats()
    {
        Debug.Log($"[Zonezero] VfxSpawner pool: spawns={SpawnCount} hits={PoolHits} misses={PoolMisses} idle={IdlePooled} active={LiveActive}");
    }

    /// <summary>
    /// Warm path→prefab resolution and pool seeding (call during battle setup). Prefab assets are
    /// requested on the background loader thread; pool instances are pre-instantiated staggered
    /// over the following frames so neither the request nor the instantiation lands on one frame.
    /// </summary>
    public static void Warmup(IEnumerable<string> paths)
    {
        var runtime = VfxPoolRuntime.Ensure();
        int resolved = 0;
        foreach (string path in paths)
        {
            if (string.IsNullOrEmpty(path)) continue;
            Guid guid = ResolvePathGuidCached(path);
            if (guid == Guid.Empty) continue;

            resolved++;
            AssetLoader.Request(guid); // background load; in the editor this is the AB-simulated path
            runtime?.EnqueuePrewarm(guid, PrewarmInstancesPerPath);
        }
        if (resolved > 0)
            Debug.Log($"[Zonezero] VfxSpawner warmup: {resolved} prefab path(s) requested, pool seeding {resolved * PrewarmInstancesPerPath} instance(s).");
    }

    /// <summary>Test hook — clears caches and counters.</summary>
    public static void ResetCache()
    {
        PathGuidCache.Clear();
        SpawnCount = 0;
        PoolHits = 0;
        PoolMisses = 0;
    }

    /// <summary>Deterministically restart every particle system on a (re)activated instance.</summary>
    private static void RestartParticles(GameObject instance)
    {
        foreach (ParticleSystemComponent system in instance.GetComponentsInChildren<ParticleSystemComponent>(true, true))
            system.Play();
    }

    private static VfxPoolRuntime? RuntimeOrNull()
        => VfxPoolRuntime.Current is { IsDisposed: false } runtime ? runtime : null;

    private static Guid ResolvePathGuidCached(string path)
    {
        if (PathGuidCache.TryGetValue(path, out Guid cached)) return cached;
        Guid guid = ResolvePathGuid(path);
        PathGuidCache[path] = guid;
        return guid;
    }

    /// <summary>Path → asset GUID through the current backend (reflection: editor-only API).</summary>
    private static Guid ResolvePathGuid(string path)
    {
        return XEngine.Zonezero.Config.BattleAssetCatalog.Load()?.FindEffect(path) ?? Guid.Empty;
    }

    /// <summary>
    /// Scene-owned VFX pool: per-prefab idle stacks, the active-instance recycle list, and the
    /// staggered prewarm queue. One per scene; dies with it (pooled instances are scene members,
    /// so scene teardown disposes them and the pool goes with the runtime).
    /// </summary>
    private sealed class VfxPoolRuntime : MonoBehaviour
    {
        private readonly Dictionary<Guid, Stack<GameObject>> _pools = new();
        private readonly Dictionary<Guid, (Quaternion Rotation, Float3 Scale)> _authoredTransforms = new();

        internal void RememberAuthoredTransform(Guid guid, GameObject instance)
        {
            if (!_authoredTransforms.ContainsKey(guid))
                _authoredTransforms.Add(guid, (instance.Transform.LocalRotation, instance.Transform.LocalScale));
        }

        internal void Place(GameObject instance, Guid guid, Float3 position, Quaternion heading, float scale)
        {
            var authored = _authoredTransforms[guid];
            instance.Transform.Position = position;
            instance.Transform.LocalRotation = heading * authored.Rotation;
            instance.Transform.LocalScale = authored.Scale * scale;
        }
        private readonly List<GameObject> _active = new();
        private readonly List<Guid> _activeGuids = new();
        private readonly List<float> _deadlines = new();
        private readonly Queue<Guid> _prewarm = new();
        private readonly List<ParticleSystemComponent> _pendingRenderAssets = new();
        private int _prewarmedPaths;
        private bool _prewarmLogged;

        internal static VfxPoolRuntime? Current { get; private set; }

        internal static VfxPoolRuntime? Ensure()
        {
            var scene = Scene.Current;
            if (scene == null) return null;
            if (Current is { IsDisposed: false } current && current.Scene == scene) return current;

            // Fresh scene (or the old runtime died with its scene): a new pool bound to THIS scene.
            // An old scene's runtime is left alone — it dies with its own scene.
            var go = new GameObject("RpgVfxPool");
            Current = go.AddComponent<VfxPoolRuntime>();
            scene.Add(go);
            return Current;
        }

        internal int IdleCount
        {
            get
            {
                int count = 0;
                foreach (var kv in _pools) count += kv.Value.Count;
                return count;
            }
        }

        internal int ActiveCount => _active.Count;

        internal void EnqueuePrewarm(Guid guid, int instances)
        {
            for (int i = 0; i < instances; i++)
                _prewarm.Enqueue(guid);
        }

        /// <summary>Pop a pooled instance for <paramref name="guid"/>, skipping disposed entries.</summary>
        internal GameObject? Take(Guid guid)
        {
            if (!_pools.TryGetValue(guid, out var pool)) return null;
            while (pool.Count > 0)
            {
                var instance = pool.Pop();
                if (instance is { IsDisposed: false })
                    return instance;
            }
            return null;
        }

        internal void TrackActive(GameObject instance, Guid guid, float seconds)
        {
            _active.Add(instance);
            _activeGuids.Add(guid);
            _deadlines.Add(Time.TimeSinceStartup + seconds);
        }

        public override void Update()
        {
            RecycleExpired();
            ProcessPrewarm();
            for (int i = _pendingRenderAssets.Count - 1; i >= 0; i--)
            {
                var system = _pendingRenderAssets[i];
                if (system.IsDisposed || PrepareParticleAssets(system))
                    _pendingRenderAssets.RemoveAt(i);
            }
        }

        private bool PrepareParticleAssets(ParticleSystemComponent system)
        {
            var scene = GameObject.Scene;
            system.Material.LockToScene(scene);
            var material = system.Material.Res;
            bool ready = material != null && material.PrepareRenderAssets(scene);
            if (system.RenderMode == ParticleRenderMode.Mesh)
            {
                system.RenderMesh.LockToScene(scene);
                ready &= system.RenderMesh.Res != null;
            }
            if (system.Trails.Enabled && system.Trails.TrailMaterial.HasValue)
            {
                var trailRef = system.Trails.TrailMaterial.Value;
                trailRef.LockToScene(scene);
                var trail = trailRef.Res;
                ready &= trail != null && trail.PrepareRenderAssets(scene);
            }
            return ready;
        }

        private void RecycleExpired()
        {
            float now = Time.TimeSinceStartup;
            for (int i = _active.Count - 1; i >= 0; i--)
            {
                if (now < _deadlines[i]) continue;
                GameObject instance = _active[i];
                Guid guid = _activeGuids[i];
                _active.RemoveAt(i);
                _activeGuids.RemoveAt(i);
                _deadlines.RemoveAt(i);

                if (instance is not { IsDisposed: false }) continue;

                instance.Enabled = false; // OnDisable → particle Stop + Clear + resource release
                if (!_pools.TryGetValue(guid, out var pool))
                {
                    pool = new Stack<GameObject>();
                    _pools[guid] = pool;
                }
                if (pool.Count < MaxIdlePerPrefab)
                    pool.Push(instance);
                else
                    instance.Dispose();
            }
        }

        private void ProcessPrewarm()
        {
            if (_prewarm.Count == 0)
            {
                if (!_prewarmLogged && _prewarmedPaths > 0)
                {
                    _prewarmLogged = true;
                    Debug.Log($"[Zonezero] VfxSpawner pool seeded: {_prewarmedPaths} instance(s) idle across prefabs.");
                    RpgVfxSpawner.LogPoolStats();
                }
                return;
            }

            // Instantiate a few per frame; a guid whose prefab is still streaming goes to the back.
            int created = 0;
            int attempts = Math.Min(_prewarm.Count, PrewarmInstantiationsPerFrame * 4);
            while (attempts-- > 0 && created < PrewarmInstantiationsPerFrame && _prewarm.Count > 0)
            {
                Guid guid = _prewarm.Dequeue();
                if (AssetDatabase.GetCached(guid) is not PrefabAsset prefab)
                {
                    _prewarm.Enqueue(guid); // not streamed in yet; retry later frames
                    continue;
                }

                GameObject? instance;
                try { instance = prefab.Instantiate(); }
                catch { continue; } // corrupt prefab data; skip its prewarm entries
                if (instance is null) continue;
                RememberAuthoredTransform(guid, instance);

                instance.Enabled = false; // pooled idle until first Take
                GameObject?.Scene?.Add(instance);
                foreach (var system in instance.GetComponentsInChildren<ParticleSystemComponent>(true, true))
                    if (!PrepareParticleAssets(system)) _pendingRenderAssets.Add(system);
                if (!_pools.TryGetValue(guid, out var pool))
                {
                    pool = new Stack<GameObject>();
                    _pools[guid] = pool;
                }
                pool.Push(instance);
                _prewarmedPaths++;
                created++;
            }
        }

        public override void OnDisable()
        {
            _pools.Clear();
            _authoredTransforms.Clear();
            _active.Clear();
            _activeGuids.Clear();
            _deadlines.Clear();
            _prewarm.Clear();
            _pendingRenderAssets.Clear();
            _prewarmedPaths = 0;
            _prewarmLogged = false;
            if (ReferenceEquals(Current, this))
                Current = null;
            base.OnDisable();
        }
    }
}
