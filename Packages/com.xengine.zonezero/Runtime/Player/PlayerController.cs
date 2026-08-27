// This file is part of the XEngine Game Engine
// Licensed under the MIT License. See the LICENSE file in the project root for details.

using System;
using System.Collections.Generic;

using XEngine.Echo;

using XEngine.Cinemachine;
using XEngine.InputSystem;
using XEngine.Runtime;
using XEngine.Runtime.Resources;
using XEngine.Vector;

namespace XEngine.Zonezero;

/// <summary>
/// Port of the ZZZ demo's PlayerController + StateMachine (zonezero M7): drives the
/// 3-character tag team from the imported .inputactions asset (Move/Evade/BigSkill/
/// SwitchModel/Fire) through a lazily-cached typed FSM. Locomotion is root motion —
/// the engine Animator's ApplyRootMotion moves the model, exactly like the reference.
/// </summary>
[AddComponentMenu("Zonezero/Player Controller")]
public sealed class PlayerController : MonoBehaviour
{
    /// <summary>Team member roots (Anbi/Corin/Nostradamus prefabs), index 0 starts active.</summary>
    [SerializeField] private List<GameObject> _team = new();

    private readonly Dictionary<Type, PlayerStateBase> _states = new();
    private PlayerStateBase? _current;
    private PlayerInput? _playerInput;
    private Camera? _mainCamera;
    private bool _animatorRuntimeWasReady;

    public float RotationSpeed = 8f;
    /// <summary>Locomotion dash speeds (m/s). The ripped ZZZ clips are in-place cycles (no baked
    /// root motion — the reference moved via CharacterController), so Run/Evade translation is
    /// code-driven.</summary>
    public float RunSpeed = 4f;
    public float EvadeSpeed = 7f;
    public Float2 InputMove;
    public float EvadeTimer = 1f;

    /// <summary>The front/back variant of the active evade (read by the recovery state).</summary>
    public PlayerState LastEvadeVariant = PlayerState.Evade_Back;

    /// <summary>The state the FSM was in before the current one (evade clip choice).</summary>
    public PlayerState PreviousState = PlayerState.Idle;

    public PlayerModel? Model { get; private set; }
    public int CurrentModelIndex { get; private set; }

    // --- Input actions (resolved from the sibling PlayerInput component) ----------------
    private InputAction? _move;
    private InputAction? _evade;
    private InputAction? _bigSkill;
    private InputAction? _switchModel;
    private InputAction? _fire;

    public InputAction? Move => _move;
    public InputAction? Evade => _evade;
    public InputAction? BigSkill => _bigSkill;
    public InputAction? SwitchModel => _switchModel;
    public InputAction? Fire => _fire;

    public IReadOnlyList<GameObject> Team => _team;

    public override void OnAddedToScene()
    {
        _playerInput = GetComponent<PlayerInput>();
        if (_playerInput != null)
        {
            _move = _playerInput.FindAction("Move");
            _evade = _playerInput.FindAction("Evade");
            _bigSkill = _playerInput.FindAction("BigSkill");
            _switchModel = _playerInput.FindAction("SwitchModel");
            _fire = _playerInput.FindAction("Fire");
        }

        foreach (GameObject member in _team)
        {
            member.Enabled = false;
            PlayerModel? model = member.GetComponent<PlayerModel>() ?? member.AddComponent<PlayerModel>();
            model.OnEnable();
        }
        if (_team.Count > 0)
            Activate(0);

        SwitchState(PlayerState.Idle);
    }

    public override void Start()
    {
        // The reference locks the cursor at Start: gameplay camera is driven by pointer deltas and
        // an unlocked cursor breaks the combat feel immediately.
        if (Application.IsPlaying && Input.CursorLocked == false)
            XEngine.Runtime.Input.LockCursor();

        // CrossFade no-ops before the animator graph ticks (edit-mode scene builds); re-enter
        // the current state when play begins so the opening clip actually starts.
        if (Model != null && _current != null)
            SwitchState(Model.CurrentState);
    }

    public override void Update()
    {
        // Refresh the input vector every frame (normalized, like the reference).
        InputMove = _move != null ? _move.ReadValue<Float2>() : Float2.Zero;
        if (Float3.Length(new Float3(InputMove.X, 0f, InputMove.Y)) > 1f)
            InputMove = Float2.Normalize(InputMove);

        if (EvadeTimer < 1f)
        {
            EvadeTimer += Time.DeltaTime;
            if (EvadeTimer > 1f) EvadeTimer = 1f;
        }

        // The animator's controller runtime can bind a few frames after the FSM's first
        // CrossFade (scene members start disabled; the animator initializes on Activate).
        // Once it is live, re-enter the current state once so the opening clip actually plays.
        bool runtimeReady = Model?.Animator?.Runtime != null;
        if (runtimeReady && !_animatorRuntimeWasReady)
            _current?.Enter();
        _animatorRuntimeWasReady = runtimeReady;

        _current?.Update();
    }

    /// <summary>Gravity — the only code-driven translation besides the switch teleport.</summary>
    public void ApplyGravity()
    {
        if (Model?.CharacterController != null)
            Model.CharacterController.Move(new Float3(0f, -9.8f * Time.DeltaTime, 0f));
    }

    /// <summary>The highest-depth enabled camera (camera-relative movement).</summary>
    public Camera? MainCamera
    {
        get
        {
            if (_mainCamera != null && _mainCamera.IsValid() && _mainCamera.GameObject!.EnabledInHierarchy)
                return _mainCamera;
            _mainCamera = null;
            Scene? scene = Scene.Current;
            if (scene == null) return null;
            foreach (GameObject root in scene.RootObjects)
            {
                Camera? camera = root.GetComponent<Camera>();
                if (camera == null || !root.EnabledInHierarchy) continue;
                if (_mainCamera == null || camera.Depth > _mainCamera.Depth)
                    _mainCamera = camera;
            }
            return _mainCamera;
        }
    }

    // --- FSM --------------------------------------------------------------------------------

    public void SwitchState(PlayerState state)
    {
        // Evade gating: 1 s cooldown.
        if (state is PlayerState.Evade_Front or PlayerState.Evade_Back)
        {
            if (EvadeTimer != 1f) return;
            EvadeTimer -= 1f;
            LastEvadeVariant = state;
        }

        PlayerStateBase? next = state switch
        {
            PlayerState.Idle => GetState<IdleState>(true),
            PlayerState.Run => GetState<RunState>(true),
            PlayerState.RunEnd => GetState<RunEndState>(),
            PlayerState.TurnBack => GetState<TurnBackState>(),
            PlayerState.Evade_Front => GetState<EvadeState>(),
            PlayerState.Evade_Back => GetState<EvadeState>(),
            PlayerState.EvadeEnd => GetState<EvadeEndState>(),
            PlayerState.Evade_Front_End => GetState<EvadeEndState>(),
            PlayerState.Evade_Back_End => GetState<EvadeEndState>(),
            PlayerState.SwitchInNormal => GetState<SwitchInNormalState>(false),
            PlayerState.NormalAttack => GetState<NormalAttackState>(true),
            PlayerState.NormalAttackEnd => GetState<NormalAttackEndState>(),
            PlayerState.BigSkillStart => GetState<BigSkillStartState>(),
            PlayerState.BigSkill => GetState<BigSkillState>(),
            PlayerState.BigSkillEnd => GetState<BigSkillEndState>(),
            _ => null,
        };
        if (next == null) return;

        if (_current != null)
            PreviousState = Model!.CurrentState;
        _current?.Exit();
        _current = next;
        Model!.CurrentState = state;
        next.Enter();
    }

    /// <summary>Lazily create + cache states by type (the ZZZ StateMachine dictionary).</summary>
    private PlayerStateBase GetState<T>(bool forceRefresh = false) where T : PlayerStateBase
    {
        if (_states.TryGetValue(typeof(T), out PlayerStateBase? cached) && !forceRefresh && _current == cached)
            return cached; // same-state re-entry needs forceRefresh (Idle/Run)
        if (!_states.TryGetValue(typeof(T), out cached))
        {
            cached = (PlayerStateBase)Activator.CreateInstance(typeof(T), this)!;
            _states[typeof(T)] = cached;
        }
        return cached;
    }

    /// <summary>Tag-team swap: clear the FSM, play the outgoing switch animation, activate
    /// the next character at the behind-right spawn.</summary>
    public void SwitchNextModel()
    {
        if (_team.Count == 0) return;
        _current?.Exit();
        _states.Clear();
        _current = null;

        PlayerModel? previous = Model;
        Float3 position = previous?.Transform.Position ?? Float3.Zero;
        Quaternion rotation = previous?.Transform.Rotation ?? Quaternion.Identity;

        CurrentModelIndex = (CurrentModelIndex + 1) % _team.Count;
        Activate(CurrentModelIndex);
        Model!.Enter(position, rotation);
        SwitchState(PlayerState.SwitchInNormal);
    }

    /// <summary>Test/sample hook: add a team member at runtime.</summary>
    public void TeamAddForTesting(GameObject member) => _team.Add(member);

    /// <summary>Enemies the auto-lock searches (tag names, ZZZ enemyTagList).</summary>
    public string[] EnemyTags = { "Enemy" };

    /// <summary>Big-skill shot vcams (created by the scene build; disabled by default).</summary>
    public GameObject? BigSkillStartShot;
    public GameObject? BigSkillShot;

    /// <summary>Nearest enemy across every enemy tag (the ZZZ auto-lock).</summary>
    public GameObject? FindNearestEnemy()
    {
        GameObject? nearest = null;
        float minDistance = float.MaxValue;
        Model ??= _team.Count > 0 ? _team[CurrentModelIndex].GetComponent<PlayerModel>() : null;
        if (Model == null) return null;
        Scene? scene = Scene.Current;
        if (scene == null) return null;
        foreach (string tag in EnemyTags)
        {
            foreach (GameObject root in scene.RootObjects)
            {
                if (!root.CompareTag(tag)) continue;
                float distance = Float3.Length(root.Transform.Position - Model.Transform.Position);
                if (distance < minDistance)
                {
                    nearest = root;
                    minDistance = distance;
                }
            }
        }
        return nearest;
    }

    /// <summary>BigSkill_Start camera cut: Cut blend, FreeLook off, intro shot vcam on.</summary>
    public void BeginBigSkillCamera()
    {
        var brain = FindBrain();
        if (brain != null)
            brain.DefaultBlend = BlendDefinition.Cut();
        SetFreeLookActive(false);
        if (BigSkillStartShot != null) BigSkillStartShot.Enabled = true;
    }

    /// <summary>BigSkill body: intro shot off, finishing shot on (same cut).</summary>
    public void SwapToBigSkillShot()
    {
        if (BigSkillStartShot != null) BigSkillStartShot.Enabled = false;
        if (BigSkillShot != null) BigSkillShot.Enabled = true;
    }

    /// <summary>BigSkill_End: shots off, FreeLook back with EaseInOut(1 s), re-centered.</summary>
    public void EndBigSkillCamera()
    {
        if (BigSkillStartShot != null) BigSkillStartShot.Enabled = false;
        if (BigSkillShot != null) BigSkillShot.Enabled = false;
        var brain = FindBrain();
        if (brain != null)
            brain.DefaultBlend = BlendDefinition.EaseInOut(1f);
        SetFreeLookActive(true);
        ResetFreeLookCamera();
    }

    private CinemachineBrain? FindBrain()
    {
        Scene? scene = Scene.Current;
        if (scene == null) return null;
        foreach (GameObject root in scene.RootObjects)
        {
            Camera? camera = root.GetComponent<Camera>();
            if (camera == null) continue;
            var brain = root.GetComponent<CinemachineBrain>();
            if (brain != null) return brain;
        }
        return null;
    }

    private static void SetFreeLookActive(bool active)
    {
        Scene? scene = Scene.Current;
        if (scene == null) return;
        foreach (GameObject root in scene.RootObjects)
            if (root.Name == "FreeLook Camera")
                root.Enabled = active;
    }

    /// <summary>CameraManager.ResetFreeLookCamera: Y back to 0.5, X to the model's yaw.</summary>
    public void ResetFreeLookCamera()
    {
        Scene? scene = Scene.Current;
        if (scene == null || Model == null) return;
        foreach (GameObject root in scene.RootObjects)
        {
            if (root.Name != "FreeLook Camera") continue;
            var freeLook = root.GetComponent<CinemachineFreeLook>();
            if (freeLook == null) continue;
            freeLook.YAxis.Value = 0.5f;
            freeLook.XAxis.Value = Quaternion.ToEuler(Model.Transform.Rotation).Y;
        }
    }

    private void Activate(int index)
    {
        foreach (GameObject member in _team)
            member.Enabled = false;
        GameObject go = _team[index];
        go.Enabled = true;
        Model = go.GetComponent<PlayerModel>() ?? go.AddComponent<PlayerModel>();
    }
}
