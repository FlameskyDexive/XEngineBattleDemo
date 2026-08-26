using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Text.Json;

using XEngine.Animation;
using XEngine.Editor;
using XEngine.Runtime;

namespace XEngine.Zonezero.Editor;

/// <summary>
/// Builds a native engine <see cref="AnimatorController"/> from a Unity .controller YAML file read
/// in place from the ZZZ reference project (the Unity file is never imported): one state per Unity
/// AnimatorState, motion resolved to the AnimationClip sub-asset of the copied+imported
/// <c>Char@ClipName.FBX</c>, loop flags from the unity-anim-events sidecar. Transitions are
/// intentionally dropped — the ZZZ demo drives everything through code CrossFade, so a
/// transition-less state list is behavior-complete. The result is written to disk as a native
/// Echo .controller by <see cref="ZonezeroNativeAssets"/>.
/// </summary>
public static class ZonezeroControllerGenerator
{
    /// <summary>Parses the Unity controller YAML and resolves motions against the imported FBX
    /// entries. Returns null (with warnings) when the file has no states.</summary>
    public static AnimatorController? BuildFromUnityYaml(string sourceControllerPath, string destRelativeRoot,
        Dictionary<string, string> guidMap,
        Dictionary<string, Dictionary<string, ZonezeroAssetCopier.ClipMeta>>? animEvents,
        EditorAssetBackend backend, List<string> warnings)
    {
        List<UnityImporter.Editor.UnityYamlDocument> documents =
            UnityImporter.Editor.UnityYaml.ParseFile(sourceControllerPath);

        // Unity states (class 1102): name + motion guid + authored Animator-Window position.
        var states = new List<(string Name, string MotionGuid, float PosX, float PosY)>();
        string controllerName = Path.GetFileNameWithoutExtension(sourceControllerPath);
        foreach (var doc in documents)
        {
            if (doc.ClassId != 91) continue;
            string? name = doc.Root.ScalarOf("m_Name");
            if (!string.IsNullOrEmpty(name)) controllerName = name;
        }
        int layoutIndex = 0;
        foreach (var doc in documents)
        {
            if (doc.ClassId != 1102) continue;
            string? name = doc.Root.ScalarOf("m_Name");
            if (string.IsNullOrEmpty(name)) continue;
            string? motionGuid = doc.Root["m_Motion"]?.ScalarOf("guid");
            // Unity's authored canvas position when present (m_Position), else a category grid so
            // states never stack at the origin in the Animator Window.
            float px, py;
            var pos = doc.Root["m_Position"];
            if (pos != null &&
                float.TryParse(pos.ScalarOf("x"), NumberStyles.Float, CultureInfo.InvariantCulture, out px) &&
                float.TryParse(pos.ScalarOf("y"), NumberStyles.Float, CultureInfo.InvariantCulture, out py))
            {
                // authored position
            }
            else
            {
                (px, py) = LayoutPosition(name!, layoutIndex++);
            }
            states.Add((name!, motionGuid ?? string.Empty, px, py));
        }
        if (states.Count == 0)
        {
            warnings.Add($"Controller '{controllerName}': no AnimatorState documents.");
            return null;
        }

        // Default state: the state machine's (class 1107) m_DefaultState fileID if present,
        // else "Idle", else the first state.
        string defaultState = string.Empty;
        foreach (var doc in documents)
        {
            if (doc.ClassId != 1107) continue;
            long defaultId = doc.Root["m_DefaultState"]?.LongOf("fileID") ?? 0;
            if (defaultId == 0) continue;
            foreach (var stateDoc in documents)
            {
                if (stateDoc.ClassId != 1102 || stateDoc.FileId != defaultId) continue;
                defaultState = stateDoc.Root.ScalarOf("m_Name") ?? string.Empty;
                break;
            }
            if (defaultState.Length > 0) break;
        }
        if (defaultState.Length == 0)
        {
            foreach (var s in states)
                if (s.Name == "Idle") { defaultState = s.Name; break; }
        }
        if (defaultState.Length == 0)
            defaultState = states[0].Name;

        var controller = new AnimatorController { Name = controllerName };
        int resolved = 0;
        foreach ((string name, string motionGuid, float posX, float posY) in states)
        {
            var state = new AnimatorController.State
            {
                Name = name,
                IsLooping = IsLooping(name, motionGuid, guidMap, animEvents),
                PosX = posX,
                PosY = posY,
            };

            if (motionGuid.Length > 0)
            {
                Guid clipGuid = ResolveClipGuid(motionGuid, name, destRelativeRoot, guidMap, backend);
                if (clipGuid != Guid.Empty)
                {
                    state.Motion = new AssetRef<AnimationClip>(clipGuid);
                    resolved++;
                }
                else
                {
                    warnings.Add($"Controller '{controllerName}': state '{name}' motion guid {motionGuid} " +
                                 "could not be resolved to an imported FBX AnimationClip — state left empty.");
                }
            }
            controller.States.Add(state);
        }
        controller.DefaultStateName = defaultState;
        Debug.Log($"[Zonezero] Generated native controller '{controllerName}': {controller.States.Count} states, " +
                  $"{resolved} motions resolved, default '{defaultState}'.");
        return controller;
    }

    /// <summary>Fallback canvas layout for states whose Unity .controller carries no authored
    /// m_Position (or none at all): Idle center, locomotion right, combo attacks left in step
    /// rows, evades below idle, big-skill chain far left, tag-switch far right, unknowns on a
    /// spill grid — so the Animator Window never draws everything stacked at the origin.</summary>
    private static (float X, float Y) LayoutPosition(string name, int index)
    {
        if (name == "Idle") return (0f, 0f);
        if (name == "Run") return (420f, 0f);
        if (name.StartsWith("Run_End", StringComparison.Ordinal)) return (420f, 200f);
        if (name == "TurnBack") return (420f, -200f);
        if (name == "Evade_Front") return (0f, 240f);
        if (name == "Evade_Back") return (0f, 420f);
        if (name.StartsWith("Evade", StringComparison.Ordinal))
            return (260f, name.Contains("Front") ? 240f : 420f);
        if (name.StartsWith("Attack_Normal_", StringComparison.Ordinal))
        {
            bool end = name.EndsWith("_End", StringComparison.Ordinal);
            string number = name.Substring("Attack_Normal_".Length,
                name.Length - "Attack_Normal_".Length - (end ? 4 : 0));
            int step = int.TryParse(number, out int v) ? v : 1;
            return (end ? -180f : -420f, (step - 1) * 220f - 300f);
        }
        if (name.StartsWith("BigSkill", StringComparison.Ordinal))
            return (-820f, name.EndsWith("Start", StringComparison.Ordinal) ? -320f
                : name.EndsWith("End", StringComparison.Ordinal) ? 160f : -80f);
        if (name.StartsWith("Switch", StringComparison.Ordinal)) return (820f, -120f);
        return (1000f + index % 4 * 220f, 400f + index / 4 * 180f);
    }

    /// <summary>Unity motion guid → AnimationClip sub-asset guid of the copied+imported FBX. The
    /// clip is matched by state name first (ripped assets keep clip name == state name), falling
    /// back to the file's only AnimationClip.</summary>
    private static Guid ResolveClipGuid(string unityGuid, string stateName, string destRelativeRoot,
        Dictionary<string, string> guidMap, EditorAssetBackend backend)
    {
        if (!guidMap.TryGetValue(unityGuid, out string? relInsideZzz)) return Guid.Empty;
        var entry = backend.GetEntry($"{destRelativeRoot}/{relInsideZzz}");
        if (entry?.SubAssets is not { Length: > 0 }) return Guid.Empty;

        Guid fallback = Guid.Empty;
        foreach (var sub in entry.SubAssets)
        {
            if (sub.Type != typeof(AnimationClip)) continue;
            if (string.Equals(sub.Name, stateName, StringComparison.OrdinalIgnoreCase))
                return sub.Guid;
            fallback = fallback == Guid.Empty ? sub.Guid : fallback;
        }
        return fallback;
    }

    /// <summary>Loop flag for a state: unity-anim-events sidecar (copied from the FBX .meta
    /// clipAnimations loopTime) when available, else Idle/Run naming heuristics.</summary>
    private static bool IsLooping(string stateName, string motionGuid, Dictionary<string, string> guidMap,
        Dictionary<string, Dictionary<string, ZonezeroAssetCopier.ClipMeta>>? animEvents)
    {
        if (animEvents != null && motionGuid.Length > 0 && guidMap.TryGetValue(motionGuid, out string? rel)
            && animEvents.TryGetValue(rel, out Dictionary<string, ZonezeroAssetCopier.ClipMeta>? clips))
        {
            foreach (var clip in clips)
            {
                if (string.Equals(clip.Key, stateName, StringComparison.OrdinalIgnoreCase))
                    return clip.Value.Loop;
            }
            // Single-clip FBX: any entry applies.
            if (clips.Count == 1)
            {
                foreach (var clip in clips.Values)
                    return clip.Loop;
            }
        }
        // Heuristic fallback: locomotion states loop, one-shots (attacks/dodge/skills/switch) don't.
        return stateName is "Idle" or "Run" || stateName.StartsWith("Run", StringComparison.OrdinalIgnoreCase);
    }

    /// <summary>Reads the unity-anim-events.json sidecar written by
    /// <see cref="ZonezeroAssetCopier"/> from the destination ZZZ directory.</summary>
    public static Dictionary<string, Dictionary<string, ZonezeroAssetCopier.ClipMeta>>? LoadAnimEventsSidecar(
        string destinationDir)
    {
        string sidecar = Path.Combine(destinationDir, ZonezeroAssetCopier.AnimEventsFileName);
        if (!File.Exists(sidecar)) return null;
        try
        {
            return JsonSerializer.Deserialize<Dictionary<string, Dictionary<string, ZonezeroAssetCopier.ClipMeta>>>(
                File.ReadAllText(sidecar));
        }
        catch (Exception ex)
        {
            Debug.LogWarning($"[Zonezero] Failed to read '{sidecar}': {ex.Message}");
            return null;
        }
    }
}
