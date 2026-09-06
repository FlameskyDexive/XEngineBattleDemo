// This file is part of the XEngine Game Engine
// Licensed under the MIT License. See the LICENSE file in the project root for details.

using System;
using System.IO;

using XEngine.Editor;
using XEngine.Editor.GUI.SceneView;
using XEngine.Editor.Projects;
using XEngine.Runtime;
using XEngine.Runtime.Resources;
using XEngine.Runtime.UI;
using XEngine.Vector;
using XEngine.Zonezero.UI;

namespace XEngine.Zonezero.Editor;

/// <summary>
/// Builds the battle HUD prefab (virtual joystick + skill cluster) with LOCAL sprite
/// references. Run this menu item once per machine after pulling: the generated prefab
/// serializes this machine's texture GUIDs, so every Image resolves its art at load time —
/// no runtime GUID guessing, no white squares. Art source: Assets/ZZZ/Arts/UI/HUD (copied
/// from the MagicCreator Unity project).
/// </summary>
public static class BattleHudPrefabBuilder
{
    private const string HudRoot = "ZZZ/Arts/UI/HUD/";
    private const string DestPath = "ZZZ/Prefab/BattleHUD.prefab";

    [MenuItem("Zonezero/Rebuild Battle HUD Prefab")]
    public static void Build()
    {
        var backend = EditorAssetBackend.Instance;
        if (backend == null)
        {
            Runtime.Debug.LogError("[BattleHUD] no editor asset backend — open a project first.");
            return;
        }

        // Root: canvas host + HUD driver.
        var root = new GameObject("BattleHUD");
        root.AddComponent<GameCanvas>();
        root.AddComponent<BattleHUD>(); // BindOrBuild() wires events to these children at play

        // ── Left half: joystick touch zone + hidden-at-rest visuals ──
        var zone = new GameObject("JoystickZone");
        zone.SetParent(root, worldPositionStays: false);
        var zoneRect = zone.EnsureRectTransform();
        zoneRect.AnchorMin = new Float2(0f, 0f);
        zoneRect.AnchorMax = new Float2(0.5f, 1f);
        zoneRect.SizeDelta = Float2.Zero;
        zoneRect.AnchoredPosition = Float2.Zero;
        var joystick = zone.AddComponent<Joystick>();
        joystick.BaseRect = ImageChild(zone, "Base", 220f, Hud("joystick_base.png"), 0.55f);
        joystick.ThumbRect = ImageChild(zone, "Thumb", 95f, Hud("joystick_thumb.png"), 0.9f);

        // ── Right bottom: skill cluster (slot 0..3 = J/K/L/I) ──
        SkillButton(root, "AttackJ", 130f, new Float2(-95f, 70f), Hud("btn_attack.png"), "J");
        SkillButton(root, "SkillK", 92f, new Float2(-235f, 70f), Hud("btn_skill_k.png"), "K");
        SkillButton(root, "SkillL", 92f, new Float2(-150f, 195f), Hud("btn_skill_l.png"), "L");
        SkillButton(root, "SkillI", 105f, new Float2(-285f, 230f), Hud("btn_skill_i.png"), "I");

        // Serialize as a native prefab (local GUIDs land in the file via the sprite instances).
        string abs = Path.GetFullPath(Path.Combine(Project.Current!.AssetsPath, "..", DestPath));
        ZonezeroNativeAssets.WriteGameObjectAsPrefab(root, abs, Guid.NewGuid());
        root.Dispose();
        Runtime.Debug.Log($"[BattleHUD] prefab written: {DestPath} (local sprite GUIDs — machine-correct).");
    }

    /// <summary>Resolves a HUD texture's Sprite sub-asset via the LOCAL asset database.</summary>
    private static Sprite? Hud(string fileName)
    {
        var backend = EditorAssetBackend.Instance!;
        var entry = backend.GetEntry(HudRoot + fileName);
        if (entry == null)
        {
            Runtime.Debug.LogError($"[BattleHUD] missing asset entry '{HudRoot + fileName}' — copy the HUD textures first.");
            return null;
        }
        foreach (SubAssetEntry sub in entry.SubAssets)
        {
            if (AssetDatabase.Get(sub.Guid) is Sprite sprite)
                return sprite;
        }
        Runtime.Debug.LogError($"[BattleHUD] '{fileName}' has no Sprite sub-asset (TextureImporter sprite mode?).");
        return null;
    }

    private static RectTransform ImageChild(GameObject parent, string name, float sizePx, Sprite? sprite, float alpha)
    {
        var go = new GameObject(name);
        go.SetParent(parent, worldPositionStays: false);
        var rect = go.EnsureRectTransform();
        rect.AnchorMin = new Float2(0.5f, 0.5f);
        rect.AnchorMax = new Float2(0.5f, 0.5f);
        rect.Pivot = new Float2(0.5f, 0.5f);
        rect.SizeDelta = new Float2(sizePx, sizePx);
        rect.AnchoredPosition = Float2.Zero;
        var image = go.AddComponent<Image>();
        if (sprite != null) image.Sprite = new AssetRef<Sprite>(sprite);
        image.Color = new Color(1f, 1f, 1f, alpha);
        go.AddComponent<CanvasRenderer>();
        return rect;
    }

    private static void SkillButton(
        GameObject root, string name, float size, Float2 offset, Sprite? icon, string key)
    {
        var go = new GameObject(name);
        go.SetParent(root, worldPositionStays: false);
        var rect = go.EnsureRectTransform();
        rect.AnchorMin = new Float2(1f, 0f);
        rect.AnchorMax = new Float2(1f, 0f);
        rect.Pivot = new Float2(0.5f, 0.5f);
        rect.SizeDelta = new Float2(size, size);
        rect.AnchoredPosition = offset;
        go.AddComponent<CanvasRenderer>();

        var button = go.AddComponent<SkillButton>();
        button.BuildVisuals(
            size,
            Ref(Hud("cd_mask.png")),
            Ref(icon),
            Ref(Hud("cd_mask.png")),
            key);
        button.Pressed += () => BattleTouchInputBridge.TapSkill(key switch { "J" => 0, "K" => 1, "L" => 2, _ => 3 });
    }

    private static AssetRef<Sprite>? Ref(Sprite? sprite) => sprite != null ? new AssetRef<Sprite>(sprite) : null;
}
