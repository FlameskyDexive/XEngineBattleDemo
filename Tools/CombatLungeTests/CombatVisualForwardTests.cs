using XEngine.Animation;
using XEngine.Runtime;
using XEngine.Vector;
using XEngine.Zonezero.Combat;

using Xunit;

namespace XEngine.Zonezero.Runtime.Tests;

public sealed class CombatVisualForwardTests
{
    [Theory]
    [InlineData(0f)]
    [InlineData(90f)]
    [InlineData(-90f)]
    [InlineData(180f)]
    public void SourceMinusZBodyFacesGameplayForwardWithoutChangingAttackDirection(float yaw)
    {
        using var actor = new GameObject("Actor");
        var (animator, model, hips, toe, _) = AddImportedModel(actor);
        actor.Transform.Position = new Float3(3f, 0f, -4f);
        actor.Transform.LocalRotation = Quaternion.FromEuler(0f, yaw, 0f);
        model.Transform.LocalPosition = new Float3(0f, 0.1f, 0.2f);
        model.Transform.LocalScale = new Float3(0.9f);
        Float3 actorPosition = actor.Transform.Position;
        Quaternion actorRotation = actor.Transform.Rotation;
        Float3 modelPosition = model.Transform.LocalPosition;
        Float3 modelScale = model.Transform.LocalScale;
        var gameplayChild = new GameObject("GameplayMarker");
        gameplayChild.SetParent(actor, worldPositionStays: false);

        CombatMotor.ConfigureVisualForward(animator);

        Float3 anatomicalForward = Float3.Normalize(toe.Transform.Position - hips.Transform.Position);
        Assert.True(Float3.Dot(anatomicalForward, actor.Transform.Forward) > 0.9999f);
        Assert.Equal(actorPosition, actor.Transform.Position);
        Assert.Equal(actorRotation, actor.Transform.Rotation);
        Assert.Equal(modelPosition, model.Transform.LocalPosition);
        Assert.Equal(modelScale, model.Transform.LocalScale);
        Assert.Equal(Quaternion.Identity, model.Transform.LocalRotation);
        Assert.Same(actor, gameplayChild.Parent);
        Assert.Same(actor, animator.GameObject);

        using var target = new GameObject("Target");
        target.Transform.Position = actorPosition + actor.Transform.Forward;
        Assert.True(CombatMotor.InAttackCone(actor, target, 2f, 65f));
        target.Transform.Position = actorPosition - actor.Transform.Forward;
        Assert.False(CombatMotor.InAttackCone(actor, target, 2f, 65f));
    }

    [Fact]
    public void RepeatedSetupAndAnimatorEnableCycleDoNotStackCorrections()
    {
        using var actor = new GameObject("Actor");
        var (animator, model, hips, toe, _) = AddImportedModel(actor);

        CombatMotor.ConfigureVisualForward(animator);
        Transform visual = animator.SkeletonRoot!;
        animator.Enabled = false;
        animator.Enabled = true;
        CombatMotor.ConfigureVisualForward(animator);

        Assert.Same(visual, animator.SkeletonRoot);
        Assert.Single(actor.Children);
        Assert.Single(visual.GameObject.Children);
        Assert.Same(visual.GameObject, model.Parent);
        Assert.True(animator.Enabled);
        Assert.True(Float3.Dot(Float3.Normalize(toe.Transform.Position - hips.Transform.Position),
            actor.Transform.Forward) > 0.9999f);
    }

    [Fact]
    public void AnimationAndSkinPathsStillResolveFromNewBindingRoot()
    {
        using var actor = new GameObject("Actor");
        var (animator, model, hips, toe, renderer) = AddImportedModel(actor);
        animator.Enabled = false;
        SkeletonBinding before = SkeletonBinding.Build(actor.Transform);

        CombatMotor.ConfigureVisualForward(animator);
        animator.RebuildGraph();
        SkeletonBinding after = animator.Skeleton!;

        Assert.False(animator.Enabled);
        Assert.Equal(before.Paths, after.Paths);
        Assert.Equal(-1, after.IndexOfBonePath("BattleVisual"));
        Assert.Same(model.Transform, after.Bones[after.IndexOfBonePath("Avatar_Model")]);
        Assert.Same(hips.Transform, after.Bones[after.IndexOfBonePath("Avatar_Model/Bip001")]);
        Assert.Same(toe.Transform, after.Bones[after.IndexOfBonePath("Avatar_Model/Bip001/Toe")]);
        renderer.OnEnable(); // Force the same lazy re-resolution used after scene reload.
        Assert.Same(hips.Transform, renderer.RootBone);
        Assert.Same(toe.Transform, Assert.Single(renderer.Bones!));
    }

    [Fact]
    public void ExplicitSkeletonRootIsPreserved()
    {
        using var actor = new GameObject("Actor");
        var (animator, model, _, _, _) = AddImportedModel(actor);
        animator.SkeletonRoot = model.Transform;

        CombatMotor.ConfigureVisualForward(animator);

        Assert.Same(model.Transform, animator.SkeletonRoot);
        Assert.Same(actor, model.Parent);
        Assert.Single(actor.Children);
        Assert.Equal(Quaternion.Identity, model.Transform.LocalRotation);
    }

    [Fact]
    public void MissingVisualModelLeavesActorUnchanged()
    {
        using var actor = new GameObject("Actor");
        Animator animator = actor.AddComponent<Animator>();

        CombatMotor.ConfigureVisualForward(animator);

        Assert.Null(animator.SkeletonRoot);
        Assert.Empty(actor.Children);
    }

    private static (Animator Animator, GameObject Model, GameObject Hips, GameObject Toe,
        SkinnedMeshRenderer Renderer) AddImportedModel(GameObject actor)
    {
        Animator animator = actor.AddComponent<Animator>();
        animator.PlayAutomatically = false;
        var model = new GameObject("Avatar_Model");
        model.SetParent(actor, worldPositionStays: false);
        var hips = new GameObject("Bip001");
        hips.SetParent(model, worldPositionStays: false);
        var toe = new GameObject("Toe");
        toe.SetParent(hips, worldPositionStays: false);
        toe.Transform.LocalPosition = -Float3.UnitZ;
        var body = new GameObject("Body");
        body.SetParent(model, worldPositionStays: false);
        SkinnedMeshRenderer renderer = body.AddComponent<SkinnedMeshRenderer>();
        renderer.RootBonePath = "Avatar_Model/Bip001";
        renderer.BonePaths = new[] { "Avatar_Model/Bip001/Toe" };
        return (animator, model, hips, toe, renderer);
    }
}
