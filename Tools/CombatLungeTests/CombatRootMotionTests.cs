using System.Reflection;
using AnimationCurve = XEngine.Runtime.AnimationCurve;

using XEngine.Runtime;
using XEngine.Runtime.Resources;
using XEngine.Vector;
using XEngine.Zonezero;
using XEngine.Zonezero.Combat;

using Xunit;
using Xunit.Abstractions;

namespace XEngine.Zonezero.Runtime.Tests;

[CollectionDefinition("Combat physics", DisableParallelization = true)]
public sealed class CombatPhysicsCollection;

[Collection("Combat physics")]
public sealed class CombatRootMotionTests : IDisposable
{
    private readonly bool _wasPlaying = Application.IsPlaying;
    private readonly bool _wasEditor = Application.IsEditor;
    private readonly bool _wasPaused = Application.IsPaused;
    private readonly Scene _scene;
    private readonly TimeData _time = new() { DeltaTime = 1f / 60f };
    private readonly ITestOutputHelper _output;

    public CombatRootMotionTests(ITestOutputHelper output)
    {
        _output = output;
        Application.IsPlaying = true;
        Application.IsEditor = false;
        Application.IsPaused = false;
        Time.TimeStack.Push(_time);
        _scene = new Scene();
        _scene.Enable();
        _scene.Physics.UseMultithreading = false;
    }

    [Theory]
    [InlineData(0f)]
    [InlineData(90f)]
    [InlineData(180f)]
    [InlineData(-90f)]
    public void AnimationDistanceUsesActorFacingAndOneCharacterControllerMove(float yaw)
    {
        CharacterController cc = AddActor(yaw);
        Float3 before = cc.Transform.Position;
        var motion = new CombatRootMotion();

        motion.Apply(cc, new Float3(0f, 2f, 0.4f));

        Float3 expected = cc.Transform.Forward * 0.4f;
        AssertNear(expected, cc.Transform.Position - before);
        AssertNear(expected, motion.RequestedDelta);
        AssertNear(expected, motion.AppliedDelta);
        Assert.Equal(0.4f, motion.RequestedDistance, 5);
        Assert.Equal(0.4f, motion.AppliedDistance, 5);
        Assert.Equal(1, motion.Steps);
    }

    [Fact]
    public void RootMotionStopsAtWallAndDoesNotReplayRejectedDistanceAfterWallRemoval()
    {
        CharacterController cc = AddActor();
        var wall = new GameObject("Wall");
        wall.Transform.Position = new Float3(0f, 4f, 2f);
        wall.AddComponent<BoxCollider>().Size = new Float3(10f, 10f, 0.5f);
        _scene.Add(wall);
        var motion = new CombatRootMotion();

        for (int i = 0; i < 12; i++)
            motion.Apply(cc, Float3.UnitZ * 0.25f);

        Assert.Equal(3f, motion.RequestedDistance, 5);
        Assert.InRange(cc.Transform.Position.Z, 0.5f, 1.35f);
        Assert.True(motion.AppliedDistance < motion.RequestedDistance - 1f);
        Float3 stopped = cc.Transform.Position;
        wall.Dispose();
        motion.Apply(cc, Float3.Zero);
        AssertNear(stopped, cc.Transform.Position);
        motion.Apply(cc, Float3.UnitZ * 0.1f);
        AssertNear(stopped + Float3.UnitZ * 0.1f, cc.Transform.Position);
    }

    [Fact]
    public void ClearingFramePreservesCumulativeAcceptanceDistances()
    {
        CharacterController cc = AddActor();
        var motion = new CombatRootMotion();
        motion.Apply(cc, Float3.UnitZ * 0.5f);

        motion.ClearFrame();

        Assert.Equal(Float3.Zero, motion.RequestedDelta);
        Assert.Equal(Float3.Zero, motion.AppliedDelta);
        Assert.Equal(0.5f, motion.RequestedDistance, 5);
        Assert.Equal(0.5f, motion.AppliedDistance, 5);
        Assert.Equal(1, motion.Steps);
    }

    [Theory]
    [InlineData((int)CombatAction.Normal)]
    [InlineData((int)CombatAction.SkillK)]
    [InlineData((int)CombatAction.SkillL)]
    [InlineData((int)CombatAction.SkillIStart)]
    [InlineData((int)CombatAction.SkillIBody)]
    [InlineData((int)CombatAction.SkillIEnd)]
    public void HeroConsumesAnimationInEveryCombatPhaseButNotAfterDisableOrCompletion(int action)
    {
        var (hero, animator, cc, move) = AddHero();
        SetField(hero, "_action", (CombatAction)action);
        SetField(hero, "_hitDoneForClip", true);
        Float3 before = cc.Transform.Position;
        Quaternion heading = cc.Transform.Rotation;

        move(Float3.UnitZ * 0.2f, Quaternion.FromEuler(0f, 90f, 0f));

        AssertNear(before + Float3.UnitZ * 0.2f, cc.Transform.Position);
        Assert.Equal(heading, cc.Transform.Rotation);
        Assert.Equal(1, hero.RootMotionSteps);
        hero.OnDisable();
        move(Float3.UnitZ * 5f, Quaternion.Identity);
        hero.OnEnable();
        move(Float3.UnitZ * 5f, Quaternion.Identity);
        AssertNear(before + Float3.UnitZ * 0.2f, cc.Transform.Position);
        Assert.Equal(1, hero.RootMotionSteps);
        Assert.False(hero.IsBusy);
        Assert.True(animator.ApplyRootMotion);
    }

    [Fact]
    public void SkillLUpdateDoesNotAddThePreviousTimedLunge()
    {
        var (hero, animator, cc, move) = AddHero();
        SetField(hero, "_action", CombatAction.SkillL);
        SetField(hero, "_hitDoneForClip", true);
        using var clip = new AnimationClip { Name = "Skill", Duration = 1f };
        animator.Play(clip);
        Float3 before = cc.Transform.Position;

        hero.Update();
        AssertNear(before, cc.Transform.Position);
        move(Float3.UnitZ * 0.25f, Quaternion.Identity);
        AssertNear(before + Float3.UnitZ * 0.25f, cc.Transform.Position);
        Assert.Equal(0.25f, hero.RootMotionRequestedDistance, 5);
    }

    [Fact]
    public void ManualLocomotionIgnoresAnimationTravel()
    {
        var (hero, _, cc, move) = AddHero();
        Float3 before = cc.Transform.Position;

        move(Float3.UnitZ * 3f, Quaternion.Identity);

        AssertNear(before, cc.Transform.Position);
        Assert.Equal(0, hero.RootMotionSteps);
    }

    [Fact]
    public void PlanarMotionKeepsAuthoredRotationInTheVisualPose()
    {
        var (_, animator, _, _) = AddHero();

        Assert.True(animator.ApplyRootMotion);
        Assert.True(animator.RootMotionPlanar);
        Assert.False(animator.ApplyRootMotionRotation);
        Assert.Equal(1f / 30f, animator.MaximumDeltaTime);
    }

    [Theory]
    [InlineData(1f / 60f)]
    [InlineData(0.2f)]
    [InlineData(1f)]
    public void DelayedAnimationFramesKeepPoseAndCapsuleInStepWithoutLosingAuthoredTravel(float frameTime)
    {
        var (hero, animator, cc, move) = AddHero();
        var root = new GameObject("Root");
        root.SetParent(cc.GameObject, worldPositionStays: false);
        animator.RebuildGraph();
        animator.Wrap = AnimationWrapMode.ClampForever;
        animator.OnAnimatorMove += move;
        using var clip = new AnimationClip { Name = "Dash", Duration = 1f, RootBonePath = "Root" };
        var forward = new AnimationCurve(new[] { new KeyFrame(0f, 0f), new KeyFrame(1f, 6f) });
        forward.SmoothTangents(CurveTangent.Linear);
        clip.AddBone(new AnimationClip.AnimBone
        {
            BoneName = "Root",
            PosX = new AnimationCurve(new[] { new KeyFrame(0f, 0f) }),
            PosY = new AnimationCurve(new[] { new KeyFrame(0f, 0f) }),
            PosZ = forward,
        });
        var state = animator.Play(clip);
        SetField(hero, "_action", CombatAction.SkillL);
        SetField(hero, "_hitDoneForClip", true);
        _time.DeltaTime = frameTime;

        for (int i = 0; i < 65; i++)
        {
            animator.LateUpdate();
            Assert.InRange(hero.RootMotionRequestedDelta.Z, 0f, 0.20001f);
            Assert.InRange(MathF.Abs(cc.Transform.Position.Z - (float)state.Time * 6f), 0f, 1e-4f);
        }

        Assert.Equal(1f, (float)state.Time, 5);
        Assert.Equal(6f, cc.Transform.Position.Z, 4);
        Assert.Equal(6f, hero.RootMotionRequestedDistance, 4);
        Assert.Equal(6f, hero.RootMotionAppliedDistance, 4);
        hero.OnDisable();
        animator.LateUpdate();
        Assert.Equal(6f, cc.Transform.Position.Z, 4);
    }

    [Fact]
    public void HitSweepClipsAStalledFrameToItsAuthoredWindow()
    {
        bool hit = CombatMotor.TryGetHitSweep(Float3.Zero, Float3.UnitZ * 10f,
            0f, 1f, out Float3 start, out Float3 end);

        Assert.True(hit);
        Assert.Equal(3.2f, start.Z, 5);
        Assert.Equal(7.2f, end.Z, 5);
        Assert.False(CombatMotor.TryGetHitSweep(Float3.Zero, Float3.UnitZ,
            0f, 0.2f, out _, out _));
        Assert.False(CombatMotor.TryGetHitSweep(Float3.Zero, Float3.UnitZ,
            0.8f, 1f, out _, out _));
        Assert.False(CombatMotor.TryGetHitSweep(Float3.Zero, Float3.UnitZ,
            0.7f, 0.2f, out _, out _));
    }

    [Theory]
    [InlineData((int)CombatAction.SkillL, 1)]
    [InlineData((int)CombatAction.Normal, 0)]
    [InlineData((int)CombatAction.SkillK, 0)]
    public void OnlyDashOpensAnEarlyDamageWindowAndCrossingTheVictimHitsOnce(int action, int expectedHits)
    {
        var (hero, animator, cc, move) = AddHero();
        var victim = new GameObject("Victim");
        victim.Transform.Position = cc.Transform.Position + Float3.UnitZ * 3f;
        EnemyController hurt = victim.AddComponent<EnemyController>();
        _scene.Add(victim);
        hero.AttackRange = 0.5f;
        SetField(hero, "_action", (CombatAction)action);
        SetField(hero, "_lockedTarget", victim);
        using var clip = new AnimationClip { Name = "Attack", Duration = 1f };
        var state = animator.Play(clip);
        state.SetTime(0.12f);

        // The root crosses the target before the common 0.32 window would have opened.
        move(Float3.UnitZ * 3.6f, Quaternion.Identity);

        Assert.Equal(expectedHits, hero.SuccessfulHits);
        Assert.Equal(expectedHits, hurt.HitCount);
        victim.Transform.Position = cc.Transform.Position + Float3.UnitZ * 0.2f;
        state.SetTime(0.20f);
        move(Float3.UnitZ * 0.1f, Quaternion.Identity);
        Assert.Equal(expectedHits, hero.SuccessfulHits);
        Assert.Equal(expectedHits, hurt.HitCount);
    }

    [Fact]
    public void SweptHitFindsCrossedVictimButRejectsTargetsBehindTheAttack()
    {
        using var attacker = new GameObject("Attacker");
        using var victim = new GameObject("Victim");
        attacker.Transform.Position = Float3.UnitZ * 3f;
        victim.Transform.Position = Float3.UnitZ * 1.5f;

        Assert.False(CombatMotor.InAttackCone(attacker, victim, 1f, 65f));
        Assert.True(CombatMotor.InAttackSweep(attacker, victim, Float3.Zero,
            attacker.Transform.Position, 1f, 65f));
        victim.Transform.Position = new Float3(0.5f, 0f, 1.5f);
        Assert.True(CombatMotor.InAttackSweep(attacker, victim, Float3.Zero,
            attacker.Transform.Position, 1f, 65f));
        victim.Transform.Position = -Float3.UnitZ;
        Assert.False(CombatMotor.InAttackSweep(attacker, victim, Float3.Zero,
            attacker.Transform.Position, 1f, 65f));
    }

    [Fact]
    public void SidewaysSweepCanHitBetweenEndpoints()
    {
        using var attacker = new GameObject("Attacker");
        using var victim = new GameObject("Victim");
        victim.Transform.Position = Float3.UnitZ * 0.5f;
        Assert.True(CombatMotor.InAttackSweep(attacker, victim,
            -Float3.UnitX * 3f, Float3.UnitX * 3f, 1f, 25f));
    }

    [Fact]
    public void RootMotionCallbackDoesNotAddAllocationsBeyondCharacterController()
    {
        var (hero, _, cc, move) = AddHero();
        SetField(hero, "_action", CombatAction.SkillL);
        SetField(hero, "_hitDoneForClip", true);
        for (int i = 0; i < 100; i++) move(Float3.UnitZ * 0.001f, Quaternion.Identity);
        long baselineBefore = GC.GetAllocatedBytesForCurrentThread();
        for (int i = 0; i < 1_000; i++) cc.Move(Float3.UnitZ * 0.001f);
        long baseline = GC.GetAllocatedBytesForCurrentThread() - baselineBefore;
        long before = GC.GetAllocatedBytesForCurrentThread();

        for (int i = 0; i < 1_000; i++) move(Float3.UnitZ * 0.001f, Quaternion.Identity);

        long callbackBytes = GC.GetAllocatedBytesForCurrentThread() - before;
        _output.WriteLine($"1000 steps: CharacterController={baseline} B; root-motion callback={callbackBytes} B");
        Assert.Equal(baseline, callbackBytes);
        Assert.Equal(1_100, hero.RootMotionSteps);
    }

    private CharacterController AddActor(float yaw = 0f)
    {
        var actor = new GameObject("Actor");
        actor.Transform.Position = new Float3(0f, 4f, 0f);
        actor.Transform.Rotation = Quaternion.FromEuler(0f, yaw, 0f);
        CharacterController cc = actor.AddComponent<CharacterController>();
        cc.Shape = CharacterController.ColliderShape.Capsule;
        _scene.Add(actor);
        return cc;
    }

    private (HeroCombatController Hero, Animator Animator, CharacterController Controller,
        Action<Float3, Quaternion> Move) AddHero()
    {
        CharacterController cc = AddActor();
        Animator animator = cc.GameObject.AddComponent<Animator>();
        animator.PlayAutomatically = false;
        CombatMotor.ConfigureRootMotion(animator);
        HeroCombatController hero = cc.GameObject.AddComponent<HeroCombatController>();
        SetField(hero, "_cc", cc);
        SetField(hero, "_animator", animator);
        hero.OnEnable();
        var move = (Action<Float3, Quaternion>)typeof(HeroCombatController)
            .GetMethod("ApplyAnimationMotion", BindingFlags.Instance | BindingFlags.NonPublic)!
            .CreateDelegate(typeof(Action<Float3, Quaternion>), hero);
        return (hero, animator, cc, move);
    }

    private static void SetField<T>(HeroCombatController hero, string name, T value)
        => typeof(HeroCombatController).GetField(name, BindingFlags.Instance | BindingFlags.NonPublic)!
            .SetValue(hero, value);

    private static void AssertNear(Float3 expected, Float3 actual)
        => Assert.True(Float3.Length(expected - actual) < 1e-4f, $"Expected {expected}, got {actual}");

    public void Dispose()
    {
        _scene.Disable();
        _scene.Dispose();
        Time.TimeStack.Pop();
        Application.IsPlaying = _wasPlaying;
        Application.IsEditor = _wasEditor;
        Application.IsPaused = _wasPaused;
    }
}
