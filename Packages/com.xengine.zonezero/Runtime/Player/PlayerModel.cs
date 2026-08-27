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

    /// <summary>Combo length (the ZZZ skillConfig.normalAttackDamageMultiple length). Zero means
    /// "derive from the controller" — Nostradamus only has three Attack_Normal clips, Corin five,
    /// so a hardcoded 4 breaks one side or the other.</summary>
    public int NormalAttackSegments;

    public override void OnEnable()
    {
        Animator ??= GetComponent<Animator>();
        // The FSM's gravity/locomotion route through the CharacterController; add one when the
        // scene/prefab predates it being baked in (ZZZ player prefabs carry the component).
        CharacterController ??= GetComponent<CharacterController>() ?? AddComponent<CharacterController>();
        // Locomotion parity: the ZZZ reference ran these characters code-driven ( ripped clips are
        // in-place cycles; run/evade translation goes through the CharacterController), so root
        // motion stays OFF — otherwise any clip-authored root path would double-write the body.
        if (Animator != null)
            Animator.ApplyRootMotion = false;
    }

    public override void OnDisable() => CurrentNormalAttackIndex = 1;

    /// <summary>Combo advance: 1..N wrap (ZZZ skillConfig index over the damage table).</summary>
    public void AdvanceCombo()
    {
        int max = NormalAttackSegments > 0 ? NormalAttackSegments : DerivedSegments();
        CurrentNormalAttackIndex++;
        if (CurrentNormalAttackIndex > max)
            CurrentNormalAttackIndex = 1;
    }

    /// <summary>Highest consecutive Attack_Normal_N state present on the animator (fallback 4).</summary>
    private int DerivedSegments()
    {
        Animator? animator = Animator;
        if (animator == null)
            return NormalAttackSegments = 4;
        int n = 0;
        while (n < 8 && animator.HasState($"Attack_Normal_{n + 1}"))
            n++;
        return NormalAttackSegments = n > 0 ? n : 4;
    }

    #region 动画事件回调（Unity 工程同名方法的移植；由剪辑事件经引擎事件管线解析调用）
    /// <summary>开启武器伤害判定窗口（剪辑事件 StartHit）。</summary>
    public void StartHit(int weaponIndex)
    {
        WeaponController? weapon = GetComponentInChildren<WeaponController>();
        weapon?.StartHit();
    }

    /// <summary>关闭伤害检测（剪辑事件 StopHit）。</summary>
    public void StopHit(int weaponIndex)
    {
        WeaponController? weapon = GetComponentInChildren<WeaponController>();
        weapon?.StopHit();
    }

    /// <summary>迈出左脚（Run 剪辑事件）。</summary>
    public void SetOutLeftFoot() => Foot = ModelFoot.Left;

    /// <summary>迈出右脚（Run 剪辑事件）。</summary>
    public void SetOutRightFoot() => Foot = ModelFoot.Right;
    #endregion

    /// <summary>Tag-team entrance: teleport to the previous model's behind-right and copy its
    /// rotation. Like the ZZZ original, route the teleport through the CharacterController so the
    /// capsule leaves collisions resolved instead of materializing inside geometry.</summary>
    public void Enter(Float3 previousPosition, Quaternion previousRotation)
    {
        Float3 target = previousPosition
            + previousRotation * Float3.UnitX * 0.8f
            + previousRotation * (-Float3.UnitZ) * 3.0f;
        if (CharacterController != null)
            CharacterController.Move(target - Transform.Position);
        else
            Transform.Position = target;
        Transform.Rotation = previousRotation;
    }
}
