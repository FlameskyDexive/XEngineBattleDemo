using System;
using System.Collections.Generic;
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

        // Unity states (class 1102): name + motion guid.
        var states = new List<(string Name, string MotionGuid)>();
        string controllerName = Path.GetFileNameWithoutExtension(sourceControllerPath);
        foreach (var doc in documents)
        {
            if (doc.ClassId != 91) continue;
            string? name = doc.Root.ScalarOf("m_Name");
            if (!string.IsNullOrEmpty(name)) controllerName = name;
        }
        foreach (var doc in documents)
        {
            if (doc.ClassId != 1102) continue;
            string? name = doc.Root.ScalarOf("m_Name");
            if (string.IsNullOrEmpty(name)) continue;
            string? motionGuid = doc.Root["m_Motion"]?.ScalarOf("guid");
            states.Add((name, motionGuid ?? string.Empty));
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
        foreach ((string name, string motionGuid) in states)
        {
            var state = new AnimatorController.State
            {
                Name = name,
                IsLooping = IsLooping(name, motionGuid, guidMap, animEvents),
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
