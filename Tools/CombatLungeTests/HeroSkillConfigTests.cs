using XEngine.Runtime;
using XEngine.Zonezero.Config;
using XEngine.Zonezero.Vfx;
using XEngine.Vector;

using Xunit;

namespace XEngine.Zonezero.Runtime.Tests;

/// <summary>
/// Z1 — hero skill config data model + library lookup + rpgvfx spawner failure modes.
/// Runs without any editor/asset backend (the spawner must degrade to null, never throw).
/// </summary>
public sealed class HeroSkillConfigTests
{
    [Fact]
    public void Defaults_MatchLegacyHardcodedValues()
    {
        var config = ScriptableObject.CreateInstance<HeroSkillConfig>();

        Assert.Equal(4.6f, config.RunSpeed, 3);
        Assert.Equal(540f, config.TurnSpeedDeg, 3);
        Assert.Equal(2.0f, config.AttackRange, 3);
        Assert.Equal(65f, config.AttackHalfAngleDeg, 3);
        Assert.Equal(0.18f, config.NormalAttackCooldown, 3);
        Assert.Equal(1.25f, config.SkillKCooldown, 3);
        Assert.Equal(2.25f, config.SkillLCooldown, 3);
        Assert.Equal(7f, config.SkillICooldown, 3);
        Assert.Equal(0.32f, config.HitWindowStart, 3);
        Assert.Equal(0.72f, config.HitWindowEnd, 3);
        Assert.Equal(0.04f, config.SkillLHitWindowStart, 3);
        Assert.Equal(0.40f, config.SkillLHitWindowEnd, 3);
        Assert.Equal(1f, config.VfxScale, 3);
        Assert.Equal(4f, config.VfxLifetime, 3);
    }

    [Fact]
    public void NormalAttackVfxPath_ClampsToLastEntry()
    {
        var config = ScriptableObject.CreateInstance<HeroSkillConfig>();
        Assert.Equal("", config.NormalAttackVfxPath(0)); // unconfigured → empty (fallback)

        config.NormalAttackVfxPaths = new[] { "a.prefab", "b.prefab" };
        Assert.Equal("a.prefab", config.NormalAttackVfxPath(0));
        Assert.Equal("b.prefab", config.NormalAttackVfxPath(1));
        Assert.Equal("b.prefab", config.NormalAttackVfxPath(99)); // beyond array → last
        Assert.Equal("a.prefab", config.NormalAttackVfxPath(-3)); // before array → first
    }

    [Fact]
    public void Library_FindsByHeroId_CaseInsensitive()
    {
        var library = ScriptableObject.CreateInstance<HeroSkillLibrary>();
        var anbi = ScriptableObject.CreateInstance<HeroSkillConfig>();
        anbi.HeroId = "Anbi";
        var corin = ScriptableObject.CreateInstance<HeroSkillConfig>();
        corin.HeroId = "Corin";
        library.Heroes.Add(anbi);
        library.Heroes.Add(corin);

        Assert.Same(anbi, library.Find("anbi"));
        Assert.Same(corin, library.Find("CORIN"));
        Assert.Null(library.Find("Nostradamus"));
        Assert.Null(library.Find(""));
        Assert.Null(library.Find(null));
    }

    [Fact]
    public void Library_LoadDefault_WithoutResources_ReturnsNullQuietly()
    {
        HeroSkillLibrary.ResetCache();
        // No editor backend / Resources map in unit tests — must be null, not throw.
        Assert.Null(HeroSkillLibrary.LoadDefault());
        // Repeat call stays cached-null without re-lookup noise (still null).
        Assert.Null(HeroSkillLibrary.LoadDefault());
        HeroSkillLibrary.ResetCache();
    }

    [Fact]
    public void Spawner_EmptyOrUnknownPath_ReturnsNullWithoutThrowing()
    {
        RpgVfxSpawner.ResetCache();
        Assert.Null(RpgVfxSpawner.Spawn("", Float3.Zero, Float3.UnitZ));
        Assert.Null(RpgVfxSpawner.Spawn("Packages/does/not/exist.prefab", Float3.Zero, Float3.UnitZ));
        Assert.Equal(0, RpgVfxSpawner.SpawnCount);
        RpgVfxSpawner.ResetCache();
    }

    [Fact]
    public void Spawner_Warmup_UnknownPaths_CacheNegativeWithoutThrowing()
    {
        RpgVfxSpawner.ResetCache();
        RpgVfxSpawner.Warmup(new[] { "nope1.prefab", "nope2.prefab" });
        Assert.Null(RpgVfxSpawner.Spawn("nope1.prefab", Float3.Zero, Float3.UnitZ));
        RpgVfxSpawner.ResetCache();
    }
}
