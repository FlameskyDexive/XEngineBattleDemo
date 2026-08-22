using System;

// This file is part of the XEngine Game Engine
// Licensed under the MIT License. See the LICENSE file in the project root for details.

using XEngine.Cinemachine;
using XEngine.InputSystem;
using XEngine.Runtime;
using XEngine.Vector;

namespace XEngine.Zonezero;

/// <summary>Idle — standing by; any action leaves.</summary>
public sealed class IdleState : PlayerStateBase
{
    public IdleState(PlayerController controller) : base(controller) { }

    public override void Enter()
    {
        base.Enter();
        PlayAnimation("Idle");
    }

    public override void Update()
    {
        base.Update();
        if (Controller.BigSkill != null && Controller.BigSkill.Triggered())
        {
            Controller.SwitchState(PlayerState.BigSkillStart);
            return;
        }
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
            Controller.SwitchState(PlayerState.Run);
    }
}

/// <summary>Run — camera-relative steering with the alternating-foot gait and the 180°
/// turn interrupt. Forward translation is Run-clip root motion.</summary>
public sealed class RunState : PlayerStateBase
{
    public RunState(PlayerController controller) : base(controller) { }

    public override void Enter()
    {
        base.Enter();
        // Foot-phase selection: start the Run clip at 0 (left foot out) or 0.5 (right).
        if (Model.Foot == ModelFoot.Right)
        {
            PlayAnimation("Run", 0.25f, 0f);
            Model.Foot = ModelFoot.Left;
        }
        else
        {
            PlayAnimation("Run", 0.25f, 0.5f);
            Model.Foot = ModelFoot.Right;
        }
    }

    public override void Update()
    {
        base.Update();
        if (Controller.BigSkill != null && Controller.BigSkill.Triggered())
        {
            Controller.SwitchState(PlayerState.BigSkillStart);
            return;
        }
        if (Controller.Fire != null && Controller.Fire.Triggered())
        {
            Controller.SwitchState(PlayerState.NormalAttack);
            return;
        }
        // Held dodge while moving = forward dash.
        if (Controller.Evade != null && Controller.Evade.IsPressed())
        {
            Controller.SwitchState(PlayerState.Evade_Front);
            return;
        }
        if (Controller.InputMove == Float2.Zero)
        {
            Controller.SwitchState(PlayerState.RunEnd);
            return;
        }

        // Camera-relative target direction + smooth turn (rotationSpeed 8).
        Camera? camera = Controller.MainCamera;
        Float3 inputMove = new(Controller.InputMove.X, 0f, Controller.InputMove.Y);
        float cameraYaw = camera != null ? Quaternion.ToEuler(camera.Transform.Rotation).Y : 0f;
        Float3 targetDirection = Quaternion.FromEuler(0f, cameraYaw, 0f) * inputMove;
        Quaternion targetRotation = Quaternion.LookRotation(targetDirection, Float3.UnitY);

        float modelYaw = Quaternion.ToEuler(Model.Transform.Rotation).Y;
        float targetYaw = Quaternion.ToEuler(targetRotation).Y;
        float angle = MathF.Abs(targetYaw - modelYaw);
        angle = MathF.Min(angle, 360f - angle);

        // 180° turn deadband: (177.5°, 182.5°).
        if (angle > 177.5f && angle < 182.5f)
        {
            Controller.SwitchState(PlayerState.TurnBack);
            return;
        }
        Model.Transform.Rotation = Quaternion.Slerp(Model.Transform.Rotation, targetRotation,
            Time.DeltaTime * Controller.RotationSpeed);
    }
}

/// <summary>RunEnd — the run-stop; left/right-foot variant chosen by the gait flag.</summary>
public sealed class RunEndState : PlayerStateBase
{
    public RunEndState(PlayerController controller) : base(controller) { }

    public override void Enter()
    {
        base.Enter();
        PlayAnimation(Model.Foot == ModelFoot.Right ? "Run_End_R" : "Run_End_L", 0.1f);
    }

    public override void Update()
    {
        base.Update();
        if (Controller.BigSkill != null && Controller.BigSkill.Triggered())
        {
            Controller.SwitchState(PlayerState.BigSkillStart);
            return;
        }
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

/// <summary>TurnBack — the 180° turn clip, back into Run when done.</summary>
public sealed class TurnBackState : PlayerStateBase
{
    public TurnBackState(PlayerController controller) : base(controller) { }

    public override void Enter()
    {
        base.Enter();
        PlayAnimation("TurnBack", 0.1f);
    }

    public override void Update()
    {
        base.Update();
        if (Controller.BigSkill != null && Controller.BigSkill.Triggered())
        {
            Controller.SwitchState(PlayerState.BigSkillStart);
            return;
        }
        if (IsAnimationEnd())
            Controller.SwitchState(PlayerState.Run);
    }
}

/// <summary>Evade — clip chosen by the previous state (backstep from standstill, forward
/// dash from run); distance/speed are Evade_Front/Back root motion.</summary>
public sealed class EvadeState : PlayerStateBase
{
    public EvadeState(PlayerController controller) : base(controller) { }

    private PlayerState _requested;

    public override void Enter()
    {
        base.Enter();
        // The reference reads playerModel.currentState — the state BEFORE the evade request
        // rewrote it. The controller records that as PreviousState.
        _requested = Controller.PreviousState;
        switch (Controller.PreviousState)
        {
            case PlayerState.Idle:
            case PlayerState.RunEnd:
            case PlayerState.NormalAttackEnd:
                PlayAnimation("Evade_Back");
                break;
            case PlayerState.Run:
                PlayAnimation("Evade_Front");
                break;
            // TurnBack: no animation (falls through, matching the reference).
        }
        XEngine.Zonezero.Vfx.ZonezeroVfx.EvadeDust(Model.Transform.Position);
    }

    public override void Update()
    {
        base.Update();
        if (!IsAnimationEnd()) return;

        if (Controller.LastEvadeVariant == PlayerState.Evade_Front)
        {
            if (Controller.Evade != null && Controller.Evade.IsPressed())
            {
                Controller.SwitchState(PlayerState.Run);   // dash-to-run chain
                return;
            }
            Controller.SwitchState(PlayerState.Evade_Front_End);
        }
        else
        {
            Controller.SwitchState(PlayerState.Evade_Back_End);
        }
    }
}

/// <summary>EvadeEnd — the recovery landing; cancel into anything or settle to Idle.</summary>
public sealed class EvadeEndState : PlayerStateBase
{
    public EvadeEndState(PlayerController controller) : base(controller) { }

    public override void Enter()
    {
        base.Enter();
        // The recorded state at Enter time was already rewritten to the evade itself; the
        // reference reads playerModel.currentState (Evade_Front/Evade_Back) — mirror via the
        // last requested variant on the controller.
        PlayAnimation(Controller.LastEvadeVariant == PlayerState.Evade_Front
            ? "Evade_Front_End" : "Evade_Back_End");
    }

    public override void Update()
    {
        base.Update();
        if (Controller.BigSkill != null && Controller.BigSkill.Triggered())
        {
            Controller.SwitchState(PlayerState.BigSkillStart);
            return;
        }
        if (Controller.Fire != null && Controller.Fire.Triggered())
        {
            Controller.SwitchState(PlayerState.NormalAttack);
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

/// <summary>SwitchInNormal — the tag-entrance (hard cut clip), cancellable.</summary>
public sealed class SwitchInNormalState : PlayerStateBase
{
    public SwitchInNormalState(PlayerController controller) : base(controller) { }

    public override void Enter()
    {
        base.Enter();
        PlayAnimation("SwitchIn_Normal", 0f);
        XEngine.Zonezero.Vfx.ZonezeroVfx.SwitchFlash(Model.Transform.Position);
    }

    public override void Update()
    {
        base.Update();
        if (Controller.BigSkill != null && Controller.BigSkill.Triggered())
        {
            Controller.SwitchState(PlayerState.BigSkillStart);
            return;
        }
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
