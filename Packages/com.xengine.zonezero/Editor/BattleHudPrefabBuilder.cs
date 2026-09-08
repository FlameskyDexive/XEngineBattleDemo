// This file is part of the XEngine Game Engine
// Licensed under the MIT License. See the LICENSE file in the project root for details.

using System;
using System.IO;

using XEngine.Data.Provenance;
using XEngine.Echo;
using XEngine.Editor;
using XEngine.Editor.Core;
using XEngine.Editor.GUI.SceneView;
using XEngine.Editor.Projects;
using XEngine.Runtime;
using XEngine.Runtime.Resources;
using XEngine.Runtime.UI;
using XEngine.Vector;
using XEngine.Zonezero.UI;

namespace XEngine.Zonezero.Editor;

/// <summary>
/// Builds the battle HUD prefab (virtual joystick + skill cluster) with Sprite references
/// resolved from the project's imported HUD textures. Art source:
/// Assets/ZZZ/Arts/UI/HUD (copied from the MagicCreator Unity project).
/// </summary>
public static class BattleHudPrefabBuilder
{
    private const string HudRoot = "ZZZ/Arts/UI/HUD/";
    private const string DestPath = "ZZZ/Prefab/BattleHUD.prefab";

    [MenuItem("Zonezero/Rebuild Battle HUD Prefab")]
    public static void Build()
    {
        var backend = EditorAssetBackend.Instance;
        var project = Project.Current;
        if (backend == null || project == null)
        {
            Runtime.Debug.LogError("[BattleHUD] no active editor project — open a project first.");
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
        // Top-left (0,1) anchors: Joystick maps canvas-design pointer offsets 1:1.
        joystick.BaseRect = ImageChild(zone, "Base", 220f, Hud("joystick_base.png"), 0.55f, topLeft: true);
        joystick.ThumbRect = ImageChild(zone, "Thumb", 95f, Hud("joystick_thumb.png"), 0.9f, topLeft: true);

        // ── Right bottom: skill cluster (slot 0..3 = J/K/L/I) ──
        SkillButton(root, "AttackJ", 130f, new Float2(-95f, 70f), Hud("btn_attack.png"), "J");
        SkillButton(root, "SkillK", 92f, new Float2(-235f, 70f), Hud("btn_skill_k.png"), "K");
        SkillButton(root, "SkillL", 92f, new Float2(-150f, 195f), Hud("btn_skill_l.png"), "L");
        SkillButton(root, "SkillI", 105f, new Float2(-285f, 230f), Hud("btn_skill_i.png"), "I");

        string absolutePath = Path.Combine(
            project.AssetsPath,
            DestPath.Replace('/', Path.DirectorySeparatorChar));

        try
        {
            EchoObject before = ReadExistingSource(absolutePath);
            EchoObject after = SerializePrefabSource(root);

            WritePrefabSource(absolutePath, after);

            // Import only the rebuilt source asset. This refreshes the asset database without
            // stamping the temporary root or refreshing any scene instance.
            Guid prefabGuid = backend.ImportFile(DestPath);
            if (prefabGuid == Guid.Empty)
            {
                Runtime.Debug.LogError($"[BattleHUD] prefab was written but could not be imported: {DestPath}.");
                return;
            }

            RecordSourceChange(project, before, after);
            Runtime.Debug.Log($"[BattleHUD] prefab rebuilt: {DestPath}.");
        }
        catch (Exception ex)
        {
            Runtime.Debug.LogError($"[BattleHUD] failed to rebuild prefab '{DestPath}': {ex.Message}");
        }
        finally
        {
            root.Dispose();
        }
    }

    private static EchoObject ReadExistingSource(string absolutePath)
        => File.Exists(absolutePath)
            ? EchoObject.ReadFromString(File.ReadAllText(absolutePath))
            : EchoObject.NewCompound();

    /// <summary>Serializes a standalone prefab source without mutating scene state.</summary>
    private static EchoObject SerializePrefabSource(GameObject source)
    {
        source.ClearPrefabDataRecursive();
        Guid savedId = source.AssetID;
        source.AssetID = Guid.Empty;
        try
        {
            return Serializer.Serialize(typeof(object), source)
                ?? throw new InvalidOperationException($"failed to serialize prefab '{source.Name}'.");
        }
        finally
        {
            source.AssetID = savedId;
        }
    }

    private static void WritePrefabSource(string absolutePath, EchoObject source)
    {
        Directory.CreateDirectory(Path.GetDirectoryName(absolutePath)!);
        File.WriteAllText(absolutePath, source.WriteToString());

        // Never rewrite an existing meta: this keeps the complete importer settings and GUID intact
        // across a rebuild. EnsureMeta is only called when the sidecar is absent.
        if (!File.Exists(MetaFile.GetMetaPath(absolutePath)))
            MetaFile.EnsureMeta(absolutePath, "PrefabImporter");
    }

    private static void RecordSourceChange(Project project, EchoObject before, EchoObject after)
    {
        ChangeProvenance provenance = ChangeProvenance.ForEditor(
            intent: "Rebuild Battle HUD prefab",
            reason: "Zonezero/Rebuild Battle HUD Prefab");
        string beforeHash = ProvenancedDelta.ContentHash(before);
        string afterHash = ProvenancedDelta.ContentHash(after);

        // Use the editor's existing journal implementation for the source delta. The short-lived
        // service does not touch the scene; it only exposes the project's journal for this asset op.
        using var journal = new ChangeJournalService(project.RootPath, project.Name);
        journal.Journal.AppendAsync(DestPath, ProvenancedDelta.Create(before, after, provenance),
            provenance, beforeHash, afterHash).GetAwaiter().GetResult();
    }

    /// <summary>Resolves a HUD texture's Sprite sub-asset via the LOCAL asset database.</summary>
    private static AssetRef<Sprite>? Hud(string fileName)
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
            if (!sub.TypeName.Contains("Sprite", StringComparison.Ordinal)) continue;

            // Keep the imported sub-asset GUID even when Get() returns a transiently unloaded
            // instance. Serializing a Sprite instance with AssetID == Empty produces an empty
            // AssetRef in the prefab, which then falls back to a white Image quad.
            var reference = new AssetRef<Sprite>(sub.Guid);
            reference.EnsureLoaded();
            Sprite? sprite = reference.ResWeak;
            sprite?.Texture.EnsureLoaded();
            return reference;
        }
        Runtime.Debug.LogError($"[BattleHUD] '{fileName}' has no Sprite sub-asset (TextureImporter sprite mode?).");
        return null;
    }

    private static RectTransform ImageChild(GameObject parent, string name, float sizePx,
        AssetRef<Sprite>? sprite, float alpha, bool topLeft = false)
    {
        var go = new GameObject(name);
        go.SetParent(parent, worldPositionStays: false);
        var rect = go.EnsureRectTransform();
        Float2 anchor = topLeft ? new Float2(0f, 1f) : new Float2(0.5f, 0.5f);
        rect.AnchorMin = anchor;
        rect.AnchorMax = anchor;
        rect.Pivot = new Float2(0.5f, 0.5f);
        rect.SizeDelta = new Float2(sizePx, sizePx);
        rect.AnchoredPosition = Float2.Zero;
        var image = go.AddComponent<Image>();
        if (sprite is { } resolved) image.Sprite = resolved;
        image.Color = new Color(1f, 1f, 1f, alpha);
        go.AddComponent<CanvasRenderer>();
        return rect;
    }

    private static void SkillButton(
        GameObject root, string name, float size, Float2 offset, AssetRef<Sprite>? icon, string key)
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
        AssetRef<Sprite>? cdMask = Hud("cd_mask.png");
        button.BuildVisuals(size, cdMask, icon, cdMask, key);
        button.Pressed += () => BattleTouchInputBridge.TapSkill(key switch { "J" => 0, "K" => 1, "L" => 2, _ => 3 });
    }

}
