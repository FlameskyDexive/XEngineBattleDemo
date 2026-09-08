// This file is part of the XEngine Game Engine
// Licensed under the MIT License. See the LICENSE file in the project root for details.

using System;

using XEngine.Runtime;
using XEngine.Runtime.Resources;
using XEngine.Runtime.UI;
using XEngine.Vector;
using XEngine.Zonezero.Combat;

namespace XEngine.Zonezero.UI;

/// <summary>
/// Battle skill button: icon + radial cooldown mask + countdown label. Fires <see cref="Pressed"/>
/// on pointer DOWN (action-game feel — the skill triggers the instant the finger lands, not on
/// release). Cooldown state is pushed by the HUD each frame via <see cref="SetCooldown"/>.
/// </summary>
public sealed class SkillButton : UIBehaviour, IPointerDownHandler
{
    public event Action? Pressed;

    /// <summary>Visuals are child Image/Text nodes; the zone itself draws nothing.</summary>
    public override void GenerateMesh(UIMeshBuilder builder, in UIContext context) { }

    /// <summary>True: the button rect is a pointer candidate even though the hit geometry lives
    /// on child Images (events bubble from the hit Graphic up to this handler).</summary>
    public override bool IsRaycastCandidate => true;

    private Image? _icon;
    private Image? _background;
    private Image? _cdMask;
    private Text? _cdLabel;
    private Text? _keyLabel;

    public Image? Icon => _icon;
    public Image? Background => _background;
    public Image? CdMask => _cdMask;
    public Text? CdLabel => _cdLabel;
    public Text? KeyLabel => _keyLabel;

    /// <summary>
    /// Rebinds the non-serialized visual fields from an authored prefab.  SkillButton keeps these
    /// fields private so a procedural button can hold direct references, but a prefab only stores
    /// the child Image/Text components; without this pass cooldown updates and runtime sprite
    /// repair would have no targets.
    /// </summary>
    internal void BindExistingVisuals()
    {
        foreach (Image image in GameObject.GetComponentsInChildren<Image>(includeSelf: true, includeInactive: true))
        {
            switch (image.GameObject?.Name)
            {
                case "Background": _background = image; break;
                case "Icon": _icon = image; break;
                case "CdMask": _cdMask = image; break;
            }
        }

        foreach (Text text in GameObject.GetComponentsInChildren<Text>(includeSelf: true, includeInactive: true))
        {
            switch (text.GameObject?.Name)
            {
                case "CdLabel": _cdLabel = text; break;
                case "KeyLabel": _keyLabel = text; break;
            }
        }
    }

    /// <summary>Builds the visual children: button bg+icon, cooldown mask, countdown label, key label.
    /// Nullable sprite refs fall back to the Image default (tinted quad).</summary>
    public void BuildVisuals(
        float size,
        AssetRef<Sprite>? backgroundSprite,
        AssetRef<Sprite>? iconSprite,
        AssetRef<Sprite>? cdMaskSprite,
        string keyLabel)
    {
        var bgRect = CreateChild("Background", size);
        var bgImage = bgRect.GameObject!.AddComponent<Image>();
        _background = bgImage;
        if (backgroundSprite is { } bg) bgImage.Sprite = bg;
        bgImage.Color = new Color(1f, 1f, 1f, 0.9f);

        var iconRect = CreateChild("Icon", size * 0.72f);
        _icon = iconRect.GameObject!.AddComponent<Image>();
        if (iconSprite is { } icon) _icon.Sprite = icon;
        _icon.Color = new Color(1f, 1f, 1f, 1f);

        var cdRect = CreateChild("CdMask", size);
        _cdMask = cdRect.GameObject!.AddComponent<Image>();
        if (cdMaskSprite is { } mask) _cdMask.Sprite = mask;
        _cdMask.FillMethod = FillMethod.Radial360;
        _cdMask.FillOrigin = 0;
        _cdMask.FillClockwise = true;
        _cdMask.FillAmount = 0f;
        _cdMask.Color = new Color(0f, 0f, 0f, 0.65f);
        cdRect.GameObject.Enabled = false;

        var labelRect = CreateChild("CdLabel", size);
        _cdLabel = labelRect.GameObject!.AddComponent<Text>();
        _cdLabel.TextValue = string.Empty;
        _cdLabel.TextColor = new Color(1f, 1f, 1f, 1f);
        labelRect.GameObject.Enabled = false;

        var keyRect = CreateChild("KeyLabel", size * 0.4f);
        keyRect.AnchoredPosition = new Float2(0f, -size * 0.5f);
        _keyLabel = keyRect.GameObject!.AddComponent<Text>();
        _keyLabel.TextValue = keyLabel;
        _keyLabel.TextColor = new Color(1f, 1f, 1f, 0.8f);
    }

    public void SetCooldown(float remaining, float total)
    {
        bool inCd = remaining > 0f && total > 0f;
        if (_cdMask != null)
        {
            _cdMask.FillAmount = inCd ? remaining / total : 0f;
            _cdMask.GameObject!.Enabled = inCd;
        }
        if (_cdLabel != null)
        {
            _cdLabel.TextValue = inCd ? remaining.ToString("F1") : string.Empty;
            _cdLabel.GameObject!.Enabled = inCd;
        }
    }

    /// <summary>Reads the combat controller's slot cooldown and refreshes the mask + label.</summary>
    public void SetCooldown(HeroCombatController combat, int slot)
    {
        (float remaining, float total) = combat.GetCooldown(slot);
        SetCooldown(remaining, total);
    }

    public void OnPointerDown(PointerEventData e)
    {
        Pressed?.Invoke();
        e.Use();
    }

    private RectTransform CreateChild(string name, float size)
    {
        var go = new GameObject(name);
        go.SetParent(GameObject, worldPositionStays: false);
        var rect = go.EnsureRectTransform();
        rect.AnchorMin = new Float2(0.5f, 0.5f);
        rect.AnchorMax = new Float2(0.5f, 0.5f);
        rect.Pivot = new Float2(0.5f, 0.5f);
        rect.SizeDelta = new Float2(size, size);
        rect.AnchoredPosition = Float2.Zero;
        go.AddComponent<CanvasRenderer>();
        return rect;
    }
}
