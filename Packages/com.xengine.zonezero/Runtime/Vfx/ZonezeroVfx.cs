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
/// zonezero M10 — the §4.2 skill-effect set as authored prefab-style builders: every effect
/// composes the M9 particle feature set (bursts, stretched billboards, additive/soft
/// materials) plus procedurally generated textures (radial sparks, rings, dust puffs) — no
/// external asset dependencies. Each builder returns the effect root for trigger-point wiring.
/// </summary>
public static class ZonezeroVfx
{
    // ------------------------------------------------------------------
    //  Procedural effect textures
    // ------------------------------------------------------------------

    /// <summary>Radial spark gradient: bright core fading to transparent edges (64×64).</summary>
    public static Texture2D SparkTexture()
    {
        const int size = 64;
        var texture = new Texture2D(size, size, generateMipmaps: false, imageFormat: TextureImageFormat.Color4b)
        {
            Name = "VfxSpark",
        };
        var pixels = new Color[size * size];
        for (int y = 0; y < size; y++)
        for (int x = 0; x < size; x++)
        {
            float dx = (x + 0.5f) / size - 0.5f;
            float dy = (y + 0.5f) / size - 0.5f;
            float d = MathF.Min(1f, MathF.Sqrt(dx * dx + dy * dy) * 2f);
            float a = MathF.Max(0f, 1f - d);
            a = a * a;
            pixels[y * size + x] = new Color(1f, 0.85f, 0.45f, a);
        }
        UploadPixels(texture, pixels);
        return texture;
    }

    /// <summary>Dust puff: soft noisy blob, low alpha (64×64).</summary>
    public static Texture2D DustTexture()
    {
        const int size = 64;
        var texture = new Texture2D(size, size, generateMipmaps: false, imageFormat: TextureImageFormat.Color4b)
        {
            Name = "VfxDust",
        };
        var pixels = new Color[size * size];
        uint seed = 0x9e3779b9u;
        for (int y = 0; y < size; y++)
        for (int x = 0; x < size; x++)
        {
            float dx = (x + 0.5f) / size - 0.5f;
            float dy = (y + 0.5f) / size - 0.5f;
            float d = MathF.Min(1f, MathF.Sqrt(dx * dx + dy * dy) * 2f);
            float a = MathF.Max(0f, 1f - d);
            seed = Hash(seed);
            float noise = 0.75f + ((seed & 0xFF) / 255f) * 0.5f;
            a = a * a * noise;
            pixels[y * size + x] = new Color(0.75f, 0.68f, 0.55f, a);
        }
        UploadPixels(texture, pixels);
        return texture;
    }

    /// <summary>Slash arc: crescent gradient brighter along the horizontal mid band (64×64).</summary>
    public static Texture2D SlashTexture()
    {
        const int size = 64;
        var texture = new Texture2D(size, size, generateMipmaps: false, imageFormat: TextureImageFormat.Color4b)
        {
            Name = "VfxSlash",
        };
        var pixels = new Color[size * size];
        for (int y = 0; y < size; y++)
        for (int x = 0; x < size; x++)
        {
            float u = (x + 0.5f) / size - 0.5f;
            float v = (y + 0.5f) / size - 0.5f;
            float radius = MathF.Sqrt(u * u + v * v) * 2f;
            float band = MathF.Exp(-v * v * 48f);
            float a = MathF.Max(0f, 1f - MathF.Abs(radius - 0.8f) * 3f) * band;
            pixels[y * size + x] = new Color(0.8f, 0.9f, 1f, a);
        }
        UploadPixels(texture, pixels);
        return texture;
    }

    private static void UploadPixels(Texture2D texture, Color[] pixels)
    {
        var bytes = new byte[pixels.Length * 4];
        for (int i = 0; i < pixels.Length; i++)
        {
            bytes[i * 4] = (byte)(System.Math.Clamp(pixels[i].R, 0f, 1f) * 255f);
            bytes[i * 4 + 1] = (byte)(System.Math.Clamp(pixels[i].G, 0f, 1f) * 255f);
            bytes[i * 4 + 2] = (byte)(System.Math.Clamp(pixels[i].B, 0f, 1f) * 255f);
            bytes[i * 4 + 3] = (byte)(System.Math.Clamp(pixels[i].A, 0f, 1f) * 255f);
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

    // ------------------------------------------------------------------
    //  Material helpers
    // ------------------------------------------------------------------

    private static Material ParticleMaterial(Texture2D texture, Color tint, bool soft)
    {
        XEngine.Runtime.Resources.Shader shader = XEngine.Runtime.Resources.Shader.LoadDefault(DefaultShader.Particle);
        var material = new Material(shader) { Name = "VfxParticle" };
        material.SetTexture("_MainTex", texture);
        material.SetColor("_MainColor", tint);
        if (soft)
            material.SetFloat("_SoftParticlesFactor", 0.4f);
        return material;
    }

    private static ParticleSystemComponent SpawnSystem(GameObject root, Material material,
        float lifetime, float speed, float size, int rate, bool stretched, float velocityScale)
    {
        var system = root.AddComponent<ParticleSystemComponent>();
        system.Material = new AssetRef<Material>(material);
        system.MaxParticles = 300;
        system.Duration = 3f;
        system.Looping = false;
        system.PlayOnEnable = false;
        system.Initial.StartLifetime.ConstantValue = lifetime;
        system.Initial.StartSpeed.ConstantValue = speed;
        system.Initial.StartSize.ConstantValue = size;
        system.Emission.RateOverTime.ConstantValue = 0f;
        system.Emission.Bursts.Add(new ParticleBurst { Time = 0f, MinCount = rate, MaxCount = rate });
        if (stretched)
        {
            system.RenderMode = ParticleRenderMode.StretchedBillboard;
            material.SetFloat("_VelocityScale", velocityScale);
            material.SetFloat("_LengthScale", 1.5f);
        }
        return system;
    }

    // ------------------------------------------------------------------
    //  §4.2 effect builders
    // ------------------------------------------------------------------

    /// <summary>打击火花 — burst of stretched additive sparks + soft fade (hit callback).</summary>
    public static GameObject HitSparks(Float3 position)
    {
        var root = new GameObject("HitSparks");
        root.Transform.Position = position;
        using Texture2D spark = SparkTexture();
        var material = ParticleMaterial(spark, new Color(1f, 1f, 1f, 1f), soft: true);
        SpawnSystem(root, material, lifetime: 0.45f, speed: 4.5f, size: 0.16f, rate: 26,
            stretched: true, velocityScale: 0.09f);
        return root;
    }

    /// <summary>武器拖尾 — a TrailRenderer child with additive white material (attach to a
    /// weapon bone; the sweep supplies the motion).</summary>
    public static TrailRenderer WeaponTrail(GameObject weaponBone)
    {
        var trail = weaponBone.AddComponent<TrailRenderer>();
        trail.Time = 0.22f;
        trail.StartWidth = 0.07f;
        trail.EndWidth = 0f;
        trail.MinVertexDistance = 0.02f;
        return trail;
    }

    /// <summary>斩击弧光 — one big billboard slash plane fired as a single-particle burst.</summary>
    public static GameObject SlashArc(Float3 position, Float3 forward)
    {
        var root = new GameObject("SlashArc");
        root.Transform.Position = position + forward * 0.8f;
        using Texture2D slash = SlashTexture();
        var material = ParticleMaterial(slash, new Color(1f, 1f, 1f, 1f), soft: false);
        SpawnSystem(root, material, lifetime: 0.28f, speed: 0f, size: 1.5f, rate: 1,
            stretched: false, velocityScale: 1f);
        return root;
    }

    /// <summary>大招爆发 — ground ring + upward eruption + big flash (BigSkill entry).</summary>
    public static GameObject BigSkillBurst(Float3 position)
    {
        var root = new GameObject("BigSkillBurst");
        root.Transform.Position = position;
        using Texture2D spark = SparkTexture();
        using Texture2D dust = DustTexture();

        // Eruption: stretched sparks fountaining up.
        var eruption = ParticleMaterial(spark, new Color(1f, 0.95f, 0.7f, 1f), soft: true);
        var eruptionSystem = SpawnSystem(root, eruption, lifetime: 0.9f, speed: 7f, size: 0.22f,
            rate: 90, stretched: true, velocityScale: 0.11f);
        eruptionSystem.Initial.GravityModifier = -0.6f;

        // Ground ring: dust billboards bursting outward at floor level.
        var ringRoot = new GameObject("Ring");
        ringRoot.Transform.Position = position + new Float3(0f, 0.05f, 0f);
        ringRoot.SetParent(root, false);
        var ring = ParticleMaterial(dust, new Color(1f, 0.9f, 0.7f, 1f), soft: true);
        SpawnSystem(ringRoot, ring, lifetime: 0.7f, speed: 6f, size: 0.5f, rate: 36,
            stretched: true, velocityScale: 0.05f);
        return root;
    }

    /// <summary>换人闪光 — quick ring of sparks around the spawn point (SwitchInNormal).</summary>
    public static GameObject SwitchFlash(Float3 position)
    {
        var root = new GameObject("SwitchFlash");
        root.Transform.Position = position;
        using Texture2D spark = SparkTexture();
        var material = ParticleMaterial(spark, new Color(0.85f, 0.95f, 1f, 1f), soft: false);
        var system = SpawnSystem(root, material, lifetime: 0.5f, speed: 3.5f, size: 0.2f,
            rate: 40, stretched: true, velocityScale: 0.06f);
        system.Emission.Shape = EmissionShape.Circle;
        system.Emission.Radius = 0.6f;
        return root;
    }

    /// <summary>闪避尘土 — low dust billboards puffing behind the dodge start.</summary>
    public static GameObject EvadeDust(Float3 position)
    {
        var root = new GameObject("EvadeDust");
        root.Transform.Position = position;
        using Texture2D dust = DustTexture();
        var material = ParticleMaterial(dust, new Color(1f, 1f, 1f, 1f), soft: true);
        SpawnSystem(root, material, lifetime: 0.6f, speed: 1.2f, size: 0.45f, rate: 18,
            stretched: false, velocityScale: 1f);
        return root;
    }

    /// <summary>落地扬尘 — same dust shape, bigger and slower (landing detection).</summary>
    public static GameObject LandDust(Float3 position)
    {
        var root = new GameObject("LandDust");
        root.Transform.Position = position;
        using Texture2D dust = DustTexture();
        var material = ParticleMaterial(dust, new Color(1f, 1f, 1f, 1f), soft: true);
        var system = SpawnSystem(root, material, lifetime: 0.8f, speed: 2.2f, size: 0.7f,
            rate: 26, stretched: false, velocityScale: 1f);
        system.Initial.StartSpeed.ConstantValue = 2.2f;
        system.Initial.GravityModifier = -0.2f;
        return root;
    }

    /// <summary>敌人受击闪白 — Emission-pulse material hook for IHurt targets: attaches the
    /// flash driver to the hurt object (paired with EnemyController's tint pulse).</summary>
    public static GameObject HitFlash(GameObject hurtTarget)
    {
        var flash = new GameObject("HitFlash");
        flash.SetParent(hurtTarget, false);
        flash.Transform.LocalPosition = Float3.Zero;
        using Texture2D spark = SparkTexture();
        var material = ParticleMaterial(spark, new Color(1f, 1f, 1f, 0.9f), soft: false);
        SpawnSystem(flash, material, lifetime: 0.2f, speed: 1f, size: 0.5f, rate: 6,
            stretched: false, velocityScale: 1f);
        return flash;
    }
}
