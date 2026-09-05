using XEngine.Zonezero.Combat;

using Xunit;

namespace XEngine.Zonezero.Runtime.Tests;

public sealed class CombatLungeMotionTests
{
    private const float SkillLungeSpeed = 7.5f;
    private const float SkillLungeDuration = 0.38f;

    [Theory]
    [InlineData(30)]
    [InlineData(60)]
    [InlineData(120)]
    [InlineData(144)]
    public void SkillLConsumesExactlyConfiguredDistanceIncludingTailFrame(int framesPerSecond)
    {
        float remaining = SkillLungeDuration;
        float distance = 0f;

        while (remaining > 0f)
        {
            float step = CombatLungeMotion.ConsumeStepSeconds(
                CombatAction.SkillL, 1f / framesPerSecond, ref remaining);
            distance += SkillLungeSpeed * step;
        }

        Assert.Equal(0f, remaining);
        Assert.InRange(MathF.Abs(distance - 2.85f), 0f, 1e-5f);
    }

    [Fact]
    public void SkillLStartsMovingOnTheCastFrame()
    {
        float remaining = SkillLungeDuration;

        float step = CombatLungeMotion.ConsumeStepSeconds(
            CombatAction.SkillL, 1f / 60f, ref remaining);

        Assert.Equal(1f / 60f, step);
        Assert.Equal(SkillLungeDuration - step, remaining);
        Assert.Equal(0.125f, SkillLungeSpeed * step, 6);
    }

    [Fact]
    public void SkillLTailFrameConsumesOnlyRemainingTime()
    {
        float remaining = 0.005f;

        float step = CombatLungeMotion.ConsumeStepSeconds(
            CombatAction.SkillL, 1f / 30f, ref remaining);

        Assert.Equal(0.005f, step);
        Assert.Equal(0f, remaining);
        Assert.Equal(0.0375f, SkillLungeSpeed * step, 6);
    }

    [Fact]
    public void CompletedLungeNeverMovesAgain()
    {
        float remaining = 0.005f;
        _ = CombatLungeMotion.ConsumeStepSeconds(
            CombatAction.SkillL, 1f / 30f, ref remaining);

        float nextStep = CombatLungeMotion.ConsumeStepSeconds(
            CombatAction.SkillL, 1f / 30f, ref remaining);

        Assert.Equal(0f, nextStep);
        Assert.Equal(0f, remaining);
    }

    [Theory]
    [InlineData(0f)]
    [InlineData(-0.02f)]
    public void NonPositiveFrameTimeDoesNotConsumeLunge(float deltaTime)
    {
        float remaining = SkillLungeDuration;

        float step = CombatLungeMotion.ConsumeStepSeconds(
            CombatAction.SkillL, deltaTime, ref remaining);

        Assert.Equal(0f, step);
        Assert.Equal(SkillLungeDuration, remaining);
    }

    [Fact]
    public void StallFrameIsCappedWithoutLosingConfiguredDistance()
    {
        float remaining = SkillLungeDuration;
        float distance = 0f;
        float largestStep = 0f;

        while (remaining > 0f)
        {
            float step = CombatLungeMotion.ConsumeStepSeconds(
                CombatAction.SkillL, 0.2f, ref remaining);
            largestStep = MathF.Max(largestStep, step);
            distance += SkillLungeSpeed * step;
        }

        Assert.Equal(0f, remaining);
        Assert.InRange(largestStep, 0f, 1f / 30f);
        Assert.InRange(SkillLungeSpeed * largestStep, 0f, 0.25f);
        Assert.InRange(MathF.Abs(distance - 2.85f), 0f, 1e-6f);
    }

    [Theory]
    [InlineData((int)CombatAction.None)]
    [InlineData((int)CombatAction.Normal)]
    [InlineData((int)CombatAction.SkillK)]
    [InlineData((int)CombatAction.SkillIStart)]
    [InlineData((int)CombatAction.SkillIBody)]
    [InlineData((int)CombatAction.SkillIEnd)]
    public void NonLungeCombatActionsNeverConsumeMovement(
        int actionValue)
    {
        float remaining = SkillLungeDuration;

        float step = CombatLungeMotion.ConsumeStepSeconds(
            (CombatAction)actionValue, 1f / 30f, ref remaining);

        Assert.Equal(0f, step);
        Assert.Equal(SkillLungeDuration, remaining);
    }

    [Fact]
    public void ConsumeStepSecondsSteadyStateAllocatesNothing()
    {
        float remaining = SkillLungeDuration;
        _ = CombatLungeMotion.ConsumeStepSeconds(
            CombatAction.SkillL, 1f / 60f, ref remaining);

        long before = GC.GetAllocatedBytesForCurrentThread();
        float total = 0f;
        for (int i = 0; i < 10_000; i++)
        {
            remaining = SkillLungeDuration;
            total += CombatLungeMotion.ConsumeStepSeconds(
                CombatAction.SkillL, 1f / 60f, ref remaining);
        }
        long allocated = GC.GetAllocatedBytesForCurrentThread() - before;

        Assert.True(total > 0f);
        Assert.Equal(0, allocated);
    }
}
