// This file is part of the XEngine Game Engine
// Licensed under the MIT License. See the LICENSE file in the project root for details.

using System;
using System.Collections.Generic;

using XEngine.Runtime;
using XEngine.Runtime.Resources;
using XEngine.Runtime.UI;
using XEngine.Vector;
using XEngine.Zonezero.Combat;

namespace XEngine.Zonezero.UI;

/// <summary>
/// Battle HUD: left-half virtual joystick + right-bottom skill cluster (normal attack J,
/// skills K/L/I), mirroring the MagicCreator DlgBattle layout. Built procedurally onto a
/// GameCanvas; input reaches <see cref="HeroCombatController"/> through
/// <see cref="BattleTouchInputBridge"/> (InputInjector), so the physical keyboard keeps
/// working in parallel. Cooldown rings read the combat controller's slot cooldowns each frame.
/// Created lazily by gameplay (<see cref="EnsureCreated"/>) when play mode starts.
/// </summary>
[AddComponentMenu("Zonezero/Battle HUD")]
public sealed class BattleHUD : MonoBehaviour
{
    private static BattleHUD? _instance;
    private readonly bool _diagnostics = Environment.GetEnvironmentVariable("XENGINE_BATTLE_DIAGNOSTICS") == "1";
    private long _diagnosticFrame;
    private bool _frameCaptureRequested, _frameCaptureSaved;

    private void CaptureRenderDiagnostics()
    {
        if (_frameCaptureSaved || Environment.GetEnvironmentVariable("XENGINE_BATTLE_FRAME_CAPTURE") != "1") return;
        if (!_frameCaptureRequested && Time.FrameCount > 300)
        {
            XEngine.Runtime.Rendering.FrameDebugger.CaptureScope = XEngine.Runtime.Rendering.FrameDebuggerCaptureScope.FullFrame;
            XEngine.Runtime.Rendering.FrameDebugger.Enable();
            Runtime.Application.IsPaused = false;
            _frameCaptureRequested = true;
        }
        else if (_frameCaptureRequested && XEngine.Runtime.Rendering.FrameDebugger.LastCapture is { } capture)
        {
            var rows = new List<object>();
            foreach (var op in capture.Ops)
                rows.Add(new { op.Label, op.CommandBufferName, target = op.RenderTarget?.Name,
                    clear = op.ClearFlags.ToString(), op.ShaderLabel, op.UniformName, op.UniformFloat, op.TextureLabel });
            System.IO.File.WriteAllText(System.IO.Path.Combine(Runtime.Application.DataPath, "battle-frame.json"),
                System.Text.Json.JsonSerializer.Serialize(rows));
            XEngine.Runtime.Rendering.FrameDebugger.Disable();
            _frameCaptureSaved = true;
        }
    }

    // HUD texture files under Assets/ZZZ/Arts/UI/HUD. Resolved by the project-relative ASSET
    // PATH at runtime (via
    // the backend's GetEntry(path) reflection hook) — never by hardcoded GUIDs: each machine
    // that imports the textures fresh assigns its own GUIDs, so baked-in values white-out.
    // EditorAssetBackend paths are relative to the project's Assets root.  Keeping the
    // leading `Assets/` here makes GetEntry miss every texture (the backend does not
    // normalize that prefix), so the procedural fallback silently builds white quads.
    private const string HudAssetsRoot = "ZZZ/Arts/UI/HUD/";
    private const string JoystickBaseFile = "joystick_base.png";
    private const string JoystickThumbFile = "joystick_thumb.png";
    private const string AttackFile = "btn_attack.png";
    private const string SkillKFile = "btn_skill_k.png";
    private const string SkillLFile = "btn_skill_l.png";
    private const string SkillIFile = "btn_skill_i.png";
    private const string RollFile = "btn_roll.png";
    private const string CdMaskFile = "cd_mask.png";

    // path → sprite AssetRef, resolved once per session via the editor backend's asset entry
    // (path → texture entry → its Sprite sub-asset GUID — enumerated directly, no derivation
    // formula, so it works whatever GUIDs the local importer assigned).
    private static readonly Dictionary<string, AssetRef<Sprite>?> s_spriteByPath = new();

    /// <summary>Returns the project-relative path used by AssetBackend.GetEntry.</summary>
    internal static string ResolveHudAssetPath(string fileName)
        => HudAssetsRoot + fileName.TrimStart('/', '\\');

    private static AssetRef<Sprite>? SpriteRef(string fileName)
    {
        string path = ResolveHudAssetPath(fileName);
        if (s_spriteByPath.TryGetValue(path, out AssetRef<Sprite>? cached) &&
            cached is { } cachedRef && cachedRef.Res is { } cachedSprite)
        {
            cachedSprite.Texture.EnsureLoaded();
            if (Runtime.Resources.Scene.Current is { } scene)
                cachedSprite.Texture.LockToScene(scene);
            return cachedRef;
        }

        AssetRef<Sprite>? resolved = null;
        if (XEngine.Zonezero.Config.BattleAssetCatalog.Load()?.LoadHudSprite(fileName) is { } packagedSprite)
        {
            resolved = new AssetRef<Sprite>(packagedSprite);
            s_spriteByPath[path] = resolved;
            return resolved;
        }
        try
        {
            var backend = AssetDatabase.Current;
            var getEntry = backend?.GetType().GetMethod("GetEntry", new[] { typeof(string) });
            if (getEntry?.Invoke(backend, new object[] { path }) is { } entry)
            {
                var subAssets = entry.GetType().GetField("SubAssets")?.GetValue(entry) as System.Array;
                if (subAssets != null)
                {
                    foreach (object? sub in subAssets)
                    {
                        if (sub == null) continue;
                        var typeName = sub.GetType().GetField("TypeName")?.GetValue(sub) as string;
                        if (typeName == null || !typeName.Contains("Sprite")) continue;
                        if (sub.GetType().GetField("Guid")?.GetValue(sub) is Guid spriteGuid)
                        {
                            // Blocking one-time load: Image bakes its mesh when the Sprite property
                            // is assigned; the async `.Res` path returns null until streaming
                            // completes and the bake would stick white (white-square bug).
                            var reference = new AssetRef<Sprite>(spriteGuid);
                            reference.EnsureLoaded();

                            // Image bakes its mesh and material independently.  Loading the
                            // Sprite alone is not enough when async asset loading is enabled:
                            // Sprite.Texture.Res can still be null on the first bake, leaving a
                            // permanent white quad until another dirty pass happens.  Resolve the
                            // source texture before assigning the reference so both UVs and the
                            // material texture are valid on the first canvas rebuild.
                            Sprite? sprite = reference.ResWeak;
                            sprite?.Texture.EnsureLoaded();
                            resolved = reference;
                            break;
                        }
                    }
                }
            }
        }
        catch { /* best-effort: unresolved paths fall back to plain-colored UI */ }
        // A failed lookup is commonly a transient import race.  Do not memoize null and make a
        // later first-frame repair impossible; successful references are stable for the session.
        if (resolved is { } ready)
            s_spriteByPath[path] = ready;
        return resolved;
    }

    /// <summary>
    /// Creates the HUD (EventSystem + GameCanvas + joystick + skill cluster) in the current scene.
    /// Fail-safe: never throws — returns a dummy instance marked failed so callers don't retry
    /// every frame (a throwing per-frame retry aborts HeroCombatController's input polling).
    /// </summary>
    public static BattleHUD EnsureCreated()
    {
        if (_instance is { IsDisposed: false } existing) return existing;

        var hud = new BattleHUD();
        try
        {
            // UI pointer events require a scene EventSystem with an input module.
            EnsureEventSystem();

            // Preferred path: the machine-local HUD prefab (built once via
            // "Zonezero/Rebuild Battle HUD Prefab" — its serialized sprite AssetRefs carry
            // THIS machine's GUIDs, so every Image resolves its art natively).
            var hudGo = TryInstantiateHudPrefab();
            if (hudGo != null)
            {
                var fromPrefab = hudGo.GetComponent<BattleHUD>();
                if (fromPrefab != null) return fromPrefab;
            }

            // Fallback: procedural build (plain tinted quads when sprite resolution fails).
            var go = new GameObject("BattleHUD");
            var canvas = go.AddComponent<GameCanvas>();
            canvas.UIScaleMode = ScaleMode.ScaleWithScreenSize;
            canvas.ReferenceResolution = new Float2(1280f, 720f);
            canvas.MatchWidthOrHeight = 1f;
            go.AddComponent<BattleHUD>();
            Runtime.Resources.Scene.Current?.Add(go);
            hud = go.GetComponent<BattleHUD>()!;
        }
        catch (Exception ex)
        {
            Runtime.Debug.LogError($"[BattleHUD] creation failed — HUD disabled: {ex.Message}\n{ex.StackTrace}");
            hud._buildFailed = true;
        }
        return hud;
    }

    private static void EnsureEventSystem()
    {
        var scene = Runtime.Resources.Scene.Current;
        if (scene == null) return;
        foreach (GameObject root in scene.RootObjects)
            if (root.GetComponent<EventSystem>() != null) return;
        var esGo = new GameObject("UIEventSystem");
        esGo.AddComponent<EventSystem>();
        esGo.AddComponent<StandaloneInputModule>();
        scene.Add(esGo);
    }

    private static GameObject? TryInstantiateHudPrefab()
    {
        try
        {
            if (XEngine.Zonezero.Config.BattleAssetCatalog.Load() is { } catalog)
            {
                var reference = catalog.HudPrefab;
                reference.EnsureLoaded();
                if (reference.Res is { } packagedPrefab && packagedPrefab.Instantiate() is { } packagedInstance)
                {
                    Runtime.Resources.Scene.Current?.Add(packagedInstance);
                    return packagedInstance;
                }
            }
            var backend = AssetDatabase.Current;
            var getEntry = backend?.GetType().GetMethod("GetEntry", new[] { typeof(string) });
            var entry = getEntry?.Invoke(backend, new object[] { "ZZZ/Prefab/BattleHUD.prefab" });
            if (entry == null) return null; // prefab not built on this machine — procedural fallback
            var guid = (Guid?)entry.GetType().GetField("Guid")?.GetValue(entry);
            if (guid is not { } prefabGuid || prefabGuid == Guid.Empty) return null;
            if (AssetDatabase.Get(prefabGuid) is not PrefabAsset prefab) return null;
            var instance = prefab.Instantiate();
            if (instance == null) return null;
            instance.Name = "BattleHUD";
            Runtime.Resources.Scene.Current?.Add(instance);
            return instance;
        }
        catch
        {
            return null; // any backend mismatch → procedural fallback
        }
    }

    private Joystick? _joystick;
    private SkillButton? _attackButton;
    private SkillButton? _skillKButton;
    private SkillButton? _skillLButton;
    private SkillButton? _skillIButton;
    private HeroCombatController? _combat;
    // Defer visual construction to the first Update tick: OnAddedToScene fires during
    // Scene.Load before the asset DB finishes importing new textures, so sprite GUID
    // resolution fails → white squares. By Update time all assets are resident.
    private bool _needsBuild = true;
    // Set when construction threw — stops the per-frame retry (each retry would abort
    // this component's Update; better to run input-less than dead).
    private bool _buildFailed;

    public Joystick? Stick => _joystick;
    public SkillButton? AttackButton => _attackButton;
    public SkillButton? SkillK => _skillKButton;
    public SkillButton? SkillL => _skillLButton;
    public SkillButton? SkillI => _skillIButton;

    public override void OnAddedToScene()
    {
        _instance = this;
        _needsBuild = true;
    }

    private void BuildChildren()
    {
        // Left half: joystick touch zone (stretch anchors fill the half-rect exactly).
        var zoneGo = new GameObject("JoystickZone");
        zoneGo.SetParent(GameObject, worldPositionStays: false);
        var zoneRect = zoneGo.EnsureRectTransform();
        zoneRect.AnchorMin = new Float2(0f, 0f);
        zoneRect.AnchorMax = new Float2(0.5f, 1f);
        zoneRect.SizeDelta = Float2.Zero;
        zoneRect.AnchoredPosition = Float2.Zero;
        _joystick = zoneGo.AddComponent<Joystick>();
        _joystick.BaseRect = CreateImageChild(zoneGo, "JoystickBase", 220f,
            SpriteRef(JoystickBaseFile), new Color(1f, 1f, 1f, 0.5f), topLeft: true);
        _joystick.ThumbRect = CreateImageChild(zoneGo, "JoystickThumb", 95f,
            SpriteRef(JoystickThumbFile), new Color(1f, 1f, 1f, 0.85f), topLeft: true);
        _joystick.OnChanged += BattleTouchInputBridge.SetMove;
        _joystick.OnReleased += BattleTouchInputBridge.ReleaseMove;

        // Right bottom: skill cluster (offsets from the bottom-right corner).
        _attackButton = CreateSkillButton("AttackJ", 130f, new Float2(-95f, 70f),
            AttackFile, "J", slot: 0);
        _skillKButton = CreateSkillButton("SkillK", 92f, new Float2(-235f, 70f),
            SkillKFile, "K", slot: 1);
        _skillLButton = CreateSkillButton("SkillL", 92f, new Float2(-150f, 195f),
            SkillLFile, "L", slot: 2);
        _skillIButton = CreateSkillButton("SkillI", 105f, new Float2(-285f, 230f),
            SkillIFile, "I", slot: 3);
    }

    private SkillButton CreateSkillButton(
        string name, float size, Float2 offset,
        string iconFile, string keyLabel, int slot)
    {
        var go = new GameObject(name);
        go.SetParent(GameObject, worldPositionStays: false);
        var rect = go.EnsureRectTransform();
        rect.AnchorMin = new Float2(1f, 0f);
        rect.AnchorMax = new Float2(1f, 0f);
        rect.Pivot = new Float2(0.5f, 0.5f);
        rect.SizeDelta = new Float2(size, size);
        rect.AnchoredPosition = offset;

        AssetRef<Sprite>? cdSprite = SpriteRef(CdMaskFile);
        var button = go.AddComponent<SkillButton>();
        button.BuildVisuals(size, cdSprite, SpriteRef(iconFile), cdSprite, keyLabel);
        button.Pressed += () => BattleTouchInputBridge.TapSkill(slot);
        go.AddComponent<CanvasRenderer>();
        return button;
    }

    private static RectTransform CreateImageChild(
        GameObject parent, string name, float size, AssetRef<Sprite>? sprite, Color color, bool topLeft = false)
    {
        var go = new GameObject(name);
        go.SetParent(parent, worldPositionStays: false);
        var rect = go.EnsureRectTransform();
        Float2 anchor = topLeft ? new Float2(0f, 1f) : new Float2(0.5f, 0.5f);
        rect.AnchorMin = anchor;
        rect.AnchorMax = anchor;
        rect.Pivot = new Float2(0.5f, 0.5f);
        rect.SizeDelta = new Float2(size, size);
        rect.AnchoredPosition = Float2.Zero;
        var image = go.AddComponent<Image>();
        if (sprite is { } resolved) image.Sprite = resolved;
        image.Color = color;
        go.AddComponent<CanvasRenderer>();
        return rect;
    }

    public override void OnDisable()
    {
        BattleTouchInputBridge.ReleaseAll();
    }

    public override void Update()
    {
        if (_diagnostics) CaptureRenderDiagnostics();
        if (_diagnostics && Time.FrameCount >= _diagnosticFrame)
        {
            _diagnosticFrame = Time.FrameCount + 30;
            var hero = FindCombat();
            var stats = XEngine.Runtime.Rendering.RenderStats.Last;
            string cameras = "";
            if (Runtime.Resources.Scene.Current is { } scene)
                foreach (var root in scene.RootObjects)
                    foreach (var camera in root.GetComponentsInChildren<Camera>(true, true))
                        cameras += $" {camera.GameObject.Name}:{camera.EnabledInHierarchy}:{camera.Target != null}:{camera.Transform.Position}";
            Runtime.Debug.Log($"[BattleDiagnostics] frame={Time.FrameCount} hero={hero?.Transform.Position} action={hero?.ActiveAction} draw={stats.DrawCalls} triangles={stats.Triangles} rtBytes={stats.TransientRenderTargetBytes} managed={GC.GetTotalMemory(false)} cameras={cameras}");
        }
        if (_buildFailed) return;

        // First tick: wire up. Prefab instances (built by BattleHudPrefabBuilder) already carry
        // their visuals + sprites — only event subscriptions are dynamic (they don't serialize).
        // A bare component falls back to building plain procedural visuals.
        if (_needsBuild)
        {
            _needsBuild = false;
            try
            {
                BindOrBuild();
            }
            catch (Exception ex)
            {
                _buildFailed = true;
                Runtime.Debug.LogError($"[BattleHUD] build failed — HUD disabled: {ex.Message}\n{ex.StackTrace}");
                return;
            }
            return;
        }

        _combat ??= FindCombat();
        if (_combat == null) return;

        _attackButton?.SetCooldown(_combat, slot: 0);
        _skillKButton?.SetCooldown(_combat, slot: 1);
        _skillLButton?.SetCooldown(_combat, slot: 2);
        _skillIButton?.SetCooldown(_combat, slot: 3);
    }

    /// <summary>Prefab instance → bind events to the authored children; bare → build procedural.</summary>
    private void BindOrBuild()
    {
        _joystick = GetComponentInChildren<Joystick>();
        if (_joystick != null)
        {
            // Prefab path: repair authored visual references as well as subscribing the input
            // bridge.  Prefabs generated before the local texture import (or copied from another
            // machine) legitimately contain the default/empty Sprite AssetRefs; leaving them
            // untouched renders the white placeholder forever.
            RepairJoystickVisuals(_joystick);
            _joystick.OnChanged += BattleTouchInputBridge.SetMove;
            _joystick.OnReleased += BattleTouchInputBridge.ReleaseMove;
            _attackButton = FindButton("AttackJ", slot: 0);
            _skillKButton = FindButton("SkillK", slot: 1);
            _skillLButton = FindButton("SkillL", slot: 2);
            _skillIButton = FindButton("SkillI", slot: 3);
            RepairButtonVisuals(_attackButton, AttackFile);
            RepairButtonVisuals(_skillKButton, SkillKFile);
            RepairButtonVisuals(_skillLButton, SkillLFile);
            RepairButtonVisuals(_skillIButton, SkillIFile);
            return;
        }

        // Procedural fallback (no prefab on this machine): plain visuals, runtime sprite resolve.
        BuildChildren();
    }

    private SkillButton? FindButton(string name, int slot)
    {
        foreach (Transform child in GameObject!.Transform.GetChildren())
        {
            if (child.GameObject?.Name != name) continue;
            var button = child.GameObject.GetComponent<SkillButton>();
            if (button != null)
            {
                button.BindExistingVisuals();
                button.Pressed += () => BattleTouchInputBridge.TapSkill(slot);
            }
            return button;
        }
        return null;
    }

    private static void RepairJoystickVisuals(Joystick joystick)
    {
        RepairImage(joystick.BaseRect?.GameObject?.GetComponent<Image>(), JoystickBaseFile);
        RepairImage(joystick.ThumbRect?.GameObject?.GetComponent<Image>(), JoystickThumbFile);
    }

    private static void RepairButtonVisuals(SkillButton? button, string iconFile)
    {
        if (button == null) return;
        RepairImage(button.Icon, iconFile);
        RepairImage(button.Background, CdMaskFile);
        if (button.Background is { } background) background.Color = SkillButton.BackgroundTint;
        RepairImage(button.CdMask, CdMaskFile);
    }

    private static void RepairImage(Image? image, string fileName)
    {
        if (image == null) return;
        AssetRef<Sprite>? sprite = SpriteRef(fileName);
        if (sprite is not { } resolved) return;

        image.Sprite = resolved;
        // AssetRef<Sprite>.EnsureLoaded also resolves the source texture above, but the image
        // may already have completed its first rebuild while the stale prefab was loading.  Mark
        // both phases dirty so this repair is effective immediately and remains cheap thereafter.
        image.SetVerticesDirty();
        image.SetMaterialDirty();
    }

    private HeroCombatController? FindCombat()
    {
        var scene = Runtime.Resources.Scene.Current;
        if (scene == null) return null;
        foreach (GameObject root in scene.RootObjects)
        {
            var controller = root.GetComponent<HeroCombatController>();
            if (controller != null) return controller;
        }
        return null;
    }
}
