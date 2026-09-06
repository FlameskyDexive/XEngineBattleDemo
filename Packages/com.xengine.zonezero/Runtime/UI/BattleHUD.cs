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

    // Imported HUD textures (Assets/ZZZ/Arts/UI/HUD) — texture GUIDs from their .meta files.
    private static readonly Guid JoystickBaseTex = new("fae7722a-3cb2-45f4-8fda-92b5c2691126");
    private static readonly Guid JoystickThumbTex = new("02bf1031-4aa0-419c-88bc-0ef24c982573");
    private static readonly Guid AttackTex = new("600db620-4f91-4200-803d-efd65a5092b6");
    private static readonly Guid SkillKTex = new("f273abf5-09bd-4fae-986b-dd259765019d");
    private static readonly Guid SkillLTex = new("936b4219-7a15-4c7a-b7e2-b6bdb16ce996");
    private static readonly Guid SkillITex = new("0744f25a-360c-475e-97dc-86eba738c840");
    private static readonly Guid RollTex = new("f5dba789-8c56-483b-bfc9-179b51fbcedc");
    private static readonly Guid CdMaskTex = new("633974b7-dc0d-4888-9828-6ae0e18b029d");

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
        => new(SpriteGuid(texGuid, texFileName));

    /// <summary>Creates the HUD (GameCanvas + joystick + skill cluster) in the current scene.</summary>
    public static BattleHUD EnsureCreated()
    {
        if (_instance is { IsDisposed: false } existing) return existing;

        var go = new GameObject("BattleHUD");
        go.AddComponent<GameCanvas>();
        go.AddComponent<BattleHUD>();
        Runtime.Resources.Scene.Current?.Add(go);
        _instance = go.GetComponent<BattleHUD>();
        return _instance;
    }

    private Joystick? _joystick;
    private SkillButton? _attackButton;
    private SkillButton? _skillKButton;
    private SkillButton? _skillLButton;
    private SkillButton? _skillIButton;
    private HeroCombatController? _combat;

    public Joystick? Stick => _joystick;
    public SkillButton? AttackButton => _attackButton;
    public SkillButton? SkillK => _skillKButton;
    public SkillButton? SkillL => _skillLButton;
    public SkillButton? SkillI => _skillIButton;

    public override void OnAddedToScene()
    {
        BuildChildren();
        _instance = this;
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
