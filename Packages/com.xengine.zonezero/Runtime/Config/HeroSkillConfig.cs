// This file is part of the XEngine Game Engine
// Licensed under the MIT License. See the LICENSE file in the project root for details.

using System.Collections.Generic;

using XEngine.Runtime;

namespace XEngine.Zonezero.Config;

/// <summary>
/// Per-hero combat numbers and effect assignments, authored as a data asset so designers can
/// tune them in the inspector without touching code. Field defaults equal the values that were
/// previously hardcoded on HeroCombatController/CombatMotor — a missing or partial config keeps
/// the demo behaving exactly like before. Effect paths address rpgvfx package prefabs
/// ("Packages/com.xengine.rpgvfx/Assets/Prefabs/&lt;name&gt;.prefab"); an empty path falls back
/// to the procedural ZonezeroVfx effect for that slot.
/// </summary>
[CreateAssetMenu("Zonezero/Hero Skill Config", Order = 10)]
public sealed class HeroSkillConfig : ScriptableObject
{
    public string HeroId = "";

    // ---- movement / combat numbers (defaults = pre-config hardcoded values) ----
    [Tooltip("Run speed in m/s.")]
    public float RunSpeed = 4.6f;
    [Tooltip("Turn speed in deg/s.")]
    public float TurnSpeedDeg = 540f;
    [Tooltip("Melee hit-test distance in m.")]
    public float AttackRange = 2.0f;
    [Tooltip("Melee hit-test half-angle in deg.")]
    public float AttackHalfAngleDeg = 65f;

    public float NormalAttackCooldown = 0.18f;
    public float SkillKCooldown = 1.25f;
    public float SkillLCooldown = 2.25f;
    public float SkillICooldown = 7f;

    [Tooltip("Normalized clip-time window where normal-attack swings can connect.")]
    public float HitWindowStart = 0.32f;
    public float HitWindowEnd = 0.72f;
    [Tooltip("Normalized clip-time window where the L-skill strike connects.")]
    public float SkillLHitWindowStart = 0.04f;
    public float SkillLHitWindowEnd = 0.40f;

    [Tooltip("Reserved for a future damage system (hits currently only count).")]
    public int Damage = 10;

    // ---- effect assignments (empty = procedural ZonezeroVfx fallback) ----
    [Tooltip("One path per normal-combo stage; stages beyond the array reuse the last entry.")]
    public string[] NormalAttackVfxPaths = System.Array.Empty<string>();
    public string SkillKVfxPath = "";
    public string SkillLVfxPath = "";
    [Tooltip("Ultimate charge loop (looping prefab — recycled after VfxLifetime).")]
    public string SkillIChargeVfxPath = "";
    public string SkillIBurstVfxPath = "";
    [Tooltip("Effect played on the victim when this hero lands a hit.")]
    public string HitVfxPath = "";

    [Tooltip("Uniform scale applied to every spawned effect for this hero.")]
    public float VfxScale = 1f;
    public float NormalVfxHeight = 1.05f;
    public float SkillKVfxHeight = 1.05f;
    public float SkillLVfxHeight = 0.18f;
    public float ChargeVfxHeight = 0f;
    public float BurstVfxHeight = 0.9f;
    public float BurstVfxForward = 0f;
    [Tooltip("Seconds before a spawned effect instance is recycled (looping prefabs need this).")]
    public float VfxLifetime = 4f;

    /// <summary>Path for a combo stage, clamped to the array (empty when unconfigured).</summary>
    public string NormalAttackVfxPath(int stage)
    {
        if (NormalAttackVfxPaths is not { Length: > 0 }) return "";
        int index = System.Math.Clamp(stage, 0, NormalAttackVfxPaths.Length - 1);
        return NormalAttackVfxPaths[index] ?? "";
    }

    /// <summary>Appends every non-empty effect path on this config to <paramref name="sink"/>.</summary>
    public void CollectVfxPaths(List<string> sink)
    {
        foreach (string path in NormalAttackVfxPaths)
            if (!string.IsNullOrEmpty(path)) sink.Add(path);
        AddIfSet(SkillKVfxPath);
        AddIfSet(SkillLVfxPath);
        AddIfSet(SkillIChargeVfxPath);
        AddIfSet(SkillIBurstVfxPath);
        AddIfSet(HitVfxPath);
        return;

        void AddIfSet(string path)
        {
            if (!string.IsNullOrEmpty(path)) sink.Add(path);
        }
    }
}

/// <summary>
/// Registry that maps hero ids to their <see cref="HeroSkillConfig"/>. One library asset lives
/// at Assets/Resources/Zonezero/HeroSkillLibrary.asset and is loaded at runtime through
/// <see cref="GameResources"/> (see <see cref="HeroSkillLibrary.LoadDefault"/>).
/// </summary>
[CreateAssetMenu("Zonezero/Hero Skill Library", Order = 11)]
public sealed class HeroSkillLibrary : ScriptableObject
{
    /// <summary>Per-hero configs, referenced by GUID so each stays an individually editable asset.</summary>
    public List<AssetRef<HeroSkillConfig>> Heroes = new();

    private static HeroSkillLibrary? _default;
    private static bool _defaultResolved;

    public HeroSkillConfig? Find(string heroId)
    {
        if (string.IsNullOrEmpty(heroId)) return null;
        for (int i = 0; i < Heroes.Count; i++)
        {
            // Res is non-blocking under async loading (null until streamed in); a lookup is a
            // one-shot query, so block — three small config assets resolve in microseconds.
            // EnsureLoaded populates the shared database cache; the local copy's Res then hits it.
            var reference = Heroes[i];
            reference.EnsureLoaded();
            var config = reference.Res;
            if (config is not null && string.Equals(config.HeroId, heroId, System.StringComparison.OrdinalIgnoreCase))
                return config;
        }
        return null;
    }

    /// <summary>
    /// Loads the collector-owned library from Bundles/Config/Heroes by its stable GUID. The result is
    /// cached; returns null (and keeps returning null only until a successful load) when the
    /// asset is absent so unconfigured projects pay one lookup and keep the procedural fallback.
    /// </summary>
    public static HeroSkillLibrary? LoadDefault()
    {
        if (_defaultResolved && _default is { IsDisposed: false }) return _default;
        _default = AssetDatabase.Get(new System.Guid("5e52ed1a-842d-5a36-9250-6d5213860b24")) as HeroSkillLibrary;
        _defaultResolved = _default is not null;
        return _default;
    }

    /// <summary>
    /// Every effect path configured across all heroes (duplicates removed) — the warm set for
    /// <c>RpgVfxSpawner.Warmup</c>. Configs are small; a one-shot blocking resolve is fine here.
    /// </summary>
    public List<string> AllVfxPaths()
    {
        var paths = new List<string>();
        var seen = new HashSet<string>(System.StringComparer.OrdinalIgnoreCase);
        for (int i = 0; i < Heroes.Count; i++)
        {
            var reference = Heroes[i];
            reference.EnsureLoaded();
            var config = reference.Res;
            if (config is null) continue;
            config.CollectVfxPaths(paths);
        }
        var unique = new List<string>(paths.Count);
        foreach (string path in paths)
            if (seen.Add(path)) unique.Add(path);
        return unique;
    }

    /// <summary>Test/reset hook — clears the cached default library.</summary>
    public static void ResetCache()
    {
        _default = null;
        _defaultResolved = false;
    }
}
