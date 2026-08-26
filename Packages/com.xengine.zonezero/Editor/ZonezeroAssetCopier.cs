using System;
using System.Collections.Generic;
using System.IO;
using System.Text.Json;
using System.Text.RegularExpressions;

using XEngine.Echo;
using XEngine.Editor;
using XEngine.UnityImporter.Editor;


namespace XEngine.Zonezero.Editor;

/// <summary>
/// Copies RAW asset files (FBX models + textures) from the ZZZ Unity reference project into an
/// XEngine project's Assets folder. Unity's own .prefab/.mat/.controller files are NOT copied —
/// native equivalents are generated from them in place by <see cref="ZonezeroNativeAssets"/>.
/// Unity .meta files are not copied either: the engine mints its own metas (deterministic
/// <see cref="StableGuid"/> of the Unity guid), texture import settings are aligned from the
/// Unity meta, and FBX unitScale comes from ModelImporter.globalScale. A `unity-guid-map.json`
/// sidecar records Unity-guid → relative-path for reference resolution, and a
/// `unity-anim-events.json` sidecar records per-FBX clip events + loop flags (from the FBX .meta
/// clipAnimations) for runtime injection and controller generation.
/// </summary>
public static partial class ZonezeroAssetCopier
{
    public const string DefaultSourceRoot = @"D:\Engine\ZZZ\Assets";
    public const string AnimEventsFileName = "unity-anim-events.json";

    /// <summary>Character/enemy/prefab unit folders (relative to the source Assets root) copied
    /// by the demo. The reference project names the hero unit folders in Chinese — they are
    /// copied into English destinations. Plug-in (MagicaCloth2/YSA Toon), scenes, and editor
    /// config stay out.</summary>
    public static readonly (string Source, string Dest)[] DemoUnits =
    {
        ("Arts/PlayerModel/安比", "Arts/PlayerModel/Anbi"),
        ("Arts/PlayerModel/可琳", "Arts/PlayerModel/Corin"),
        ("Arts/PlayerModel/妮可", "Arts/PlayerModel/Nicole"),
        ("Arts/EnemyModel/Claymore", "Arts/EnemyModel/Claymore"),
        ("Prefab", "Prefab"),
    };

    /// <summary>File extensions copied verbatim (engine importers handle them natively).</summary>
    private static readonly string[] s_textureExtensions = { ".png", ".jpg", ".jpeg", ".bmp", ".tga" };

    private static readonly string[] s_modelExtensions = { ".fbx" };

    [GeneratedRegex(@"^guid:\s*([0-9a-f]{32})", RegexOptions.Multiline)]
    private static partial Regex GuidRegex();

    public sealed class CopyResult
    {
        public int FilesCopied;
        public int TexturesAligned;
        public int ClipsWithEvents;
        public List<string> Warnings = new();
    }

    /// <summary>Per-clip data distilled from a Unity FBX .meta (clipAnimations entries).
    /// Properties (not fields) so System.Text.Json serializes them; shape matches the runtime
    /// <see cref="XEngine.Zonezero.ZonezeroAnimEvents"/> sidecar types.</summary>
    public sealed class ClipMeta
    {
        public bool Loop { get; set; }
        public List<ClipEventEntry> Events { get; set; } = new();
    }

    public sealed class ClipEventEntry
    {
        public float Time { get; set; }
        public string Function { get; set; } = string.Empty;
        public float FloatParam { get; set; }
        public int IntParam { get; set; }
        public string Data { get; set; } = string.Empty;
    }

    /// <summary>Copies the demo unit folders from the ZZZ source project into
    /// <paramref name="destinationDir"/> inside the current project's Assets.</summary>
    public static CopyResult CopyUnits(string sourceRoot, IEnumerable<(string Source, string Dest)> unitNames, string destinationDir)
    {
        var result = new CopyResult();
        var guidMap = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
        var animEvents = new Dictionary<string, Dictionary<string, ClipMeta>>(StringComparer.OrdinalIgnoreCase);

        Directory.CreateDirectory(destinationDir);
        foreach ((string source, string dest) in unitNames)
        {
            string unitDir = Path.Combine(sourceRoot, source);
            if (!Directory.Exists(unitDir))
            {
                result.Warnings.Add($"Unit folder not found: {unitDir}");
                continue;
            }
            CopyDirectory(unitDir, Path.Combine(destinationDir, dest), destinationDir, result, guidMap, animEvents);
        }

        string mapPath = Path.Combine(destinationDir, UnityGuidMap.MapFileName);
        File.WriteAllText(mapPath, JsonSerializer.Serialize(guidMap, new JsonSerializerOptions { WriteIndented = true }));
        UnityGuidMap.InvalidateCache();

        File.WriteAllText(Path.Combine(destinationDir, AnimEventsFileName),
            JsonSerializer.Serialize(animEvents, new JsonSerializerOptions { WriteIndented = true }));
        return result;
    }

    private static void CopyDirectory(string source, string destination, string mapRoot, CopyResult result,
        Dictionary<string, string> guidMap, Dictionary<string, Dictionary<string, ClipMeta>> animEvents)
    {
        Directory.CreateDirectory(destination);

        foreach (string filePath in Directory.GetFiles(source))
        {
            string extension = Path.GetExtension(filePath).ToLowerInvariant();
            bool isTexture = Array.IndexOf(s_textureExtensions, extension) >= 0;
            bool isModel = Array.IndexOf(s_modelExtensions, extension) >= 0;
            if (!isTexture && !isModel)
                continue; // Unity .prefab/.mat/.controller are generated natively, not copied;
                          // .meta / .asset / .unity / csproj are not part of the runtime demo

            string destFile = Path.Combine(destination, Path.GetFileName(filePath));
            File.Copy(filePath, destFile, overwrite: true);
            result.FilesCopied++;

            string? unityGuid = ReadUnityGuid(filePath + ".meta");
            if (unityGuid != null)
                guidMap[unityGuid] = Path.GetRelativePath(mapRoot, destFile).Replace('\\', '/');

            WriteEngineMeta(destFile, isTexture, isModel, filePath + ".meta", unityGuid, result);

            if (isModel)
            {
                string relPath = Path.GetRelativePath(mapRoot, destFile).Replace('\\', '/');
                Dictionary<string, ClipMeta>? clips = ReadClipMeta(filePath + ".meta");
                if (clips is { Count: > 0 })
                {
                    animEvents[relPath] = clips;
                    foreach (var clip in clips.Values)
                        if (clip.Events.Count > 0) result.ClipsWithEvents++;
                }
            }
        }

        foreach (string directory in Directory.GetDirectories(source))
            CopyDirectory(directory, Path.Combine(destination, Path.GetFileName(directory)), mapRoot,
                result, guidMap, animEvents);
    }

    public static string? ReadUnityGuid(string metaPath)
    {
        if (!File.Exists(metaPath)) return null;
        using var stream = new FileStream(metaPath, FileMode.Open, FileAccess.Read, FileShare.Read);
        var buffer = new byte[256];
        int read = stream.Read(buffer, 0, buffer.Length);
        Match match = GuidRegex().Match(System.Text.Encoding.ASCII.GetString(buffer, 0, read));
        return match.Success ? match.Groups[1].Value : null;
    }

    private static void WriteEngineMeta(string destFile, bool isTexture, bool isModel, string unityMetaPath,
        string? unityGuid, CopyResult result)
    {
        string importerName = isTexture ? "TextureImporter" : ImporterNameFor(Path.GetExtension(destFile));
        EditorRegistries.Initialize();
        var importer = EditorRegistries.CreateImporterByName(importerName);
        EchoObject? settings = null;
        if (isTexture)
        {
            settings = UnityTextureMetaAligner.AlignSettings(unityMetaPath, result.Warnings);
            result.TexturesAligned++;
        }
        else if (isModel)
        {
            settings = importer?.DefaultSettings()?.Clone();
            EchoObject? scaleSettings = ReadModelImporterSettings(unityMetaPath);
            if (scaleSettings != null)
            {
                if (settings == null)
                {
                    settings = scaleSettings;
                }
                else
                {
                    foreach (var kvp in scaleSettings.Tags)
                        settings[kvp.Key] = kvp.Value.Clone();
                }
            }
        }

        var meta = new MetaFileData
        {
            Guid = unityGuid != null ? StableGuid(unityGuid) : Guid.NewGuid(),
            ImporterType = importerName,
            ImporterVersion = importer?.Version ?? 1,
            Settings = settings,
        };
        MetaFile.Write(MetaFile.GetMetaPath(destFile), meta);
    }

    /// <summary>Unity ModelImporter.meshes.globalScale (or ModelImporter.globalScale) → engine
    /// <c>unitScale</c>. ZZZ character FBXs use globalScale 100 so Unity's cm→m conversion is a
    /// no-op; Clay already converts cm→m, so the same 100 must be applied as UnitScale or the
    /// skinned meshes come out ~1.5 cm tall.</summary>
    public static EchoObject? ReadModelImporterSettings(string unityMetaPath)
    {
        try
        {
            if (!File.Exists(unityMetaPath)) return null;
            UnityYamlNode root = UnityYaml.ParsePlainFile(unityMetaPath);
            UnityYamlNode? importer = root["ModelImporter"];
            if (importer?.Map == null) return null;
            float scale = importer["meshes"]?.FloatOf("globalScale", 1f)
                          ?? importer.FloatOf("globalScale", 1f);
            if (scale <= 0f) scale = 1f;
            var settings = EchoObject.NewCompound();
            settings["unitScale"] = new EchoObject(scale);
            return settings;
        }
        catch
        {
            return null;
        }
    }

    /// <summary>Deterministic engine GUID for a Unity source guid (MD5-derived, stable across
    /// machines and runs; separate hash domain from the genshin copier).</summary>
    public static Guid StableGuid(string unityGuid)
    {
        byte[] hash = System.Security.Cryptography.MD5.HashData(
            System.Text.Encoding.UTF8.GetBytes("xengine:zonezero:unity-guid:" + unityGuid));
        return new Guid(hash);
    }

    private static string ImporterNameFor(string extension) => extension.ToLowerInvariant() switch
    {
        ".fbx" => "EditorModelImporter",
        _ => "DefaultImporter",
    };

    /// <summary>Reads clipAnimations (name/loopTime/events) from a Unity FBX .meta
    /// (headerless YAML parsed by UnityYaml.ParsePlainFile).</summary>
    public static Dictionary<string, ClipMeta>? ReadClipMeta(string metaPath)
    {
        try
        {
            if (!File.Exists(metaPath)) return null;
            UnityYamlNode root = UnityYaml.ParsePlainFile(metaPath);
            UnityYamlNode? importer = root["ModelImporter"];
            // Unity 6 ModelImporter stores clipAnimations under animations:; older / fixture
            // metas put the list directly on ModelImporter.
            List<UnityYamlNode>? clipList = importer?["clipAnimations"]?.List
                ?? importer?["animations"]?["clipAnimations"]?.List;
            if (clipList is not { Count: > 0 }) return null;

            var clips = new Dictionary<string, ClipMeta>(StringComparer.OrdinalIgnoreCase);
            foreach (UnityYamlNode clipNode in clipList)
            {
                string? name = clipNode.ScalarOf("name");
                if (string.IsNullOrEmpty(name)) continue;
                var meta = new ClipMeta
                {
                    Loop = clipNode.ScalarOf("loopTime") == "1",
                };
                foreach (UnityYamlNode ev in clipNode["events"]?.List ?? new List<UnityYamlNode>())
                {
                    meta.Events.Add(new ClipEventEntry
                    {
                        Time = ev.FloatOf("time"),
                        Function = ev.ScalarOf("functionName") ?? string.Empty,
                        FloatParam = ev.FloatOf("floatParameter"),
                        IntParam = (int)ev.LongOf("intParameter"),
                        Data = ev.ScalarOf("data") ?? string.Empty,
                    });
                }
                clips[name] = meta;
            }
            return clips;
        }
        catch
        {
            return null;
        }
    }
}
