// This file is part of the XEngine Game Engine
// Licensed under the MIT License. See the LICENSE file in the project root for details.

using XEngine.Runtime;
using XEngine.Vector;

namespace XEngine.Zonezero.Combat;

internal enum CombatAction
{
    None,
    Normal,
    SkillK,
    SkillL,
    SkillIStart,
    SkillIBody,
    SkillIEnd,
}

/// <summary>One collision solve per animation step, with allocation-free acceptance telemetry.</summary>
internal struct CombatRootMotion
{
    internal Float3 RequestedDelta;
    internal Float3 AppliedDelta;
    internal float RequestedDistance;
    internal float AppliedDistance;
    internal int Steps;

    [HotPath]
    internal void ClearFrame()
    {
        RequestedDelta = Float3.Zero;
        AppliedDelta = Float3.Zero;
    }

    [HotPath]
    internal void Apply(CharacterController controller, Float3 bodyDelta)
    {
        // Animator delivers a distance, already sampled across the animation's elapsed time.
        // Multiplying by deltaTime here would make the attack almost stationary. The Animator
        // has also included the visual-root axis correction in this actor-local vector.
        Float3 worldDelta = controller.Transform.TransformVector(bodyDelta);
        worldDelta.Y = 0f;
        Float3 before = controller.Transform.Position;
        controller.Move(worldDelta);
        Float3 applied = controller.Transform.Position - before;
        RequestedDelta = worldDelta;
        AppliedDelta = applied;
        RequestedDistance += Float3.Length(worldDelta);
        AppliedDistance += Float2.Length(new Float2(applied.X, applied.Z));
        Steps++;
    }
}
