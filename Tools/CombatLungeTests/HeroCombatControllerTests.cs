using System.Reflection;

using XEngine.Zonezero.Combat;

using Xunit;

namespace XEngine.Zonezero.Runtime.Tests;

public sealed class HeroCombatControllerTests
{
    [Theory]
    [InlineData((int)CombatAction.SkillL)]
    [InlineData((int)CombatAction.Normal)]
    public void DisablingHeroClearsInterruptedMovementAndCombo(int actionValue)
    {
        var hero = new HeroCombatController();
        SetField(hero, "_action", (CombatAction)actionValue);
        SetField(hero, "_lungeRemaining", 0.2f);
        SetField(hero, "_normalStage", 1);
        SetField(hero, "_queuedNormalStage", 2);

        hero.OnDisable();

        Assert.False(hero.IsBusy);
        Assert.Equal(0, hero.NormalStage);
        Assert.Equal(0f, GetField<float>(hero, "_lungeRemaining"));
        Assert.Equal(0, GetField<int>(hero, "_queuedNormalStage"));
        // Enabling does not revive the action or its remaining displacement.
        hero.OnEnable();
        Assert.False(hero.IsBusy);
        Assert.Equal(0f, GetField<float>(hero, "_lungeRemaining"));
    }

    private static void SetField<T>(HeroCombatController hero, string name, T value)
        => typeof(HeroCombatController).GetField(name, BindingFlags.Instance | BindingFlags.NonPublic)!
            .SetValue(hero, value);

    private static T GetField<T>(HeroCombatController hero, string name)
        => (T)typeof(HeroCombatController).GetField(name, BindingFlags.Instance | BindingFlags.NonPublic)!
            .GetValue(hero)!;
}
