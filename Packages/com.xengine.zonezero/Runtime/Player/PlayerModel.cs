// This file is part of the XEngine Game Engine
// Licensed under the MIT License. See the LICENSE file in the project root for details.

using System.Collections.Generic;

using XEngine.Cinemachine;
using XEngine.Runtime;
using XEngine.Vector;

namespace XEngine.Zonezero;

/// <summary>
/// Port of the ZZZ demo's PlayerModel (zonezero M7): the per-character wrapper around one
/// team prefab — animator access, gait foot flag, current-state recording, and the
/// tag-team enter/exit lifecycle (spawn +0.8 right / +3.0 back of the outgoing model).
/// </summary>
public sealed class PlayerModel : MonoBehaviour
{
    public Animator? Animator;
    public CharacterController? CharacterController;
    public PlayerState CurrentState;

    /// <summary>Which foot the next Run entry leads with (starts Right, alternates).</summary>
    public ModelFoot Foot = ModelFoot.Right;

    /// <summary>Combo counter (M8) — resets when the character is benched.</summary>
    public int CurrentNormalAttackIndex = 1;

    public int NormalAttackSegments = 4;

    public override void OnEnable()
    {
        Animator ??= GetComponent<Animator>();
        CharacterController ??= GetComponent<CharacterController>();
        if (Animator != null)
            Animator.ApplyRootMotion = true;
    }

    public override void OnDisable() => CurrentNormalAttackIndex = 1;

    /// <summary>Combo advance: 1..N wrap (ZZZ skillConfig index over the damage table).</summary>
    public void AdvanceCombo()
    {
        CurrentNormalAttackIndex++;
        if (CurrentNormalAttackIndex > NormalAttackSegments)
            CurrentNormalAttackIndex = 1;
    }

    /// <summary>Tag-team entrance: teleport to the previous model's behind-right and copy
    /// its rotation (the ZZZ SwitchIn spawn offsets).</summary>
    public void Enter(Float3 previousPosition, Quaternion previousRotation)
    {
        Transform.Position = previousPosition
            + previousRotation * Float3.UnitX * 0.8f
            + previousRotation * (-Float3.UnitZ) * 3.0f;
        Transform.Rotation = previousRotation;
    }
}
