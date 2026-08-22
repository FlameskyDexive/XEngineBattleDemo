// This file is part of the XEngine Game Engine
// Licensed under the MIT License. See the LICENSE file in the project root for details.

using System;

using XEngine.Cinemachine;
using XEngine.InputSystem;
using XEngine.Runtime;
using XEngine.Runtime.Resources;
using XEngine.Vector;

namespace XEngine.Zonezero;

/// <summary>
/// M8 combat states — the ZZZ demo's normal-attack combo and the big-skill camera chain.
/// </summary>
public sealed class NormalAttackState : PlayerStateBase
{
    private bool _enterNextAttack;

    public NormalAttackState(PlayerController controller) : base(controller) { }

    public override void Enter()
    {
        base.Enter();
        _enterNextAttack = false;

        // Auto-lock: snap toward the nearest enemy before the swing.
        GameObject? target = Controller.FindNearestEnemy();
        if (target != null)
        {
            Float3 direction = Float3.Normalize(target.Transform.Position - Model.Transform.Position);
            Model.Transform.Rotation = Quaternion.LookRotation(
                new Float3(direction.X, 0f, direction.Z), Float3.UnitY);
        }

        PlayAnimation("Attack_Normal_" + Model.CurrentNormalAttackIndex, 0f);

        // M10: slash arc at the swing start.
        XEngine.Zonezero.Vfx.ZonezeroVfx.SlashArc(Model.Transform.Position, Model.Transform.Forward);
    }

    public override void Update()
    {
        base.Update();

        // Combo window: Fire at ≥ 0.5 normalized time chains to the next segment.
        if (NormalizedTime() >= 0.5f && Controller.Fire != null && Controller.Fire.Triggered())
            _enterNextAttack = true;

        if (!IsAnimationEnd()) return;

        if (_enterNextAttack)
        {
            Model.AdvanceCombo();
            Controller.SwitchState(PlayerState.NormalAttack);
        }
        else
        {
            Controller.SwitchState(PlayerState.NormalAttackEnd);
        }
    }
}

/// <summary>Attack recovery — Fire chains, Evade resets the combo, move (after 0.5 s) runs.</summary>
public sealed class NormalAttackEndState : PlayerStateBase
{
    public NormalAttackEndState(PlayerController controller) : base(controller) { }

    public override void Enter()
    {
        base.Enter();
        PlayAnimation($"Attack_Normal_{Model.CurrentNormalAttackIndex}_End", 0f);
    }

    public override void Update()
    {
        base.Update();

        if (Controller.Fire != null && Controller.Fire.Triggered())
        {
            Model.AdvanceCombo();
            Controller.SwitchState(PlayerState.NormalAttack);
            return;
        }
        if (Controller.Evade != null && Controller.Evade.Triggered())
        {
            Model.CurrentNormalAttackIndex = 1;
            Controller.SwitchState(PlayerState.Evade_Back);
            return;
        }
        if (Controller.InputMove != Float2.Zero && AnimationPlayTime > 0.5f)
        {
            Model.CurrentNormalAttackIndex = 1;
            Controller.SwitchState(PlayerState.Run);
            return;
        }
        if (IsAnimationEnd())
        {
            Model.CurrentNormalAttackIndex = 1;
            Controller.SwitchState(PlayerState.Idle);
        }
    }
}

/// <summary>Big-skill intro — hard camera cut onto the character shot vcam.</summary>
public sealed class BigSkillStartState : PlayerStateBase
{
    public BigSkillStartState(PlayerController controller) : base(controller) { }

    public override void Enter()
    {
        base.Enter();
        Controller.BeginBigSkillCamera();
        PlayAnimation("BigSkill_Start", 0f);
    }

    public override void Update()
    {
        base.Update();
        if (IsAnimationEnd())
            Controller.SwitchState(PlayerState.BigSkill);
    }
}

/// <summary>Big-skill body — swaps to the finishing shot camera.</summary>
public sealed class BigSkillState : PlayerStateBase
{
    public BigSkillState(PlayerController controller) : base(controller) { }

    public override void Enter()
    {
        base.Enter();
        Controller.SwapToBigSkillShot();
        XEngine.Zonezero.Vfx.ZonezeroVfx.BigSkillBurst(Model.Transform.Position);
        PlayAnimation("BigSkill", 0f);
    }

    public override void Update()
    {
        base.Update();
        if (IsAnimationEnd())
            Controller.SwitchState(PlayerState.BigSkillEnd);
    }
}

/// <summary>Big-skill outro — FreeLook returns with a 1 s EaseInOut blend and re-centers.</summary>
public sealed class BigSkillEndState : PlayerStateBase
{
    public BigSkillEndState(PlayerController controller) : base(controller) { }

    public override void Enter()
    {
        base.Enter();
        Controller.EndBigSkillCamera();
        PlayAnimation("BigSkill_End", 0f);
    }

    public override void Update()
    {
        base.Update();
        if (Controller.Fire != null && Controller.Fire.Triggered())
        {
            Controller.SwitchState(PlayerState.NormalAttack);
            return;
        }
        if (Controller.Evade != null && Controller.Evade.Triggered())
        {
            Controller.SwitchState(PlayerState.Evade_Back);
            return;
        }
        if (Controller.InputMove != Float2.Zero)
        {
            Controller.SwitchState(PlayerState.Run);
            return;
        }
        if (IsAnimationEnd())
            Controller.SwitchState(PlayerState.Idle);
    }
}
