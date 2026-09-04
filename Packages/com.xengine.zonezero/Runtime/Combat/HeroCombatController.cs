// This file is part of the XEngine Game Engine
// Licensed under the MIT License. See the LICENSE file in the project root for details.

using XEngine.InputSystem;
using System;

using XEngine.Runtime;
using XEngine.Runtime.Resources;
using XEngine.Vector;
using XEngine.Vector;

namespace XEngine.Zonezero.Combat;

/// <summary>
/// Player-controlled battle hero (Anbi): camera-relative WASD locomotion plus keyboard skills —
/// <c>J</c> = single normal attack, <c>K</c> = 3-stage combo with buffered chaining (press once for
/// stage one, press again during a stage to queue the next, up to the finisher). Movement is locked
/// while swinging. Damage windows are normalized-time slices with forward-cone target tests; every
/// swing spawns its slash-arc VFX and every connect spawns sparks + flash on the dummy.
/// </summary>
[AddComponentMenu("Zonezero/Battle Hero Controller")]
public sealed class HeroCombatController : MonoBehaviour
{
    public float RunSpeed = 4.6f;
    public float TurnSpeedDeg = 540f;
    public float AttackRange = 2.0f;
    public float AttackHalfAngle = 65f;
    public float NormalAttackCooldown = 0.5f;

    private CharacterController? _cc;
    private Animator? _animator;
    private InputAction? _move;
    private InputAction? _skillJ;
    private InputAction? _skillK;

    /// <summary>Combo stage currently playing or queued start (1..3), 0 when not attacking.</summary>
    private int _comboStage;
    /// <summary>Next queued combo stage after the current clip ends (0 = none).</summary>
    private int _queuedStage;

    private bool _jAttacking;
    private float _jCooldown;
    private GameObject? _lockedTarget;
    private static readonly string[] ComboClips = { "Attack_Normal_1", "Attack_Normal_2", "Attack_Normal_3" };
    public bool IsBusy => _comboStage > 0 || _jAttacking;

    public override void Start()
    {
        _cc = GetComponent<CharacterController>() ?? AddComponent<CharacterController>();
        _animator = GetComponent<Animator>();
        PlayerInput? input = FindInput();
        if (input != null)
        {
            _move = input.FindAction("Move");
            _skillJ = input.FindAction("SkillJ");
            _skillK = input.FindAction("SkillK");
        }
        else
        {
            Debug.LogWarning("[Battle] No PlayerInput in scene — WASD/JK disabled.");
        }
    }

    public override void Update()
    {
        if (_cc == null || _animator == null) return;
        CombatMotor.ApplyGravity(_cc);
        if (_jCooldown > 0f) _jCooldown -= Time.DeltaTime;

        if (_skillJ?.Triggered() == true && !IsBusy && _jCooldown <= 0f)
        {
            StartAttack(ComboClips[0]);
            _jCooldown = NormalAttackCooldown;
        }
        else if (_skillK?.Triggered() == true)
        {
            // Buffering rule: first press starts stage 1; a press during any later stage queues the
            // next one so mashing K walks the chain 1→2→3, finishing at three.
            if (_comboStage == 0 && !_jAttacking)
                StartCombo(1);
            else if (_comboStage is >= 1 and < 3)
                _queuedStage = Math.Max(_queuedStage, _comboStage + 1);
        }

        if (_jAttacking)
        {
            TickDamageWindow();
            FaceLockedTarget();
            if (CombatMotor.ClipFinished(_animator))
                _jAttacking = false;
            return; // movement locked while swinging
        }

        if (_comboStage > 0)
        {
            TickDamageWindow();
            FaceLockedTarget();
            if (CombatMotor.ClipFinished(_animator))
            {
                int next = _queuedStage;
                _queuedStage = 0;
                if (next > _comboStage && next <= 3)
                    StartCombo(next);
                else
                    EndCombo();
            }
            return; // movement locked mid-combo
        }

        LocomotionTick();
    }

    // ── movement ──────────────────────────────────────────────────────────────

    private void LocomotionTick()
    {
        Float2 mv = _move?.ReadValue<Float2>() ?? default;
        BattleFollowCamera? rig = FindCameraRig();
        Float3 camForward = new(0f, 0f, 1f), camRight = new(1f, 0f, 0f);
        if (rig != null)
        {
            Float3 f = rig.Transform.Forward;
            Float2 ff = new(f.X, f.Z);
            if (Float2.LengthSquared(ff) > 1e-4f)
            {
                ff /= MathF.Sqrt(Float2.LengthSquared(ff));
                camForward = new Float3(ff.X, 0f, ff.Y);
                camRight = new Float3(-ff.Y, 0f, ff.X); // right = forward rotated -90° around Y
            }
        }
        // No rig found: identity axes keep the controls alive instead of dead-ending.

        Float3 wish = camForward * mv.Y + camRight * mv.X;
        bool moving = Float3.LengthSquared(wish) > 0.02f;
        // Asset streaming can leave the controller temporarily unable to enter Idle/Run. Keep
        // locomotion and its visual state atomic so input cannot slide a static-pose character.
        if (!CombatMotor.Play(_animator!, moving ? "Run" : "Idle", 0.15f)) return;
        if (moving)
        {
            wish /= Math.Max(MathF.Sqrt(Float3.LengthSquared(wish)), 1e-4f);
            CombatMotor.TurnToward(Transform, wish, TurnSpeedDeg, Time.DeltaTime);
            CombatMotor.MoveGrounded(_cc!, wish, RunSpeed);
        }
    }

    // ── attacks ───────────────────────────────────────────────────────────────

    private void StartAttack(string clip)
    {
        if (!CombatMotor.Play(_animator!, clip)) return;
        _jAttacking = true;
        AcquireTarget();
        CombatMotor.SpawnSwingVfx(GameObject!);
        _hitVfxDone = false;
    }

    private void StartCombo(int stage)
    {
        _comboStage = stage;
        _queuedStage = 0;
        if (!CombatMotor.Play(_animator!, ComboClips[Math.Min(stage, ComboClips.Length) - 1]))
        {
            EndCombo();
            return;
        }
        AcquireTarget();
        CombatMotor.SpawnSwingVfx(GameObject!);
        _hitVfxDone = false;
    }

    private void EndCombo() => _comboStage = 0;

    private void TickDamageWindow()
    {
        if (!CombatMotor.InHitWindow(_animator!) || _hitVfxDone) return;
        GameObject? victim = FindVictimInCone();
        if (victim == null) return;
        _hitVfxDone = true;
        CombatMotor.ApplyHit(GameObject!, victim);
    }

    private void AcquireTarget()
    {
        _lockedTarget = BattleTargets.FindNearest(Transform.Position, 12f);
    }

    private void FaceLockedTarget()
    {
        if (_lockedTarget == null || _lockedTarget.IsDisposed) return;
        CombatMotor.TurnToward(Transform,
            _lockedTarget.Transform.Position - Transform.Position, TurnSpeedDeg * 0.8f, Time.DeltaTime);
    }

    private GameObject? FindVictimInCone()
    {
        // Locked target first, then any enemy inside the cone.
        if (_lockedTarget != null && !_lockedTarget.IsDisposed &&
            CombatMotor.InAttackCone(GameObject!, _lockedTarget, AttackRange, AttackHalfAngle))
            return _lockedTarget;
        GameObject? nearest = BattleTargets.FindNearest(Transform.Position, AttackRange + 4f);
        if (nearest != null && CombatMotor.InAttackCone(GameObject!, nearest, AttackRange, AttackHalfAngle))
            return nearest;
        return null;
    }

    private bool _hitVfxDone;

    // ── scene lookups ─────────────────────────────────────────────────────────

    internal static PlayerInput? FindInput()
    {
        Scene? scene = Scene.Current;
        if (scene == null) return null;
        foreach (GameObject root in scene.RootObjects)
            if (root.GetComponent<PlayerInput>() != null && root.EnabledInHierarchy)
                return root.GetComponent<PlayerInput>();
        return null;
    }

    private BattleFollowCamera? FindCameraRig()
    {
        Scene? scene = Scene.Current;
        if (scene == null) return null;
        foreach (GameObject root in scene.RootObjects)
            if (root.GetComponent<BattleFollowCamera>() != null && root.EnabledInHierarchy)
                return root.GetComponent<BattleFollowCamera>();
        return null;
    }
}
