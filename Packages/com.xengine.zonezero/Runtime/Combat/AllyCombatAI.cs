// This file is part of the XEngine Game Engine
// Licensed under the MIT License. See the LICENSE file in the project root for details.

using System;

using XEngine.Runtime;
using XEngine.Vector;
using XEngine.Zonezero.Vfx;

namespace XEngine.Zonezero.Combat;

/// <summary>
/// Simple battle-demo ally brain. Each cycle patrols to a waypoint, idles, randomly selects one
/// active practice dummy, chases it, performs an exact three-hit normal combo, then chooses either
/// Attack_Normal_4 or the full BigSkill sequence before returning to patrol.
/// </summary>
[AddComponentMenu("Zonezero/Battle Ally AI")]
public sealed class AllyCombatAI : MonoBehaviour
{
    public Float3 PatrolPointA = new(-2.2f, 0f, -5.0f);
    public Float3 PatrolPointB = new(2.2f, 0f, -7.5f);

    /// <summary>Builder-facing waypoint pair.</summary>
    public readonly record struct PatrolRoute(Float3 PointA, Float3 PointB);

    public float WalkSpeed = 1.6f;
    public float RunSpeed = 4.2f;
    public float TurnSpeedDeg = 480f;
    public float AttackRange = 1.9f;
    public float AttackHalfAngle = 65f;
    public float IdleDurationMin = 0.55f;
    public float IdleDurationMax = 1.1f;
    public float RecoverDuration = 0.5f;

    private enum Phase
    {
        Patrol,
        Idle,
        AcquireTarget,
        Chase,
        Combo1,
        Combo2,
        Combo3,
        SkillAttack4,
        SkillStart,
        SkillBody,
        SkillEnd,
        Recover,
    }

    private CharacterController? _cc;
    private Animator? _animator;
    private Phase _phase;
    private bool _toB = true;
    private float _phaseDeadline;
    private GameObject? _target;
    private bool _damageActive;
    private bool _hitDoneForClip;
    private uint _randomState;

    /// <summary>Read-only acceptance telemetry; these properties are not used by the frame loop.</summary>
    public string AiPhase => _phase.ToString();
    public string TargetName => TargetAlive() ? _target!.Name : "-";
    public uint VisitedPhaseMask { get; private set; }
    public uint SelectedTargetMask { get; private set; }
    public int CompletedCombos { get; private set; }
    public int CompletedSkills { get; private set; }
    public int SuccessfulHits { get; private set; }

    public override void Start()
    {
        _cc = GetComponent<CharacterController>() ?? AddComponent<CharacterController>();
        _animator = GetComponent<Animator>();
        uint identifierHash = unchecked((uint)GameObject!.Identifier.GetHashCode());
        _randomState = identifierHash ^ 0x9E3779B9u;
        if (_randomState == 0)
            _randomState = 0xA341316Cu;
        EnterPhase(Phase.Patrol);
    }

    [HotPath]
    public override void Update()
    {
        if (_cc == null || _animator == null) return;

        CombatMotor.ApplyGravity(_cc);
        switch (_phase)
        {
            case Phase.Patrol:
                PatrolTick();
                break;
            case Phase.Idle:
                IdleTick();
                break;
            case Phase.AcquireTarget:
                AcquireTargetTick();
                break;
            case Phase.Chase:
                ChaseTick();
                break;
            case Phase.Combo1:
            case Phase.Combo2:
            case Phase.Combo3:
            case Phase.SkillAttack4:
            case Phase.SkillStart:
            case Phase.SkillBody:
            case Phase.SkillEnd:
                AttackTick();
                break;
            case Phase.Recover:
                RecoverTick();
                break;
        }
    }

    private void PatrolTick()
    {
        Float3 goal = _toB ? PatrolPointB : PatrolPointA;
        Float3 delta = goal - Transform.Position;
        float distanceSqr = delta.X * delta.X + delta.Z * delta.Z;
        if (distanceSqr < 0.09f)
        {
            _toB = !_toB;
            _phaseDeadline = Time.TimeSinceStartup + RandomRange(IdleDurationMin, IdleDurationMax);
            EnterPhase(Phase.Idle);
            CombatMotor.Play(_animator!, "Idle", 0.15f);
            return;
        }

        float inverseDistance = 1f / Math.Max(MathF.Sqrt(distanceSqr), 1e-4f);
        Float3 direction = new(delta.X * inverseDistance, 0f, delta.Z * inverseDistance);
        if (!CombatMotor.Play(_animator!, "Run", 0.18f)) return;
        CombatMotor.TurnToward(Transform, direction, TurnSpeedDeg, Time.DeltaTime);
        CombatMotor.MoveGrounded(_cc!, direction, WalkSpeed);
    }

    private void IdleTick()
    {
        CombatMotor.Play(_animator!, "Idle", 0.15f);
        if (Time.TimeSinceStartup >= _phaseDeadline)
            EnterPhase(Phase.AcquireTarget);
    }

    private void AcquireTargetTick()
    {
        _target = BattleTargets.FindRandom(ref _randomState);
        if (_target == null) return;

        RecordSelectedTarget(_target);
        EnterPhase(Phase.Chase);
    }

    private void ChaseTick()
    {
        if (!TargetAlive())
        {
            _target = null;
            EnterPhase(Phase.AcquireTarget);
            return;
        }

        Float3 delta = _target!.Transform.Position - Transform.Position;
        float distanceSqr = delta.X * delta.X + delta.Z * delta.Z;
        if (distanceSqr <= AttackRange * AttackRange)
        {
            CombatMotor.TurnToward(Transform, delta, TurnSpeedDeg, 1f);
            BeginCombo();
            return;
        }

        float inverseDistance = 1f / Math.Max(MathF.Sqrt(distanceSqr), 1e-4f);
        Float3 direction = new(delta.X * inverseDistance, 0f, delta.Z * inverseDistance);
        if (!CombatMotor.Play(_animator!, "Run", 0.15f)) return;
        CombatMotor.TurnToward(Transform, direction, TurnSpeedDeg, Time.DeltaTime);
        CombatMotor.MoveGrounded(_cc!, direction, RunSpeed);
    }

    private void BeginCombo()
    {
        if (!StartAttackPhase(Phase.Combo1, "Attack_Normal_1", damageActive: true))
            BeginRecover();
    }

    private void AttackTick()
    {
        if (!TargetAlive())
        {
            BeginRecover();
            return;
        }

        CombatMotor.TurnToward(Transform,
            _target!.Transform.Position - Transform.Position, TurnSpeedDeg * 0.6f, Time.DeltaTime);
        PollDamageWindow();
        if (!CombatMotor.ClipFinished(_animator!)) return;

        switch (_phase)
        {
            case Phase.Combo1:
                if (!StartAttackPhase(Phase.Combo2, "Attack_Normal_2", damageActive: true))
                    BeginRecover();
                break;
            case Phase.Combo2:
                if (!StartAttackPhase(Phase.Combo3, "Attack_Normal_3", damageActive: true))
                    BeginRecover();
                break;
            case Phase.Combo3:
                CompletedCombos++;
                BeginRandomSkill();
                break;
            case Phase.SkillAttack4:
                CompleteSkill();
                break;
            case Phase.SkillStart:
                if (!StartAttackPhase(Phase.SkillBody, "BigSkill", damageActive: true))
                    BeginRecover();
                else
                    ZonezeroVfx.BigSkillBurst(Transform.Position + new Float3(0f, 0.9f, 0f));
                break;
            case Phase.SkillBody:
                if (!StartAttackPhase(Phase.SkillEnd, "BigSkill_End", damageActive: false))
                    BeginRecover();
                break;
            case Phase.SkillEnd:
                CompleteSkill();
                break;
        }
    }

    private void BeginRandomSkill()
    {
        Animator animator = _animator!;
        bool hasAttack4 = animator.HasState("Attack_Normal_4");
        bool hasBigSkill = animator.HasState("BigSkill_Start")
                           && animator.HasState("BigSkill")
                           && animator.HasState("BigSkill_End");
        bool chooseAttack4 = hasAttack4 && (!hasBigSkill || (NextRandom() & 1u) == 0u);

        if (chooseAttack4)
        {
            if (!StartAttackPhase(Phase.SkillAttack4, "Attack_Normal_4", damageActive: true))
                BeginRecover();
            return;
        }

        if (hasBigSkill)
        {
            if (!StartAttackPhase(Phase.SkillStart, "BigSkill_Start", damageActive: false))
                BeginRecover();
            return;
        }

        if (hasAttack4 && StartAttackPhase(Phase.SkillAttack4, "Attack_Normal_4", damageActive: true))
            return;

        BeginRecover();
    }

    private bool StartAttackPhase(Phase phase, string stateName, bool damageActive)
    {
        if (!CombatMotor.Play(_animator!, stateName, 0.08f)) return false;

        _damageActive = damageActive;
        _hitDoneForClip = false;
        EnterPhase(phase);
        if (damageActive)
            CombatMotor.SpawnSwingVfx(GameObject!);
        return true;
    }

    private void PollDamageWindow()
    {
        if (!_damageActive || _hitDoneForClip || !CombatMotor.InHitWindow(_animator!)) return;
        if (!CombatMotor.InAttackCone(GameObject!, _target!, AttackRange + 0.4f, AttackHalfAngle)) return;

        _hitDoneForClip = true;
        SuccessfulHits++;
        CombatMotor.ApplyHit(GameObject!, _target!);
    }

    private void CompleteSkill()
    {
        CompletedSkills++;
        BeginRecover();
    }

    private void BeginRecover()
    {
        _target = null;
        _damageActive = false;
        _phaseDeadline = Time.TimeSinceStartup + Math.Max(RecoverDuration, 0f);
        EnterPhase(Phase.Recover);
        CombatMotor.Play(_animator!, "Idle", 0.12f);
    }

    private void RecoverTick()
    {
        CombatMotor.Play(_animator!, "Idle", 0.12f);
        if (Time.TimeSinceStartup >= _phaseDeadline)
            EnterPhase(Phase.Patrol);
    }

    private bool TargetAlive()
        => _target != null && !_target.IsDisposed && _target.EnabledInHierarchy;

    private void EnterPhase(Phase phase)
    {
        _phase = phase;
        VisitedPhaseMask |= 1u << (int)phase;
    }

    private void RecordSelectedTarget(GameObject target)
    {
        SelectedTargetMask |= target.Name switch
        {
            "Dummy_A" => 1u,
            "Dummy_B" => 2u,
            "Dummy_C" => 4u,
            _ => 0u,
        };
    }

    private float RandomRange(float minimum, float maximum)
    {
        float low = Math.Min(minimum, maximum);
        float high = Math.Max(minimum, maximum);
        float unit = (NextRandom() >> 8) * (1f / 16777216f);
        return low + (high - low) * unit;
    }

    private uint NextRandom()
    {
        uint value = _randomState;
        value ^= value << 13;
        value ^= value >> 17;
        value ^= value << 5;
        _randomState = value == 0 ? 0xA341316Cu : value;
        return _randomState;
    }
}
