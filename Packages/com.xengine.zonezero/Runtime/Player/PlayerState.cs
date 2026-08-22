// This file is part of the XEngine Game Engine
// Licensed under the MIT License. See the LICENSE file in the project root for details.

using XEngine.Cinemachine;
using XEngine.InputSystem;
using XEngine.Animation;
using XEngine.Runtime;
using XEngine.Vector;

namespace XEngine.Zonezero;

/// <summary>Player FSM states (ZZZ PlayerStateBase enum, verbatim).</summary>
public enum PlayerState
{
    Idle,
    Run,
    RunEnd,
    TurnBack,
    Evade_Front,
    Evade_Back,
    EvadeEnd,
    Evade_Front_End,
    Evade_Back_End,
    NormalAttack,
    NormalAttackEnd,
    BigSkillStart,
    BigSkill,
    BigSkillEnd,
    SwitchInNormal,
}

/// <summary>Which foot the run gait leads with (alternates each Run entry).</summary>
public enum ModelFoot
{
    Right,
    Left,
}

/// <summary>
/// Port of the ZZZ demo's PlayerStateBase (zonezero M7): per-state animation playback,
/// the shared gravity + tag-switch poll, and the normalized-time helpers every state uses.
/// States are plain classes ticked by the <see cref="PlayerController"/> (the ZZZ
/// MonoManager pump collapses into the controller's Update here).
/// </summary>
public abstract class PlayerStateBase
{
    protected readonly PlayerController Controller;
    protected PlayerModel Model => Controller.Model!;
    protected float AnimationPlayTime;

    protected PlayerStateBase(PlayerController controller) => Controller = controller;

    public virtual void Enter() => AnimationPlayTime = 0f;
    public virtual void Exit() { }
    public virtual void Update()
    {
        // Gravity (ZZZ applies -9.8 every state).
        Controller.ApplyGravity();
        AnimationPlayTime += Time.DeltaTime;

        // Tag-team switch poll — legal from everything except the big-skill chain.
        if (Controller.Model!.CurrentState is not (PlayerState.BigSkillStart or PlayerState.BigSkill)
            && Controller.SwitchModel.Triggered())
            Controller.SwitchNextModel();
    }

    // --- Animation helpers (ZZZ PlayAnimation = CrossFadeInFixedTime) -----------------

    protected void PlayAnimation(string name, float transitionDuration = 0.25f, float normalizedOffset = 0f)
    {
        Animator? animator = Model.Animator;
        if (animator == null) return;
        animator.CrossFade(name, transitionDuration);
        if (animator.CurrentClip != null && animator.GetState(animator.CurrentClip) is { } playable)
        {
            if (normalizedOffset > 0f)
            {
                // Foot-phase offset: jump the freshly crossfaded clip forward (Unity's
                // CrossFadeInFixedTime normalizedTimeOffset).
                playable.Time = normalizedOffset * playable.Duration;
            }
            ArmCombatEvents(playable, animator.CurrentClip);
        }
    }

    /// <summary>Wires the attack clips' authored StartHit/StopHit events (imported from the
    /// FBX .meta sidecars into clip.Events) to the model's weapon hit windows. The engine's
    /// event pipeline fires inline callbacks, so this re-binds them per playback.</summary>
    private void ArmCombatEvents(AnimationClipPlayable playable, AnimationClip clip)
    {
        WeaponController? weapon = Model.GetComponentInChildren<WeaponController>();
        if (weapon == null || clip.Events.Count == 0) return;
        for (int i = 0; i < clip.Events.Count; i++)
        {
            AnimationClipEvent clipEvent = clip.Events[i];
            switch (clipEvent.FunctionName)
            {
                case "StartHit":
                    clip.Events[i] = clipEvent with { Callback = weapon.StartHit };
                    break;
                case "StopHit":
                    clip.Events[i] = clipEvent with { Callback = weapon.StopHit };
                    break;
            }
        }
    }

    protected bool IsAnimationEnd()
    {
        Animator? animator = Model.Animator;
        if (animator == null || animator.CurrentClip == null) return true; // stubbed: instantly done
        AnimatorStateInfo info = animator.GetCurrentAnimatorStateInfo();
        // ZZZ: normalizedTime >= 1 && !IsInTransition — the engine equivalent of "not in
        // transition" is the incoming state having reached full weight.
        bool fading = animator.GetState(animator.CurrentClip) is { } state && state.Weight < 0.99f;
        return info.normalizedTime >= 1f && !fading;
    }

    protected float NormalizedTime()
    {
        Animator? animator = Model.Animator;
        if (animator == null || animator.CurrentClip == null) return 1f; // stubbed: clips read as done
        return animator.GetCurrentAnimatorStateInfo().normalizedTime;
    }
}
