// This file is part of the XEngine Game Engine
// Licensed under the MIT License. See the LICENSE file in the project root for details.

using System;

using XEngine.InputSystem;
using XEngine.Runtime;
using XEngine.Runtime.Resources;
using XEngine.Vector;

namespace XEngine.Zonezero.Combat;

/// <summary>
/// Player-controlled battle hero (Anbi): camera-relative WASD locomotion plus four keyboard
/// combat actions. J buffers a three-hit normal chain, K is a quick fourth-form skill, L is a
/// forward lunge skill, and I plays the full big-skill intro/body/outro sequence.
/// </summary>
[AddComponentMenu("Zonezero/Battle Hero Controller")]
public sealed class HeroCombatController : MonoBehaviour
{
    public float RunSpeed = 4.6f;
    public float TurnSpeedDeg = 540f;
    public float AttackRange = 2.0f;
    public float AttackHalfAngle = 65f;
    public float NormalAttackCooldown = 0.18f;
    public float SkillKCooldown = 1.25f;
    public float SkillLCooldown = 2.25f;
    public float SkillICooldown = 7f;
    public float SkillLungeSpeed = 7.5f;
    public float SkillLungeDuration = 0.38f;

    private enum CombatAction
    {
        None,
        Normal,
        SkillK,
        SkillL,
        SkillIStart,
        SkillIBody,
        SkillIEnd,
    }

    private static readonly string[] s_normalClips =
    {
        "Attack_Normal_1",
        "Attack_Normal_2",
        "Attack_Normal_3",
    };

    private CharacterController? _cc;
    private Animator? _animator;
    private BattleFollowCamera? _cameraRig;
    private InputAction? _move;
    private InputAction? _skillJ;
    private InputAction? _skillK;
    private InputAction? _skillL;
    private InputAction? _skillI;
    private CombatAction _action;
    private int _normalStage;
    private int _queuedNormalStage;
    private float _normalCooldown;
    private float _skillKCooldown;
    private float _skillLCooldown;
    private float _skillICooldown;
    private float _lungeRemaining;
    private Float3 _lungeDirection;
    private GameObject? _lockedTarget;
    private bool _hitDoneForClip;

    public bool IsBusy => _action != CombatAction.None;

    /// <summary>Acceptance-test telemetry; never queried by the per-frame path.</summary>
    public string ActiveAction => _action.ToString();

    public int NormalStage => _normalStage;

    public override void Start()
    {
        _cc = GetComponent<CharacterController>() ?? AddComponent<CharacterController>();
        _animator = GetComponent<Animator>();
        _cameraRig = FindCameraRig();

        PlayerInput? input = FindInput();
        if (input == null)
        {
            Debug.LogWarning("[Battle] No PlayerInput in scene — WASD/J/K/L/I disabled.");
            return;
        }

        _move = input.FindAction("Move");
        _skillJ = input.FindAction("SkillJ");
        _skillK = input.FindAction("SkillK");
        _skillL = input.FindAction("SkillL");
        _skillI = input.FindAction("SkillI");
    }

    [HotPath]
    public override void Update()
    {
        if (_cc == null || _animator == null) return;

        float dt = Time.DeltaTime;
        CombatMotor.ApplyGravity(_cc);
        TickCooldowns(dt);

        bool normalPressed = _skillJ?.Triggered() == true;
        bool skillKPressed = _skillK?.Triggered() == true;
        bool skillLPressed = _skillL?.Triggered() == true;
        bool skillIPressed = _skillI?.Triggered() == true;

        if (_action == CombatAction.Normal && normalPressed && _normalStage < s_normalClips.Length)
            _queuedNormalStage = _normalStage + 1;

        if (_action == CombatAction.None)
        {
            if (skillIPressed && _skillICooldown <= 0f)
                StartSkillI();
            else if (skillLPressed && _skillLCooldown <= 0f)
                StartSkillL();
            else if (skillKPressed && _skillKCooldown <= 0f)
                StartSkillK();
            else if (normalPressed && _normalCooldown <= 0f)
                StartNormal(1);
        }

        if (_action != CombatAction.None)
        {
            CombatTick(dt);
            return;
        }

        LocomotionTick();
    }

    [HotPath]
    private void TickCooldowns(float dt)
    {
        if (_normalCooldown > 0f) _normalCooldown -= dt;
        if (_skillKCooldown > 0f) _skillKCooldown -= dt;
        if (_skillLCooldown > 0f) _skillLCooldown -= dt;
        if (_skillICooldown > 0f) _skillICooldown -= dt;
    }

    [HotPath]
    private void LocomotionTick()
    {
        Float2 input = _move?.ReadValue<Float2>() ?? default;
        Float3 cameraForward = Float3.UnitZ;
        Float3 cameraRight = Float3.UnitX;

        if (_cameraRig == null || !_cameraRig.IsValid())
            _cameraRig = FindCameraRig();
        if (_cameraRig != null)
        {
            Float3 forward = _cameraRig.Transform.Forward;
            Float2 flatForward = new(forward.X, forward.Z);
            float forwardSqr = Float2.LengthSquared(flatForward);
            if (forwardSqr > 1e-4f)
            {
                flatForward /= MathF.Sqrt(forwardSqr);
                cameraForward = new Float3(flatForward.X, 0f, flatForward.Y);
                cameraRight = new Float3(-flatForward.Y, 0f, flatForward.X);
            }
        }

        Float3 wish = cameraForward * input.Y + cameraRight * input.X;
        float wishSqr = Float3.LengthSquared(wish);
        bool moving = wishSqr > 0.02f;

        // Asset streaming can leave the controller temporarily unable to enter Idle/Run. Keep
        // locomotion and visual state atomic so input cannot slide a static-pose character.
        if (!CombatMotor.Play(_animator!, moving ? "Run" : "Idle", 0.15f)) return;
        if (!moving) return;

        wish /= Math.Max(MathF.Sqrt(wishSqr), 1e-4f);
        CombatMotor.TurnToward(Transform, wish, TurnSpeedDeg, Time.DeltaTime);
        CombatMotor.MoveGrounded(_cc!, wish, RunSpeed);
    }

    [HotPath]
    private void CombatTick(float dt)
    {
        if (_action == CombatAction.SkillL && _lungeRemaining > 0f)
        {
            CombatMotor.MoveGrounded(_cc!, _lungeDirection, SkillLungeSpeed);
            _lungeRemaining -= dt;
        }

        if (_action is CombatAction.Normal or CombatAction.SkillK or CombatAction.SkillL or CombatAction.SkillIBody)
            TickDamageWindow();
        FaceLockedTarget();

        if (!CombatMotor.ClipFinished(_animator!)) return;

        switch (_action)
        {
            case CombatAction.Normal:
                if (_queuedNormalStage > _normalStage && _queuedNormalStage <= s_normalClips.Length)
                    StartNormal(_queuedNormalStage);
                else
                    EndAction();
                break;
            case CombatAction.SkillIStart:
                StartSkillIPhase(CombatAction.SkillIBody, "BigSkill");
                break;
            case CombatAction.SkillIBody:
                StartSkillIPhase(CombatAction.SkillIEnd, "BigSkill_End");
                break;
            default:
                EndAction();
                break;
        }
    }

    private void StartNormal(int stage)
    {
        int index = Math.Clamp(stage - 1, 0, s_normalClips.Length - 1);
        if (!StartClip(CombatAction.Normal, s_normalClips[index])) return;
        _normalStage = index + 1;
        _queuedNormalStage = 0;
        _normalCooldown = NormalAttackCooldown;
    }

    private void StartSkillK()
    {
        if (!StartClip(CombatAction.SkillK, "Attack_Normal_4")) return;
        _skillKCooldown = SkillKCooldown;
    }

    private void StartSkillL()
    {
        AcquireTarget();
        Float3 direction = Transform.Forward;
        if (_lockedTarget != null && !_lockedTarget.IsDisposed)
            direction = _lockedTarget.Transform.Position - Transform.Position;
        float flatSqr = direction.X * direction.X + direction.Z * direction.Z;
        if (flatSqr > 1e-4f)
            direction = new Float3(direction.X / MathF.Sqrt(flatSqr), 0f, direction.Z / MathF.Sqrt(flatSqr));
        else
            direction = Float3.UnitZ;

        if (!StartClip(CombatAction.SkillL, "Evade_Front", acquireTarget: false)) return;
        _lungeDirection = direction;
        _lungeRemaining = SkillLungeDuration;
        _skillLCooldown = SkillLCooldown;
        CombatMotor.TurnToward(Transform, direction, TurnSpeedDeg, 1f);
    }

    private void StartSkillI()
    {
        if (!StartClip(CombatAction.SkillIStart, "BigSkill_Start")) return;
        _skillICooldown = SkillICooldown;
    }

    private void StartSkillIPhase(CombatAction phase, string stateName)
    {
        if (!StartClip(phase, stateName, acquireTarget: false))
        {
            EndAction();
            return;
        }

        if (phase == CombatAction.SkillIBody)
            XEngine.Zonezero.Vfx.ZonezeroVfx.BigSkillBurst(Transform.Position + new Float3(0f, 0.9f, 0f));
    }

    private bool StartClip(CombatAction action, string stateName, bool acquireTarget = true)
    {
        if (!CombatMotor.Play(_animator!, stateName)) return false;
        _action = action;
        if (acquireTarget) AcquireTarget();
        CombatMotor.SpawnSwingVfx(GameObject!);
        _hitDoneForClip = false;
        return true;
    }

    private void EndAction()
    {
        _action = CombatAction.None;
        _normalStage = 0;
        _queuedNormalStage = 0;
        _lungeRemaining = 0f;
        _lockedTarget = null;
    }

    [HotPath]
    private void TickDamageWindow()
    {
        if (_hitDoneForClip || !CombatMotor.InHitWindow(_animator!)) return;
        GameObject? victim = FindVictimInCone();
        if (victim == null) return;
        _hitDoneForClip = true;
        CombatMotor.ApplyHit(GameObject!, victim);
    }

    [HotPath]
    private void FaceLockedTarget()
    {
        if (_lockedTarget == null || _lockedTarget.IsDisposed) return;
        CombatMotor.TurnToward(Transform,
            _lockedTarget.Transform.Position - Transform.Position, TurnSpeedDeg * 0.8f, Time.DeltaTime);
    }

    private void AcquireTarget()
    {
        _lockedTarget = BattleTargets.FindNearest(Transform.Position, 12f);
    }

    [HotPath]
    private GameObject? FindVictimInCone()
    {
        if (_lockedTarget != null && !_lockedTarget.IsDisposed &&
            CombatMotor.InAttackCone(GameObject!, _lockedTarget, AttackRange, AttackHalfAngle))
            return _lockedTarget;

        GameObject? nearest = BattleTargets.FindNearest(Transform.Position, AttackRange + 4f);
        return nearest != null && CombatMotor.InAttackCone(GameObject!, nearest, AttackRange, AttackHalfAngle)
            ? nearest
            : null;
    }

    internal static PlayerInput? FindInput()
    {
        Scene? scene = Scene.Current;
        if (scene == null) return null;
        foreach (GameObject root in scene.RootObjects)
        {
            PlayerInput? input = root.GetComponent<PlayerInput>();
            if (input != null && root.EnabledInHierarchy)
                return input;
        }
        return null;
    }

    private static BattleFollowCamera? FindCameraRig()
    {
        Scene? scene = Scene.Current;
        if (scene == null) return null;
        foreach (GameObject root in scene.RootObjects)
        {
            BattleFollowCamera? rig = root.GetComponent<BattleFollowCamera>();
            if (rig != null && root.EnabledInHierarchy)
                return rig;
        }
        return null;
    }
}
