// This file is part of the XEngine Game Engine
// Licensed under the MIT License. See the LICENSE file in the project root for details.

using System;

using XEngine.Runtime;
using XEngine.Zonezero.Vfx;
using XEngine.Vector;

namespace XEngine.Zonezero.Combat;

/// <summary>
/// Same-faction battle AI: patrols between two points on foot; when a practice dummy is inside
/// aggro range it runs in and plays its attack program — normal attack, then the 3-stage combo,
/// then the big-skill trio (clips absent from a character are skipped gracefully) — then walks
/// back to patrolling. The cycle repeats forever.
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
    public float AggroRange = 9f;
    public float AttackRange = 1.9f;
    public float AttackHalfAngle = 65f;

    private enum Phase { Patrol, Chase, Program }
    private enum ProgramStep { Normal, Combo1, Combo2, Combo3, SkillStart, SkillBody, SkillEnd }

    private CharacterController? _cc;
    private Animator? _animator;
    private Phase _phase;
    private bool _toB = true;
    private float _pauseUntil;
    private GameObject? _target;
    private float _targetRefreshAt;
    private ProgramStep _step;
    private int _cycleIndex; // advances across attack programs so the sequence keeps rotating

    /// <summary>Debug/telemetry surface for harnesses.</summary>
    public string AiPhase => _phase.ToString();
    public string AiStep => _phase == Phase.Program ? _step.ToString() : "-";
    public bool DbgInWindow { get; private set; }
    public bool DbgCone { get; private set; }
    public float DbgDist { get; private set; }
    public int DbgPollCount { get; private set; }
    public int DbgUpdateCount { get; private set; }
    public string DbgLastError { get; private set; } = "-";
    public string DbgTargetState { get; private set; } = "init";

    public override void Start()
    {
        _cc = GetComponent<CharacterController>() ?? AddComponent<CharacterController>();
        _animator = GetComponent<Animator>();
    }

    public override void Update()
    {
        DbgUpdateCount++;
        if (DbgUpdateCount == 1) Debug.Log("[Battle] ally tick v2 active");
        SwingHitPoll();
        if (_cc == null || _animator == null) return;
        CombatMotor.ApplyGravity(_cc);

        try
        {
            switch (_phase)
            {
                case Phase.Patrol: PatrolTick(); break;
                case Phase.Chase: ChaseTick(); break;
                case Phase.Program: ProgramTick(); break;
            }
        }
        catch (Exception ex)
        {
            DbgLastError = ex.GetType().Name + ": " + ex.Message;
        }
    }

    // ── patrol / chase ────────────────────────────────────────────────────────

    private void PatrolTick()
    {
        TryPickTarget();

        if (_pauseUntil > Time.TimeSinceStartup) { CombatMotor.Play(_animator, "Idle", 0.15f); return; }

        Float3 goal = _toB ? PatrolPointB : PatrolPointA;
        Float3 delta = goal - Transform.Position;
        Float2 flat = new(delta.X, delta.Z);
        if (Float2.LengthSquared(flat) < 0.09f)
        {
            _toB = !_toB;                       // reached waypoint → swap and linger
            _pauseUntil = Time.TimeSinceStartup + 0.8f;
            CombatMotor.Play(_animator, "Idle", 0.15f);
            return;
        }

        Float3 dir = new(flat.X / MathF.Sqrt(Float2.LengthSquared(flat)), 0f, flat.Y / MathF.Sqrt(Float2.LengthSquared(flat)));
        // These production controllers currently expose Idle/Run but no Walk. Prefer Walk when a
        // future controller supplies it, otherwise animate patrol with Run at the lower WalkSpeed.
        // If neither state is available yet, clips are still streaming: keep body and pose atomic
        // instead of visibly sliding the actor in its static pose.
        Animator animator = _animator!;
        string locomotionState = animator.HasState("Walk") ? "Walk" : "Run";
        if (!CombatMotor.Play(animator, locomotionState, 0.18f)) return;
        CombatMotor.TurnToward(Transform, dir, TurnSpeedDeg, Time.DeltaTime);
        CombatMotor.MoveGrounded(_cc!, dir, WalkSpeed);

        // Spawned far from a dummy still triggers aggression once in range.
        if (_target != null && Float3.LengthSquared(_target.Transform.Position - Transform.Position) < AggroRange * AggroRange)
            BeginProgram();
    }

    private void ChaseTick()
    {
        if (!TargetAlive())
        {
            _phase = Phase.Patrol;
            return;
        }

        Float3 delta = _target!.Transform.Position - Transform.Position;
        float dist = MathF.Sqrt(delta.X * delta.X + delta.Z * delta.Z);
        if (dist <= AttackRange)
        {
            BeginProgram();
            return;
        }

        Float3 dir = delta / Math.Max(dist, 1e-4f);
        dir = new Float3(dir.X, 0f, dir.Z);
        if (!CombatMotor.Play(_animator!, "Run", 0.15f)) return;
        CombatMotor.TurnToward(Transform, dir, TurnSpeedDeg, Time.DeltaTime);
        CombatMotor.MoveGrounded(_cc!, dir, RunSpeed);
    }

    // ── attack program ────────────────────────────────────────────────────────

    private void BeginProgram()
    {
        _phase = Phase.Program;
        SetFirstStepOfCycle();

        if (_target != null && _target.IsValid())
            CombatMotor.TurnToward(Transform, _target.Transform.Position - Transform.Position, TurnSpeedDeg, 1f);
        EnterStep();
    }

    private void SetFirstStepOfCycle()
    {
        // Rotation through [Normal, Combo(3), Skill], so every program exercises all tools.
        _cycleIndex %= 3;
        _step = _cycleIndex switch
        {
            0 => ProgramStep.Normal,
            1 => ProgramStep.Combo1,
            _ => ProgramStep.SkillStart,
        };
    }

    private void ProgramTick()
    {
        if (!TargetAlive() && _step == ProgramStep.Normal)
        {
            EndProgram();
            return;
        }

        FaceTargetDuringSwing();

        if (!CombatMotor.ClipFinished(_animator)) return;

        switch (_step)
        {
            case ProgramStep.Normal:
                EndProgram();
                break;
            case ProgramStep.Combo1:
                Advance(ProgramStep.Combo2);
                break;
            case ProgramStep.Combo2:
                Advance(ProgramStep.Combo3);
                break;
            case ProgramStep.Combo3:
                EndProgram();
                break;
            case ProgramStep.SkillStart:
                Advance(ProgramStep.SkillBody);
                break;
            case ProgramStep.SkillBody:
                Advance(ProgramStep.SkillEnd);
                break;
            case ProgramStep.SkillEnd:
                EndProgram();
                break;
        }
    }

    private void Advance(ProgramStep next)
    {
        _step = next;
        EnterStep();
    }

    private void EnterStep()
    {
        switch (_step)
        {
            case ProgramStep.Normal:
                PlayCombat("Attack_Normal_1", oncePerStageVfx: true);
                break;
            case ProgramStep.Combo1: PlayCombat("Attack_Normal_1"); break;
            case ProgramStep.Combo2: PlayCombat("Attack_Normal_2"); break;
            case ProgramStep.Combo3: PlayCombat("Attack_Normal_3"); break;
            case ProgramStep.SkillStart: PlayCombat("BigSkill_Start"); break;
            case ProgramStep.SkillBody:
                PlayCombat("BigSkill");
                ZonezeroVfx.BigSkillBurst(Transform.Position + new Float3(0f, 0.9f, 0f));
                break;
            case ProgramStep.SkillEnd: PlayCombat("BigSkill_End"); break;
        }
    }

    private void PlayCombat(string clip, bool oncePerStageVfx = false)
    {
        if (!CombatMotor.Play(_animator!, clip, 0.08f))
        {
            // Clip missing for this character (no BigSkill, shorter combo): skip to program end.
            EndProgram();
            return;
        }
        CombatMotor.SpawnSwingVfx(GameObject!);
        _hitDoneForClip = false;
    }

    private void FaceTargetDuringSwing()
    {
        if (!TargetAlive()) return;
        if (_target!.Transform.Position is var p)
            CombatMotor.TurnToward(Transform, p - Transform.Position, TurnSpeedDeg * 0.6f, Time.DeltaTime);
    }

    /// <summary>Damage window polled every frame of the active clip (matches hero behavior).</summary>
    private void SwingHitPoll()
    {
        if (_phase != Phase.Program || !TargetAlive() || _hitDoneForClip) return;
        if (!CombatMotor.InHitWindow(_animator!)) return;
        if (!CombatMotor.InAttackCone(GameObject!, _target!, AttackRange + 0.4f, AttackHalfAngle)) return;
        _hitDoneForClip = true;
        CombatMotor.ApplyHit(GameObject!, _target!);
    }
    private bool _hitDoneForClip;

    private void EndProgram()
    {
        _cycleIndex++;
        _phase = Phase.Patrol;
        _pauseUntil = Time.TimeSinceStartup + 0.5f;
        CombatMotor.Play(_animator, "Idle", 0.12f);
    }

    private bool TargetAlive()
    {
        RefreshTarget();
        return _target != null && _target.IsValid() && _target.EnabledInHierarchy;
    }

    private void TryPickTarget()
    {
        RefreshTarget();
    }

    private void RefreshTarget()
    {
        if (Time.TimeSinceStartup < _targetRefreshAt) return;
        _targetRefreshAt = Time.TimeSinceStartup + 0.4f;
        if (_target != null && !_target.IsDisposed && _target.EnabledInHierarchy) return;
        _target = BattleTargets.FindNearest(Transform.Position, AggroRange + 4f);
    }
}
