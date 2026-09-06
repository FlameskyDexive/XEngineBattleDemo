// This file is part of the XEngine Game Engine
// Licensed under the MIT License. See the LICENSE file in the project root for details.

using System;
using System.Security.Cryptography;
using System.Text;

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

    // Imported HUD textures (Assets/ZZZ/Arts/UI/HUD) — texture GUIDs from their CURRENT .meta
    // files (the editor re-assigned GUIDs when the hand-written metas proved unreadable; these
    // MUST match the .meta or every sprite resolves to null → white squares).
    private static readonly Guid JoystickBaseTex = new("8e1b6990-e50a-4f1d-94c8-3f0acffb2bc4");
    private static readonly Guid JoystickThumbTex = new("6e09e608-3d25-4d60-b5f7-fd708e2a68ce");
    private static readonly Guid AttackTex = new("43c8d3c4-b443-45d8-ab0a-2fa2344a0ee7");
    private static readonly Guid SkillKTex = new("13d63023-cf32-4061-a2a1-d3275197436c");
    private static readonly Guid SkillLTex = new("85f259f4-0f50-40b6-a865-a53dce61f0e9");
    private static readonly Guid SkillITex = new("0fa51ece-b98f-4d49-9f46-ecae3b11998f");
    private static readonly Guid RollTex = new("91f2f374-7559-46f9-8533-4d551d5860bc");
    private static readonly Guid CdMaskTex = new("6973055a-2f89-4541-bcb3-bff7ba42817f");

    /// <summary>
    /// Sprite sub-asset GUID = SHA256(parentGuid bytes + texture file name)[0..16] — the same
    /// derivation as the editor's sub-asset registration (AssetEntry.DeriveSubAssetGuid).
    /// </summary>
    private static Guid SpriteGuid(Guid texGuid, string texFileName)
    {
        byte[] parentBytes = texGuid.ToByteArray();
        byte[] nameBytes = Encoding.UTF8.GetBytes(texFileName);
        byte[] combined = new byte[parentBytes.Length + nameBytes.Length];
        parentBytes.CopyTo(combined, 0);
        nameBytes.CopyTo(combined, parentBytes.Length);
        byte[] hash = SHA256.HashData(combined);
        return new Guid(hash.AsSpan(0, 16));
    }

    private static AssetRef<Sprite> SpriteRef(Guid texGuid, string texFileName)
    {
        var reference = new AssetRef<Sprite>(SpriteGuid(texGuid, texFileName));
        // Blocking one-time load: Image bakes its mesh when the Sprite property is assigned, and
        // the async `.Res` path returns null until streaming completes — the bake would fall back
        // to the white default and never re-run (white-square bug). These are 8 tiny textures.
        reference.EnsureLoaded();
        return reference;
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
            if (Runtime.Resources.Scene.Current != null)
            {
                bool hasEventSystem = false;
                foreach (GameObject root in Runtime.Resources.Scene.Current.RootObjects)
                    if (root.GetComponent<EventSystem>() != null) { hasEventSystem = true; break; }
                if (!hasEventSystem)
                {
                    var esGo = new GameObject("UIEventSystem");
                    esGo.AddComponent<EventSystem>();
                    esGo.AddComponent<StandaloneInputModule>();
                    Runtime.Resources.Scene.Current.Add(esGo);
                }
            }

            var go = new GameObject("BattleHUD");
            go.AddComponent<GameCanvas>();
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
            SpriteRef(JoystickBaseTex, "joystick_base"), new Color(1f, 1f, 1f, 0.5f));
        _joystick.ThumbRect = CreateImageChild(zoneGo, "JoystickThumb", 95f,
            SpriteRef(JoystickThumbTex, "joystick_thumb"), new Color(1f, 1f, 1f, 0.85f));
        _joystick.OnChanged += BattleTouchInputBridge.SetMove;
        _joystick.OnReleased += BattleTouchInputBridge.ReleaseMove;

        // Right bottom: skill cluster (offsets from the bottom-right corner).
        _attackButton = CreateSkillButton("AttackJ", 130f, new Float2(-95f, 70f),
            AttackTex, "btn_attack", "J", slot: 0);
        _skillKButton = CreateSkillButton("SkillK", 92f, new Float2(-235f, 70f),
            SkillKTex, "btn_skill_k", "K", slot: 1);
        _skillLButton = CreateSkillButton("SkillL", 92f, new Float2(-150f, 195f),
            SkillLTex, "btn_skill_l", "L", slot: 2);
        _skillIButton = CreateSkillButton("SkillI", 105f, new Float2(-285f, 230f),
            SkillITex, "btn_skill_i", "I", slot: 3);
    }

    private SkillButton CreateSkillButton(
        string name, float size, Float2 offset,
        Guid texGuid, string texName, string keyLabel, int slot)
    {
        var go = new GameObject(name);
        go.SetParent(GameObject, worldPositionStays: false);
        var rect = go.EnsureRectTransform();
        rect.AnchorMin = new Float2(1f, 0f);
        rect.AnchorMax = new Float2(1f, 0f);
        rect.Pivot = new Float2(0.5f, 0.5f);
        rect.SizeDelta = new Float2(size, size);
        rect.AnchoredPosition = offset;

        AssetRef<Sprite> cdSprite = SpriteRef(CdMaskTex, "cd_mask");
        var button = go.AddComponent<SkillButton>();
        button.BuildVisuals(size, cdSprite, SpriteRef(texGuid, texName), cdSprite, keyLabel);
        button.Pressed += () => BattleTouchInputBridge.TapSkill(slot);
        go.AddComponent<CanvasRenderer>();
        return button;
    }

    private static RectTransform CreateImageChild(
        GameObject parent, string name, float size, AssetRef<Sprite> sprite, Color color)
    {
        var go = new GameObject(name);
        go.SetParent(parent, worldPositionStays: false);
        var rect = go.EnsureRectTransform();
        rect.AnchorMin = new Float2(0.5f, 0.5f);
        rect.AnchorMax = new Float2(0.5f, 0.5f);
        rect.Pivot = new Float2(0.5f, 0.5f);
        rect.SizeDelta = new Float2(size, size);
        rect.AnchoredPosition = Float2.Zero;
        var image = go.AddComponent<Image>();
        image.Sprite = sprite;
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
        if (_buildFailed) return;

        // Deferred visual construction: the asset DB must finish importing new textures before
        // sprite GUID resolution succeeds (OnAddedToScene fires during Scene.Load, too early).
        if (_needsBuild)
        {
            _needsBuild = false;
            try
            {
                BuildChildren();
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
