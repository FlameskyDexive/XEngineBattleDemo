// This file is part of the XEngine Game Engine
// Licensed under the MIT License. See the LICENSE file in the project root for details.

using System;

using XEngine.Runtime;
using XEngine.Runtime.ParticleSystem;
using XEngine.Runtime.ParticleSystem.Modules;
using XEngine.Runtime.Resources;
using XEngine.Vector;

namespace XEngine.Zonezero.Vfx;

/// <summary>
/// Battle VFX facade. Effects are backed by a scene-owned fixed pool so repeated combat does not
/// grow the hierarchy or recreate textures, materials, particle meshes, and managed buffers.
/// </summary>
public static class ZonezeroVfx
{
    private static readonly string[] s_weaponBoneNames =
    {
        "Anbi_Weapon_Bone04",
        "Anbi_Weapon_Bone07",
        "Weapon_saw",
        "Weapon_saw_b",
        "Weapon_08",
    };

    private static BattleVfxRuntime? s_runtime;
    private static int s_weaponTrailCount;

    /// <summary>Acceptance telemetry for soak tests.</summary>
    public static int PoolSlotCount => RuntimeOrNull()?.SlotCount ?? 0;
    public static int ActiveParticleCount => RuntimeOrNull()?.ActiveParticleCount ?? 0;
    public static int TriggerCount => RuntimeOrNull()?.TriggerCount ?? 0;
    public static int WeaponTrailCount => s_weaponTrailCount;

    /// <summary>Creates all battle VFX resources and fixed pool slots once for the active scene.</summary>
    public static void Warmup()
    {
        Scene? scene = Scene.Current;
        if (scene == null) return;

        BattleVfxRuntime? current = RuntimeOrNull();
        if (current != null && current.Scene == scene) return;

        var root = new GameObject("BattleVfxRuntime");
        var runtime = root.AddComponent<BattleVfxRuntime>();
        runtime.Initialize();
        s_runtime = runtime;
        s_weaponTrailCount = 0;
        scene.Add(root);
    }

    /// <summary>Attaches one or two cached trails to the animated weapon bones of an actor.</summary>
    public static WeaponTrailHandle? AttachWeaponTrail(GameObject actor)
    {
        BattleVfxRuntime? runtime = RequireRuntime();
        if (runtime == null) return null;

        GameObject? first = null;
        GameObject? second = null;
        for (int i = 0; i < s_weaponBoneNames.Length; i++)
        {
            GameObject? bone = FindDescendant(actor, s_weaponBoneNames[i]);
            if (bone == null) continue;
            if (first == null)
                first = bone;
            else if (bone != first)
            {
                second = bone;
                break;
            }
        }

        // Some imported characters do not expose their weapon hierarchy. The animated right hand
        // still gives a readable fallback, unlike attaching to the static skinned-mesh object.
        first ??= FindDescendant(actor, "Bip001 R Hand");
        if (first == null)
        {
            Debug.LogWarning($"[BattleVfx] No animated weapon bone found under '{actor.Name}'.");
            return null;
        }

        TrailRenderer primary = ConfigureTrail(first, runtime.TrailMaterial, 0.15f);
        TrailRenderer? secondary = second != null
            ? ConfigureTrail(second, runtime.TrailMaterial, 0.09f)
            : null;
        return new WeaponTrailHandle(primary, secondary);
    }

    /// <summary>Three normal-chain stages use increasingly broad, differently angled arcs.</summary>
    public static GameObject NormalSlash(Float3 position, Float3 forward, int stage)
    {
        BattleVfxRuntime? runtime = RequireRuntime();
        if (runtime == null) return new GameObject("UnavailableNormalSlash");
        int clampedStage = Math.Clamp(stage, 1, 3);
        float rotation = clampedStage switch { 1 => -0.42f, 2 => 0.20f, _ => 0.62f };
        float size = clampedStage switch { 1 => 1.35f, 2 => 1.62f, _ => 1.92f };
        Color color = clampedStage switch
        {
            1 => new Color(0.70f, 0.94f, 1.00f, 0.95f),
            2 => new Color(0.35f, 0.88f, 1.00f, 1.00f),
            _ => new Color(1.00f, 0.88f, 0.48f, 1.00f),
        };
        return runtime.Slash(position + SafeFlat(forward) * 0.72f, size, rotation, color);
    }

    /// <summary>K: crossing cyan-white energy cuts plus a compact contact ring.</summary>
    public static GameObject SkillK(Float3 position, Float3 forward)
    {
        BattleVfxRuntime? runtime = RequireRuntime();
        if (runtime == null) return new GameObject("UnavailableSkillK");
        Float3 direction = SafeFlat(forward);
        GameObject first = runtime.Slash(position + direction * 0.82f, 2.05f, -0.68f,
            new Color(0.28f, 0.92f, 1f, 1f));
        runtime.Slash(position + direction * 0.98f + new Float3(0f, 0.12f, 0f), 1.72f, 0.72f,
            new Color(0.86f, 0.98f, 1f, 0.92f));
        runtime.Ring(position + direction * 0.88f - new Float3(0f, 0.32f, 0f), 1.10f,
            new Color(0.24f, 0.82f, 1f, 0.82f));
        return first;
    }

    /// <summary>L: forward dash streaks followed by an oversized horizontal finishing cut.</summary>
    public static GameObject SkillL(Float3 position, Float3 forward)
    {
        BattleVfxRuntime? runtime = RequireRuntime();
        if (runtime == null) return new GameObject("UnavailableSkillL");
        Float3 direction = SafeFlat(forward);
        GameObject streaks = runtime.Dash(position + new Float3(0f, 0.75f, 0f), -direction);
        runtime.Slash(position + direction * 1.20f + new Float3(0f, 0.18f, 0f), 2.65f, 0.05f,
            new Color(0.50f, 0.96f, 1f, 1f));
        return streaks;
    }

    /// <summary>I intro: an anticipatory floor pulse before the eruption body.</summary>
    public static GameObject UltimateCharge(Float3 position)
    {
        BattleVfxRuntime? runtime = RequireRuntime();
        if (runtime == null) return new GameObject("UnavailableUltimateCharge");
        GameObject ring = runtime.Ring(position + new Float3(0f, 0.08f, 0f), 1.75f,
            new Color(1f, 0.54f, 0.18f, 0.85f));
        runtime.Flash(position + new Float3(0f, 1.0f, 0f), 0.85f,
            new Color(1f, 0.82f, 0.42f, 0.72f));
        return ring;
    }

    /// <summary>I body: expanding ground rings, an upward spark eruption, and a center flash.</summary>
    public static GameObject BigSkillBurst(Float3 position)
    {
        BattleVfxRuntime? runtime = RequireRuntime();
        if (runtime == null) return new GameObject("UnavailableBigSkillBurst");
        runtime.Ring(position - new Float3(0f, 0.82f, 0f), 3.65f,
            new Color(1f, 0.38f, 0.12f, 0.92f));
        runtime.Ring(position - new Float3(0f, 0.70f, 0f), 2.35f,
            new Color(1f, 0.90f, 0.42f, 0.78f));
        runtime.Flash(position + new Float3(0f, 0.32f, 0f), 2.25f,
            new Color(1f, 0.76f, 0.35f, 0.88f));
        return runtime.Eruption(position - new Float3(0f, 0.78f, 0f));
    }

    /// <summary>Directional hit sparks and a short central impact flash.</summary>
    public static GameObject HitSparks(Float3 position, Float3 direction)
    {
        BattleVfxRuntime? runtime = RequireRuntime();
        if (runtime == null) return new GameObject("UnavailableHitSparks");
        GameObject sparks = runtime.Hit(position, direction);
        runtime.Flash(position, 0.82f, new Color(1f, 0.94f, 0.72f, 0.90f));
        return sparks;
    }

    /// <summary>Compatibility overload for older VFX demo call sites.</summary>
    public static GameObject HitSparks(Float3 position) => HitSparks(position, Float3.UnitY);

    /// <summary>Compatibility builder now backed by the normal-slash pool.</summary>
    public static GameObject SlashArc(Float3 position, Float3 forward)
        => NormalSlash(position, forward, 1);

    /// <summary>Compatibility hit-flash hook; pooled at the target chest instead of parented.</summary>
    public static GameObject HitFlash(GameObject hurtTarget)
    {
        BattleVfxRuntime? runtime = RequireRuntime();
        return runtime?.Flash(hurtTarget.Transform.Position + new Float3(0f, 0.9f, 0f), 0.72f,
            new Color(1f, 0.90f, 0.68f, 0.82f)) ?? new GameObject("UnavailableHitFlash");
    }

    /// <summary>Quick circular switch-in accent.</summary>
    public static GameObject SwitchFlash(Float3 position)
    {
        BattleVfxRuntime? runtime = RequireRuntime();
        if (runtime == null) return new GameObject("UnavailableSwitchFlash");
        runtime.Flash(position + new Float3(0f, 0.9f, 0f), 1.25f,
            new Color(0.65f, 0.94f, 1f, 0.82f));
        return runtime.Ring(position + new Float3(0f, 0.08f, 0f), 1.45f,
            new Color(0.42f, 0.84f, 1f, 0.76f));
    }

    public static GameObject EvadeDust(Float3 position)
        => RequireRuntime()?.Dust(position, 16, 0.42f, 1.35f) ?? new GameObject("UnavailableEvadeDust");

    public static GameObject LandDust(Float3 position)
        => RequireRuntime()?.Dust(position, 24, 0.68f, 2.10f) ?? new GameObject("UnavailableLandDust");

    /// <summary>Legacy direct trail builder; pooled material ownership remains scene scoped.</summary>
    public static TrailRenderer WeaponTrail(GameObject weaponBone)
    {
        BattleVfxRuntime? runtime = RequireRuntime();
        if (runtime == null)
            return weaponBone.GetComponent<TrailRenderer>() ?? weaponBone.AddComponent<TrailRenderer>();
        return ConfigureTrail(weaponBone, runtime.TrailMaterial, 0.15f);
    }

    /// <summary>Radial spark gradient: bright core fading to transparent edges.</summary>
    public static Texture2D SparkTexture()
    {
        const int size = 64;
        var texture = NewTexture("VfxSpark", size);
        var pixels = new Color[size * size];
        for (int y = 0; y < size; y++)
        for (int x = 0; x < size; x++)
        {
            float dx = (x + 0.5f) / size - 0.5f;
            float dy = (y + 0.5f) / size - 0.5f;
            float d = MathF.Min(1f, MathF.Sqrt(dx * dx + dy * dy) * 2f);
            float alpha = MathF.Max(0f, 1f - d);
            pixels[y * size + x] = new Color(1f, 0.92f, 0.62f, alpha * alpha);
        }
        UploadPixels(texture, pixels);
        return texture;
    }

    /// <summary>Soft noisy dust blob.</summary>
    public static Texture2D DustTexture()
    {
        const int size = 64;
        var texture = NewTexture("VfxDust", size);
        var pixels = new Color[size * size];
        uint seed = 0x9e3779b9u;
        for (int y = 0; y < size; y++)
        for (int x = 0; x < size; x++)
        {
            float dx = (x + 0.5f) / size - 0.5f;
            float dy = (y + 0.5f) / size - 0.5f;
            float d = MathF.Min(1f, MathF.Sqrt(dx * dx + dy * dy) * 2f);
            float alpha = MathF.Max(0f, 1f - d);
            seed = Hash(seed);
            float noise = 0.75f + ((seed & 0xFF) / 255f) * 0.5f;
            pixels[y * size + x] = new Color(0.75f, 0.68f, 0.55f, alpha * alpha * noise);
        }
        UploadPixels(texture, pixels);
        return texture;
    }

    /// <summary>Crescent blade texture with a sharp core and soft outer edge.</summary>
    public static Texture2D SlashTexture()
    {
        const int size = 96;
        var texture = NewTexture("VfxSlash", size);
        var pixels = new Color[size * size];
        for (int y = 0; y < size; y++)
        for (int x = 0; x < size; x++)
        {
            float u = (x + 0.5f) / size - 0.5f;
            float v = (y + 0.5f) / size - 0.5f;
            float ellipse = MathF.Sqrt(u * u + v * v * 4.2f) * 2f;
            float shell = MathF.Max(0f, 1f - MathF.Abs(ellipse - 0.74f) * 5.4f);
            float taper = MathF.Max(0f, 1f - MathF.Abs(u) * 1.65f);
            float alpha = shell * taper * MathF.Max(0f, 1f - MathF.Abs(v) * 1.2f);
            pixels[y * size + x] = new Color(0.78f, 0.94f, 1f, alpha);
        }
        UploadPixels(texture, pixels);
        return texture;
    }

    /// <summary>Thin radial ring used by contact and ultimate floor pulses.</summary>
    public static Texture2D RingTexture()
    {
        const int size = 96;
        var texture = NewTexture("VfxRing", size);
        var pixels = new Color[size * size];
        for (int y = 0; y < size; y++)
        for (int x = 0; x < size; x++)
        {
            float dx = (x + 0.5f) / size - 0.5f;
            float dy = (y + 0.5f) / size - 0.5f;
            float radius = MathF.Sqrt(dx * dx + dy * dy) * 2f;
            float outer = MathF.Max(0f, 1f - MathF.Abs(radius - 0.76f) * 16f);
            float innerGlow = MathF.Max(0f, 1f - MathF.Abs(radius - 0.62f) * 8f) * 0.22f;
            pixels[y * size + x] = new Color(1f, 0.86f, 0.52f, MathF.Min(1f, outer + innerGlow));
        }
        UploadPixels(texture, pixels);
        return texture;
    }

    private static BattleVfxRuntime? RequireRuntime()
    {
        Warmup();
        return RuntimeOrNull();
    }

    private static BattleVfxRuntime? RuntimeOrNull()
    {
        if (s_runtime == null || s_runtime.IsDisposed || s_runtime.GameObject == null || s_runtime.GameObject.IsDisposed)
            s_runtime = null;
        return s_runtime;
    }

    private static void Release(BattleVfxRuntime runtime)
    {
        if (ReferenceEquals(s_runtime, runtime))
        {
            s_runtime = null;
            s_weaponTrailCount = 0;
        }
    }

    private static TrailRenderer ConfigureTrail(GameObject bone, Material material, float width)
    {
        TrailRenderer? trail = bone.GetComponent<TrailRenderer>();
        if (trail == null)
        {
            trail = bone.AddComponent<TrailRenderer>();
            s_weaponTrailCount++;
        }
        trail.Material = new AssetRef<Material>(material);
        trail.Time = 0.20f;
        trail.StartWidth = width;
        trail.EndWidth = 0.01f;
        trail.MinVertexDistance = 0.018f;
        trail.Enabled = false;
        return trail;
    }

    private static GameObject? FindDescendant(GameObject root, string name)
    {
        if (string.Equals(root.Name, name, StringComparison.OrdinalIgnoreCase)) return root;
        for (int i = 0; i < root.Children.Count; i++)
        {
            GameObject? found = FindDescendant(root.Children[i], name);
            if (found != null) return found;
        }
        return null;
    }

    private static Float3 SafeFlat(Float3 direction)
    {
        float sqr = direction.X * direction.X + direction.Z * direction.Z;
        if (sqr <= 1e-5f) return Float3.UnitZ;
        float inverse = 1f / MathF.Sqrt(sqr);
        return new Float3(direction.X * inverse, 0f, direction.Z * inverse);
    }

    private static Texture2D NewTexture(string name, int size)
        => new((uint)size, (uint)size, generateMipmaps: false, imageFormat: TextureImageFormat.Color4b) { Name = name };

    private static void UploadPixels(Texture2D texture, Color[] pixels)
    {
        var bytes = new byte[pixels.Length * 4];
        for (int i = 0; i < pixels.Length; i++)
        {
            bytes[i * 4] = (byte)(Math.Clamp(pixels[i].R, 0f, 1f) * 255f);
            bytes[i * 4 + 1] = (byte)(Math.Clamp(pixels[i].G, 0f, 1f) * 255f);
            bytes[i * 4 + 2] = (byte)(Math.Clamp(pixels[i].B, 0f, 1f) * 255f);
            bytes[i * 4 + 3] = (byte)(Math.Clamp(pixels[i].A, 0f, 1f) * 255f);
        }
        texture.SetData<byte>(bytes);
    }

    private static uint Hash(uint h)
    {
        h ^= 61u;
        h ^= h >> 16;
        h += h << 3;
        h ^= h >> 4;
        h *= 0x27d4eb2du;
        h ^= h >> 15;
        return h;
    }

    /// <summary>Scene-owned implementation and resource lifetime boundary.</summary>
    [AddComponentMenu("Zonezero/Battle VFX Runtime")]
    public sealed class BattleVfxRuntime : MonoBehaviour
    {
        private Texture2D? _sparkTexture;
        private Texture2D? _slashTexture;
        private Texture2D? _ringTexture;
        private Texture2D? _dustTexture;
        private Material? _hitMaterial;
        private Material? _slashMaterial;
        private Material? _ringMaterial;
        private Material? _dashMaterial;
        private Material? _eruptionMaterial;
        private Material? _flashMaterial;
        private Material? _dustMaterial;
        private Material? _trailMaterial;
        private VfxPool? _slashPool;
        private VfxPool? _hitPool;
        private VfxPool? _dashPool;
        private VfxPool? _eruptionPool;
        private VfxPool? _ringPool;
        private VfxPool? _flashPool;
        private VfxPool? _dustPool;
        private VfxPool[] _allPools = Array.Empty<VfxPool>();

        public int TriggerCount { get; private set; }
        public int SlotCount { get; private set; }
        public Material TrailMaterial => _trailMaterial!;

        public int ActiveParticleCount
        {
            get
            {
                int count = 0;
                for (int i = 0; i < _allPools.Length; i++)
                    count += _allPools[i].ActiveParticleCount;
                return count;
            }
        }

        internal void Initialize()
        {
            _sparkTexture = SparkTexture();
            _slashTexture = SlashTexture();
            _ringTexture = RingTexture();
            _dustTexture = DustTexture();

            _hitMaterial = ParticleMaterial("HitSparks", _sparkTexture, 0.09f, 1.6f);
            _slashMaterial = ParticleMaterial("SlashArc", _slashTexture, 1f, 1f);
            _ringMaterial = ParticleMaterial("ImpactRing", _ringTexture, 1f, 1f);
            _dashMaterial = ParticleMaterial("DashStreak", _sparkTexture, 0.12f, 2.2f);
            _eruptionMaterial = ParticleMaterial("UltimateEruption", _sparkTexture, 0.10f, 2f);
            _flashMaterial = ParticleMaterial("ImpactFlash", _sparkTexture, 1f, 1f);
            _dustMaterial = ParticleMaterial("BattleDust", _dustTexture, 1f, 1f);
            _trailMaterial = ParticleMaterial("WeaponTrail", _slashTexture, 1f, 1f);
            _trailMaterial.SetColor("_MainColor", new Color(0.48f, 0.92f, 1f, 0.88f));

            _slashPool = CreatePool("Slash", 4, _slashMaterial, stretched: false);
            _hitPool = CreatePool("Hit", 6, _hitMaterial, stretched: true);
            _dashPool = CreatePool("Dash", 3, _dashMaterial, stretched: true);
            _eruptionPool = CreatePool("Eruption", 2, _eruptionMaterial, stretched: true);
            _ringPool = CreatePool("Ring", 4, _ringMaterial, stretched: false);
            _flashPool = CreatePool("Flash", 4, _flashMaterial, stretched: false);
            _dustPool = CreatePool("Dust", 3, _dustMaterial, stretched: false);
            _allPools = new[] { _slashPool, _hitPool, _dashPool, _eruptionPool, _ringPool, _flashPool, _dustPool };
            for (int i = 0; i < _allPools.Length; i++)
                SlotCount += _allPools[i].Count;
        }

        internal GameObject Slash(Float3 position, float size, float rotation, Color color)
            => Fire(_slashPool!, position, Float3.UnitY, EmissionShape.Point, 1, 0.30f, 0f,
                size, rotation, color, 0f, 0f, 0f, false);

        internal GameObject Hit(Float3 position, Float3 direction)
            => Fire(_hitPool!, position, SafeDirection(direction + new Float3(0f, 0.18f, 0f)),
                EmissionShape.Cone, 24, 0.42f, 5.4f, 0.15f, 0f,
                new Color(1f, 0.78f, 0.28f, 1f), 0.34f, 28f, 0.35f, false);

        internal GameObject Dash(Float3 position, Float3 direction)
            => Fire(_dashPool!, position, SafeDirection(direction + new Float3(0f, 0.05f, 0f)),
                EmissionShape.Cone, 18, 0.46f, 5.6f, 0.12f, 0f,
                new Color(0.32f, 0.90f, 1f, 0.82f), 0.60f, 22f, -0.10f, false);

        internal GameObject Eruption(Float3 position)
            => Fire(_eruptionPool!, position, Float3.UnitY, EmissionShape.Cone, 36, 0.78f, 6.2f,
                0.11f, 0f, new Color(1f, 0.55f, 0.16f, 0.78f), 1.05f, 28f, -0.34f, false);

        internal GameObject Ring(Float3 position, float size, Color color)
            => Fire(_ringPool!, position, Float3.UnitY, EmissionShape.Point, 1, 0.46f, 0f,
                size, 0f, color, 0f, 0f, 0f, false);

        internal GameObject Flash(Float3 position, float size, Color color)
            => Fire(_flashPool!, position, Float3.UnitY, EmissionShape.Point, 1, 0.18f, 0f,
                size, 0f, color, 0f, 0f, 0f, false);

        internal GameObject Dust(Float3 position, int count, float size, float speed)
            => Fire(_dustPool!, position + new Float3(0f, 0.06f, 0f), Float3.UnitY,
                EmissionShape.Circle, count, 0.66f, speed, size, 0f,
                new Color(0.62f, 0.58f, 0.52f, 0.48f), 0.45f, 0f, 0.72f, false);

        private GameObject Fire(VfxPool pool, Float3 position, Float3 direction, EmissionShape shape,
            int count, float lifetime, float speed, float size, float rotation, Color color,
            float radius, float coneAngle, float gravity, bool emitFromShell)
        {
            VfxSlot slot = pool.Rent();
            ParticleSystemComponent system = slot.System;
            slot.Root.Transform.Position = position;
            slot.Root.Transform.Rotation = Quaternion.Identity;
            slot.Root.Transform.Up = SafeDirection(direction);

            system.Initial.StartLifetime.Mode = MinMaxCurveMode.Constant;
            system.Initial.StartLifetime.ConstantValue = lifetime;
            system.Initial.StartSpeed.Mode = MinMaxCurveMode.Constant;
            system.Initial.StartSpeed.ConstantValue = speed;
            system.Initial.StartSize.Mode = MinMaxCurveMode.Constant;
            system.Initial.StartSize.ConstantValue = size;
            system.Initial.StartRotation.Mode = MinMaxCurveMode.Constant;
            system.Initial.StartRotation.ConstantValue = rotation;
            system.Initial.StartColor.Mode = MinMaxGradientMode.Color;
            system.Initial.StartColor.ConstantColor = color;
            system.Initial.GravityModifier = gravity;
            system.Emission.Shape = shape;
            system.Emission.Radius = radius;
            system.Emission.ConeAngle = coneAngle;
            system.Emission.EmitFromShell = emitFromShell;
            system.Clear();
            system.Play();
            system.Emit(count);
            TriggerCount++;
            return slot.Root;
        }

        private VfxPool CreatePool(string name, int count, Material material, bool stretched)
        {
            var slots = new VfxSlot[count];
            for (int i = 0; i < count; i++)
            {
                var root = new GameObject($"{name}_{i + 1:00}");
                root.SetParent(GameObject, false);
                var system = root.AddComponent<ParticleSystemComponent>();
                system.Material = new AssetRef<Material>(material);
                system.MaxParticles = name == "Eruption" ? 96 : 64;
                system.Duration = 1.15f;
                system.Looping = false;
                system.PlayOnEnable = false;
                system.Prewarm = false;
                system.SimulationSpace = SimulationSpace.World;
                system.RenderMode = stretched ? ParticleRenderMode.StretchedBillboard : ParticleRenderMode.Billboard;
                system.SortingFudge = 0.08f;
                system.Emission.RateOverTime.Mode = MinMaxCurveMode.Constant;
                system.Emission.RateOverTime.ConstantValue = 0f;
                system.Emission.Bursts.Clear();
                ConfigureLifetimeFade(system);
                slots[i] = new VfxSlot(root, system);
            }
            return new VfxPool(slots);
        }

        private static void ConfigureLifetimeFade(ParticleSystemComponent system)
        {
            system.ColorOverLifetime.Enabled = true;
            XEngine.Runtime.Gradient gradient = system.ColorOverLifetime.ColorGradient;
            gradient.ColorKeys.Clear();
            gradient.ColorKeys.Add(new XEngine.Runtime.GradientColorKey(Color.White, 0f));
            gradient.ColorKeys.Add(new XEngine.Runtime.GradientColorKey(Color.White, 1f));
            gradient.AlphaKeys.Clear();
            gradient.AlphaKeys.Add(new XEngine.Runtime.GradientAlphaKey(1f, 0f));
            gradient.AlphaKeys.Add(new XEngine.Runtime.GradientAlphaKey(0.76f, 0.45f));
            gradient.AlphaKeys.Add(new XEngine.Runtime.GradientAlphaKey(0f, 1f));

            system.SizeOverLifetime.Enabled = true;
            system.SizeOverLifetime.SizeCurve = new XEngine.Runtime.AnimationCurve(new[]
            {
                new KeyFrame(0f, 0.82f),
                new KeyFrame(0.14f, 1f),
                new KeyFrame(1f, 0.34f),
            });
        }

        private static Material ParticleMaterial(string name, Texture2D texture,
            float velocityScale, float lengthScale)
        {
            Material material = Material.LoadDefault(DefaultMaterial.Particle);
            material.Name = $"BattleVfx_{name}";
            material.SetTexture("_MainTex", texture);
            material.SetColor("_MainColor", Color.White);
            // Keep demo effects readable on every backend. The current runtime soft-particle
            // depth fade rejects particles in front of opaque geometry, so enabling it makes
            // correctly placed ground rings and slashes disappear on Vulkan.
            material.SetFloat("_SoftParticlesFactor", 0f);
            material.SetFloat("_VelocityScale", velocityScale);
            material.SetFloat("_LengthScale", lengthScale);
            return material;
        }

        private static Float3 SafeDirection(Float3 direction)
        {
            float sqr = Float3.LengthSquared(direction);
            return sqr > 1e-5f ? direction / MathF.Sqrt(sqr) : Float3.UnitY;
        }

        public override void OnDispose()
        {
            Release(this);
            DisposeResource(_trailMaterial);
            DisposeResource(_dustMaterial);
            DisposeResource(_flashMaterial);
            DisposeResource(_eruptionMaterial);
            DisposeResource(_dashMaterial);
            DisposeResource(_ringMaterial);
            DisposeResource(_slashMaterial);
            DisposeResource(_hitMaterial);
            DisposeResource(_dustTexture);
            DisposeResource(_ringTexture);
            DisposeResource(_slashTexture);
            DisposeResource(_sparkTexture);
            base.OnDispose();
        }

        private static void DisposeResource(EngineObject? resource)
        {
            if (resource != null && !resource.IsDisposed)
                resource.Dispose();
        }
    }

    private sealed class VfxPool
    {
        private readonly VfxSlot[] _slots;
        private int _next;

        public int Count => _slots.Length;

        public int ActiveParticleCount
        {
            get
            {
                int count = 0;
                for (int i = 0; i < _slots.Length; i++)
                    count += _slots[i].System.ParticleCount;
                return count;
            }
        }

        public VfxPool(VfxSlot[] slots) => _slots = slots;

        public VfxSlot Rent()
        {
            VfxSlot slot = _slots[_next];
            _next = (_next + 1) % _slots.Length;
            return slot;
        }
    }

    private sealed class VfxSlot
    {
        public readonly GameObject Root;
        public readonly ParticleSystemComponent System;

        public VfxSlot(GameObject root, ParticleSystemComponent system)
        {
            Root = root;
            System = system;
        }
    }
}

/// <summary>Controller-owned handle that toggles already-created weapon trails without allocation.</summary>
public sealed class WeaponTrailHandle
{
    private readonly TrailRenderer _primary;
    private readonly TrailRenderer? _secondary;
    private bool _enabled;

    internal WeaponTrailHandle(TrailRenderer primary, TrailRenderer? secondary)
    {
        _primary = primary;
        _secondary = secondary;
    }

    public bool IsEnabled => _enabled;
    public int TrailCount => _secondary == null ? 1 : 2;

    public void SetEnabled(bool enabled)
    {
        if (_enabled == enabled) return;
        _enabled = enabled;
        if (!_primary.IsDisposed)
            _primary.Enabled = enabled;
        if (_secondary != null && !_secondary.IsDisposed)
            _secondary.Enabled = enabled;
    }
}
