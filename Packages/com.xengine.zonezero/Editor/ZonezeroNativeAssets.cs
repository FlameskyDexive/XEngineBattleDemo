using System;
using System.Collections.Generic;
using System.IO;
using System.Text.Json;

using XEngine.Animation;
using XEngine.Echo;
using XEngine.Editor;
using XEngine.Editor.Projects;
using XEngine.Runtime;
using XEngine.Runtime.Resources;
using XEngine.UnityImporter.Editor;
using XEngine.Vector;


namespace XEngine.Zonezero.Editor;

/// <summary>
/// Generates NATIVE engine assets (.mat / .controller / .prefab, Echo text) from the ZZZ Unity
/// reference project. Unity's own .mat/.controller/.prefab files are never copied into the
/// project — only raw FBX + textures are imported (see <see cref="ZonezeroAssetCopier"/>); the
/// Unity files are read in place as a *reference answer sheet*:
///
///  - Unity .mat  → native <see cref="Material"/> (Standard, or Default/Unlit for URP Unlit),
///    diffuse wired to the copied texture's deterministic engine guid.
///  - Unity .controller → native <see cref="AnimatorController"/> file: one state per Unity
///    AnimatorState, motion resolved to the imported FBX's AnimationClip sub-asset, loop flags
///    from the unity-anim-events sidecar. Transitions are dropped (demo drives code CrossFade).
///  - Unity .prefab → the engine FBX <see cref="Model"/> is instantiated (the proven-correct
///    axis/skinning path) and only the *bindings* are taken from Unity: material slots per
///    skinned node and the Animator's controller. The tree is saved as a native .prefab.
///
/// Generated assets reuse <see cref="ZonezeroAssetCopier.StableGuid"/> of their Unity source
/// guid, so regeneration is idempotent and cross-references stay stable.
/// </summary>
public static class ZonezeroNativeAssets
{
    public sealed class GenerateResult
    {
        public int Materials;
        public int Controllers;
        public int Prefabs;
        public List<string> Warnings = new();
    }

    /// <summary>Runs all three generators against the Unity source tree. FBX/texture copies must
    /// already be imported (Zonezero/Copy ZZZ Assets Into Project).</summary>
    public static GenerateResult GenerateAll(string sourceAssetsRoot, IEnumerable<string> unitNames,
        string destinationDir, string destRelativeRoot, EditorAssetBackend backend)
    {
        var result = new GenerateResult();
        Dictionary<string, string> guidMap = LoadGuidMap(destinationDir, result.Warnings);
        var animEvents = ZonezeroControllerGenerator.LoadAnimEventsSidecar(destinationDir);

        foreach (string unitName in unitNames)
        {
            string unitDir = Path.Combine(sourceAssetsRoot, unitName);
            if (!Directory.Exists(unitDir)) continue;

            foreach (string matPath in Directory.GetFiles(unitDir, "*.mat", SearchOption.AllDirectories))
                GenerateMaterial(matPath, sourceAssetsRoot, destinationDir, guidMap, result);

            foreach (string controllerPath in Directory.GetFiles(unitDir, "*.controller", SearchOption.AllDirectories))
                GenerateController(controllerPath, sourceAssetsRoot, destinationDir, destRelativeRoot,
                    guidMap, animEvents, backend, result);
        }
        return result;
    }

    // ================================================================
    //  Materials
    // ================================================================

    /// <summary>Reads a Unity .mat as reference and writes the equivalent native Echo .mat at the
    /// mirrored destination path with a deterministic guid.</summary>
    public static void GenerateMaterial(string sourceMatPath, string sourceAssetsRoot, string destinationDir,
        Dictionary<string, string> guidMap, GenerateResult result)
    {
        try
        {
            if (!UnityAssetSniffer.IsUnityYaml(sourceMatPath)) return;
            Material? material = BuildMaterialFromUnityYaml(sourceMatPath, guidMap, result.Warnings);
            if (material == null) return;

            string? unityGuid = ZonezeroAssetCopier.ReadUnityGuid(sourceMatPath + ".meta");
            Guid guid = unityGuid != null ? ZonezeroAssetCopier.StableGuid(unityGuid) : Guid.NewGuid();
            string destPath = Path.Combine(destinationDir, Path.GetRelativePath(sourceAssetsRoot, sourceMatPath));
            WriteNativeAsset(material, destPath, guid, "UnityMatImporter");
            result.Materials++;
        }
        catch (Exception ex)
        {
            result.Warnings.Add($"Material '{sourceMatPath}': {ex.Message}");
        }
    }

    /// <summary>Unity Material YAML → native Material. Shader: URP Unlit guid → Default/Unlit,
    /// YSA Toon Lit guid → Default/Toon (M2: banded lights/shadows/ambient/gradient/alpha clip
    /// mapped below), everything else → Default/Standard placeholder. Diffuse
    /// (_BaseMap/_MainTex) → copied texture's deterministic engine guid.</summary>
    public static Material? BuildMaterialFromUnityYaml(string unityMatPath,
        Dictionary<string, string> guidMap, List<string> warnings)
    {
        List<UnityYamlDocument> documents = UnityYaml.ParseFile(unityMatPath);
        UnityYamlDocument? doc = null;
        foreach (UnityYamlDocument candidate in documents)
            if (candidate.ClassId == 21) { doc = candidate; break; }
        if (doc == null)
        {
            warnings.Add($"'{unityMatPath}': no Material document.");
            return null;
        }
        UnityYamlNode root = doc.Root;

        string? shaderGuid = root["m_Shader"]?.ScalarOf("guid");
        bool unlit = string.Equals(shaderGuid, UnityMatImporter.UrpUnlitShaderGuid, StringComparison.OrdinalIgnoreCase);
        bool ysaToon = string.Equals(shaderGuid, YsaToonLitShaderGuid, StringComparison.OrdinalIgnoreCase);
        bool ysaOutline = string.Equals(shaderGuid, YsaOutlineShaderGuid, StringComparison.OrdinalIgnoreCase);
        var material = new Material(ResolveShader(unlit, ysaToon, ysaOutline));
        material.Name = root.ScalarOf("m_Name") is { Length: > 0 } name
            ? name
            : Path.GetFileNameWithoutExtension(unityMatPath);

        string? diffuseGuid = FindTextureGuid(root, "_BaseMap") ?? FindTextureGuid(root, "_MainTex");
        if (diffuseGuid != null)
        {
            if (!guidMap.ContainsKey(diffuseGuid))
                warnings.Add($"Material '{material.Name}': diffuse texture guid {diffuseGuid} was not copied " +
                             "(missing from unity-guid-map.json) — reference may dangle.");
            material.SetTexture("_MainTex", new AssetRef<Texture2D>(ZonezeroAssetCopier.StableGuid(diffuseGuid)));
        }
        if (ysaToon)
            ApplyYsaToonProperties(material, root, guidMap);
        else if (ysaOutline)
            ApplyYsaOutlineProperties(material, root);
        return material;
    }

    /// <summary>Resolves a YSA material's shader. Toon/ToonOutline are game-owned project shaders
    /// (resolved by their preserved GUID via the asset database); Unlit/Standard remain engine
    /// built-ins. Falls back to Standard when the project shader isn't present (e.g. headless
    /// unit tests without an imported project).</summary>
    private static Shader ResolveShader(bool unlit, bool ysaToon, bool ysaOutline)
    {
        if (unlit)
            return Shader.LoadDefault(DefaultShader.Unlit);
        if (ysaToon && AssetDatabase.Get(ToonShaderGuid) is Shader toon)
            return toon;
        if (ysaOutline && AssetDatabase.Get(ToonOutlineShaderGuid) is Shader toonOutline)
            return toonOutline;
        return Shader.LoadDefault(DefaultShader.Standard);
    }

    /// <summary>"Toon Lit.shadergraph" guid from the YSA Toon plugin (ScriptedImporter meta).</summary>
    private const string YsaToonLitShaderGuid = "b3a5ca620cf906445a708f4f5731a19d";

    /// <summary>"Perfect Outline.shadergraph" guid (YSA inverted-hull outline).</summary>
    private const string YsaOutlineShaderGuid = "f2c8216f97326fd4baf6a7828f7c29be";

    /// <summary>Game-owned "Zonezero/Toon" shader guid (preserved from the former engine built-in
    /// <c>Default/Toon</c> so existing materials referencing it keep resolving).</summary>
    private static readonly Guid ToonShaderGuid = new("24fb7ce8-7a2b-f354-8fb6-cc00130c521a");

    /// <summary>Game-owned "Zonezero/ToonOutline" shader guid (preserved from the former engine
    /// built-in <c>Default/ToonOutline</c>).</summary>
    private static readonly Guid ToonOutlineShaderGuid = new("d7af2277-7f05-3a57-b4ea-216e25ad79c6");

    /// <summary>Maps a YSA "Perfect Outline" material onto Default/ToonOutline.</summary>
    private static void ApplyYsaOutlineProperties(Material material, UnityYamlNode root)
    {
        CopyYsaColor(material, root, "_OutlineColor", "_OutlineColor");
        CopyYsaFloat(material, root, "_OutlineWidth", "_OutlineWidth");
        // Typo'd in the YSA graph ("_Vertex_Color_Normlas") — kept verbatim as serialized.
        CopyYsaFloat(material, root, "_Vertex_Color_Normlas", "_VertexColorNormals");
    }

    /// <summary>Copies the YSA Toon Lit property subset the M2 engine toon shader implements
    /// (Default/Toon). YSA names map onto engine names; unsaved entries keep the shader's
    /// PropertyArray defaults. The URP _AlphaClip toggle gates the _Alpha_Clipping threshold.</summary>
    private static void ApplyYsaToonProperties(Material material, UnityYamlNode root,
        Dictionary<string, string> guidMap)
    {
        CopyYsaColor(material, root, "_Ambient", "_Ambient");
        CopyYsaColor(material, root, "_BaseColor", "_MainColor");
        CopyYsaColor(material, root, "_GColor_1", "_GColor1");
        CopyYsaColor(material, root, "_GColor_2", "_GColor2");

        CopyYsaFloat(material, root, "_MainLightMidPoint", "_MainLightMidPoint");
        CopyYsaFloat(material, root, "_MainLightSmoothness", "_MainLightSmoothness");
        CopyYsaFloat(material, root, "_ReceiveShadowsEnabled", "_ReceiveShadowsEnabled");
        CopyYsaFloat(material, root, "_MainShadowsPower", "_MainShadowsPower");
        CopyYsaFloat(material, root, "_Shadow_Smoothness", "_ShadowSmoothness");
        CopyYsaFloat(material, root, "_OcclusionPower", "_OcclusionPower");
        CopyYsaFloat(material, root, "_AdditionalLightsEnabled", "_AdditionalLightsEnabled");
        CopyYsaFloat(material, root, "_AdditionalLightsMidPoint", "_AdditionalLightsMidPoint");
        // Typo'd in the YSA shader graph ("Smothness") — kept verbatim as the serialized name.
        CopyYsaFloat(material, root, "_AdditionalLightsSmothness", "_AdditionalLightsSmoothness");
        CopyYsaFloat(material, root, "_AdditionalLightShadows", "_AdditionalLightShadows");
        CopyYsaFloat(material, root, "_AdditionalShadowsPower", "_AdditionalShadowsPower");
        CopyYsaFloat(material, root, "_Gradient_Enabled", "_GradientEnabled");
        CopyYsaFloat(material, root, "_Gradient_Multiplier", "_GradientMultiplier");
        CopyYsaFloat(material, root, "_Gradient_Offset", "_GradientOffset");
        CopyYsaFloat(material, root, "_Inverse_Colors", "_InverseColors");

        // --- M3 tail: rim / H-offset specular / emission / face normals / shrink.
        CopyYsaColor(material, root, "_RimColor", "_RimColor");
        CopyYsaFloat(material, root, "_Rim_Enabled", "_RimEnabled");
        CopyYsaFloat(material, root, "_RimMidPoint", "_RimMidPoint");
        CopyYsaFloat(material, root, "_RimSmoothness", "_RimSmoothness");
        CopyYsaFloat(material, root, "_DynamicRemap", "_DynamicRemap");
        CopyYsaFloat(material, root, "_RimHideOnShadow", "_RimHideOnShadow");
        CopyYsaFloat(material, root, "_MainLightSpecular", "_MainLightSpecular");
        CopyYsaFloat(material, root, "_AdditionalLightsSpecular", "_AdditionalLightsSpecular");
        CopyYsaFloat(material, root, "_SpecularMapPower", "_SpecularMapPower");
        CopyYsaFloat(material, root, "_SpecularMidPoint", "_SpecularMidPoint");
        CopyYsaFloat(material, root, "_SpecularSmoothness", "_SpecularSmoothness");
        CopyYsaColor(material, root, "_SpecularTint", "_SpecularTint");
        CopyYsaFloat(material, root, "_Specular_Customize_Enabled", "_SpecularCustomizeEnabled");
        CopyYsaFloat(material, root, "_Specular_Hide_On_Shadows", "_SpecularHideOnShadows");
        CopyYsaColor(material, root, "_Specular_Texture_Color", "_SpecularTextureColor");
        CopyYsaFloat(material, root, "_Emission", "_Emission");
        CopyYsaColor(material, root, "_EmissionColor", "_EmissionColor");
        CopyYsaFloat(material, root, "_is_Face", "_IsFace");
        CopyYsaFloat(material, root, "_Shrink_Size", "_ShrinkSize");
        // Face axis params: modifier / pos multiplier / offset packs into one engine Vector4.
        SetFaceVector(material, root, "_X_Modifier", "_X_Pos_Multiplier", "_X_Offset", "_FaceX");
        SetFaceVector(material, root, "_Y_Modifier", "_Y_Pos_Multiplier", "_Y_Offset", "_FaceY");
        SetFaceVector(material, root, "_Z_Modifier", "_Z_Pos_Multiplier", "_Z_Offset", "_FaceZ");
        // Specular textures (glossiness bias + customize color source).
        string? specularGuid = FindTextureGuid(root, "_SpecularMap");
        if (specularGuid != null && guidMap.ContainsKey(specularGuid))
            material.SetTexture("_SpecularMap", new AssetRef<Texture2D>(ZonezeroAssetCopier.StableGuid(specularGuid)));
        string? specularTexGuid = FindTextureGuid(root, "_Specular_Texture");
        if (specularTexGuid != null && guidMap.ContainsKey(specularTexGuid))
            material.SetTexture("_Specular_Texture", new AssetRef<Texture2D>(ZonezeroAssetCopier.StableGuid(specularTexGuid)));
        string? emissionGuid = FindTextureGuid(root, "_EmissionMap");
        if (emissionGuid != null && guidMap.ContainsKey(emissionGuid))
            material.SetTexture("_EmissionTex", new AssetRef<Texture2D>(ZonezeroAssetCopier.StableGuid(emissionGuid)));

        // BaseMapTiling serializes as a color node {r,g} — Vector2 tiling in the engine.
        UnityYamlNode? tiling = FindYsaEntry(root["m_SavedProperties"]?["m_Colors"]?.List, "_BaseMapTiling");
        if (tiling != null)
            material.SetVector("_Tiling", new Float2(tiling.FloatOf("r", 1f), tiling.FloatOf("g", 1f)));

        // Alpha clip only exists when the URP surface option enabled it; otherwise 0 = never discard.
        UnityYamlNode? alphaClip = FindYsaEntry(root["m_SavedProperties"]?["m_Floats"]?.List, "_AlphaClip");
        UnityYamlNode? threshold = FindYsaEntry(root["m_SavedProperties"]?["m_Floats"]?.List, "_Alpha_Clipping");
        bool clipEnabled = alphaClip != null && alphaClip.Scalar is { } enabled && enabled != "0";
        material.SetFloat("_AlphaCutoff", clipEnabled ? ParseScalar(threshold, 0.5f) : 0f);

        string? occlusionGuid = FindTextureGuid(root, "_OcclusionMap");
        if (occlusionGuid != null && guidMap.ContainsKey(occlusionGuid))
            material.SetTexture("_OcclusionMap", new AssetRef<Texture2D>(ZonezeroAssetCopier.StableGuid(occlusionGuid)));

        // Gradient axis: YSA keyword enum _GRADIENT_DIRECTION_{X,Y,Z} → engine float (0/1/2, Y default).
        List<UnityYamlNode>? keywords = root["m_ValidKeywords"]?.List;
        if (keywords != null)
        {
            foreach (UnityYamlNode keyword in keywords)
            {
                switch (keyword.Scalar)
                {
                    case "_GRADIENT_DIRECTION_X": material.SetFloat("_GradientDirection", 0f); break;
                    case "_GRADIENT_DIRECTION_Y": material.SetFloat("_GradientDirection", 1f); break;
                    case "_GRADIENT_DIRECTION_Z": material.SetFloat("_GradientDirection", 2f); break;
                }
            }
        }
    }

    private static void CopyYsaColor(Material material, UnityYamlNode root, string ysaName, string engineName)
    {
        UnityYamlNode? entry = FindYsaEntry(root["m_SavedProperties"]?["m_Colors"]?.List, ysaName);
        if (entry == null) return;
        material.SetColor(engineName, new Color(
            ParseScalar(entry, 0f, "r"), ParseScalar(entry, 0f, "g"),
            ParseScalar(entry, 0f, "b"), ParseScalar(entry, 1f, "a")));
    }

    private static void CopyYsaFloat(Material material, UnityYamlNode root, string ysaName, string engineName)
    {
        UnityYamlNode? entry = FindYsaEntry(root["m_SavedProperties"]?["m_Floats"]?.List, ysaName);
        if (entry != null)
            material.SetFloat(engineName, ParseScalar(entry, 0f));
    }

    /// <summary>Packs the three YSA face-normal scalars of one axis into the engine's packed
    /// Vector4 (x=modifier, y=posMultiplier, z=offset); missing entries fall back to the
    /// shader default (multiplier 10).</summary>
    private static void SetFaceVector(Material material, UnityYamlNode root,
        string modifierName, string multiplierName, string offsetName, string engineName)
    {
        List<UnityYamlNode>? floats = root["m_SavedProperties"]?["m_Floats"]?.List;
        UnityYamlNode? modifier = FindYsaEntry(floats, modifierName);
        UnityYamlNode? multiplier = FindYsaEntry(floats, multiplierName);
        UnityYamlNode? offset = FindYsaEntry(floats, offsetName);
        if (modifier == null && multiplier == null && offset == null) return;
        material.SetVector(engineName, new Float4(
            ParseScalar(modifier, 0f),
            ParseScalar(multiplier, 10f),
            ParseScalar(offset, 0f),
            0f));
    }

    /// <summary>m_Floats/m_Colors serialize as a list of single-entry maps (name → scalar/map).</summary>
    private static UnityYamlNode? FindYsaEntry(List<UnityYamlNode>? entries, string name)
    {
        if (entries == null) return null;
        foreach (UnityYamlNode entry in entries)
        {
            if (entry.Map == null || !entry.Map.ContainsKey(name)) continue;
            UnityYamlNode value = entry[name]!;
            return value;
        }
        return null;
    }

    private static float ParseScalar(UnityYamlNode? node, float fallback, string key = "value")
    {
        string? scalar = node?.Map != null ? node.ScalarOf(key) : node?.Scalar;
        return float.TryParse(scalar, System.Globalization.CultureInfo.InvariantCulture, out float value) ? value : fallback;
    }

    private static string? FindTextureGuid(UnityYamlNode root, string propertyName)
    {
        List<UnityYamlNode>? entries = root["m_SavedProperties"]?["m_TexEnvs"]?.List;
        if (entries == null) return null;
        foreach (UnityYamlNode entry in entries)
        {
            if (entry.Map == null || !entry.Map.ContainsKey(propertyName)) continue;
            string? guid = entry[propertyName]?["m_Texture"]?.ScalarOf("guid");
            if (!string.IsNullOrEmpty(guid)) return guid;
        }
        return null;
    }

    // ================================================================
    //  Controllers
    // ================================================================

    private static void GenerateController(string sourceControllerPath, string sourceAssetsRoot,
        string destinationDir, string destRelativeRoot, Dictionary<string, string> guidMap,
        Dictionary<string, Dictionary<string, ZonezeroAssetCopier.ClipMeta>>? animEvents,
        EditorAssetBackend backend, GenerateResult result)
    {
        try
        {
            if (!UnityAssetSniffer.IsUnityYaml(sourceControllerPath)) return;
            AnimatorController? controller = ZonezeroControllerGenerator.BuildFromUnityYaml(
                sourceControllerPath, destRelativeRoot, guidMap, animEvents, backend, result.Warnings);
            if (controller == null) return;

            string? unityGuid = ZonezeroAssetCopier.ReadUnityGuid(sourceControllerPath + ".meta");
            Guid guid = unityGuid != null ? ZonezeroAssetCopier.StableGuid(unityGuid) : Guid.NewGuid();
            string destPath = Path.Combine(destinationDir, Path.GetRelativePath(sourceAssetsRoot, sourceControllerPath));
            WriteNativeAsset(controller, destPath, guid, "ScriptableObjectImporter");
            result.Controllers++;
        }
        catch (Exception ex)
        {
            result.Warnings.Add($"Controller '{sourceControllerPath}': {ex.Message}");
        }
    }

    // ================================================================
    //  Prefabs
    // ================================================================

    /// <summary>Builds a native character prefab: instantiate the engine FBX Model referenced by
    /// the Unity prefab, bind material slots per skinned node from the Unity reference, attach an
    /// Animator pointing at the generated native controller, and write a native .prefab.</summary>
    public static bool GenerateCharacterPrefab(string sourcePrefabPath, string destinationDir,
        string destRelativeRoot, Dictionary<string, string> guidMap, EditorAssetBackend backend,
        GenerateResult result)
    {
        string charName = Path.GetFileNameWithoutExtension(sourcePrefabPath);
        try
        {
            UnityPrefabData reference = UnityPrefabData.Read(sourcePrefabPath);
            if (reference.SkinnedMeshes.Count == 0)
            {
                result.Warnings.Add($"Prefab '{charName}': no SkinnedMeshRenderers in Unity reference.");
                return false;
            }

            // Dominant FBX among skinned meshes — the ripped characters keep hair/face/body/weapon
            // meshes inside one FBX.
            string? fbxUnityGuid = DominantMeshGuid(reference);
            if (fbxUnityGuid == null || !guidMap.TryGetValue(fbxUnityGuid, out string? fbxRel))
            {
                result.Warnings.Add($"Prefab '{charName}': FBX guid {fbxUnityGuid} not in unity-guid-map.json.");
                return false;
            }

            var fbxEntry = backend.GetEntry($"{destRelativeRoot}/{fbxRel}");
            if (fbxEntry == null || AssetDatabase.Get(fbxEntry.Guid) is not Model model)
            {
                result.Warnings.Add($"Prefab '{charName}': engine FBX '{destRelativeRoot}/{fbxRel}' not imported.");
                return false;
            }
            GameObject? instance = model.Instantiate();
            if (instance == null)
            {
                result.Warnings.Add($"Prefab '{charName}': Model.Instantiate returned null.");
                return false;
            }
            instance.Name = charName;

            // Material slots: per skinned node name, from the Unity reference.
            int bound = 0;
            foreach (PrefabSkinnedMesh smrRef in reference.SkinnedMeshes)
            {
                if (smrRef.MaterialGuids.Count == 0) continue;
                GameObject? node = FindByName(instance, smrRef.Node.Name);
                var smr = node?.GetComponent<SkinnedMeshRenderer>();
                if (smr == null)
                {
                    result.Warnings.Add($"Prefab '{charName}': node '{smrRef.Node.Name}' has no engine " +
                                        "SkinnedMeshRenderer — material slots skipped.");
                    continue;
                }
                smr.Materials.Clear();
                foreach (string matGuid in smrRef.MaterialGuids)
                    smr.Materials.Add(new AssetRef<Material>(ZonezeroAssetCopier.StableGuid(matGuid)));
                bound++;
            }

            // Animator wired to the generated native controller (same stable-guid scheme).
            // FBX Model.Instantiate already attaches an Animator via ModelRigBinding (clip set,
            // controller empty) — reuse it so GetComponent/serialization see the wired asset.
            string? controllerUnityGuid = ReadAnimatorControllerGuid(sourcePrefabPath);
            if (controllerUnityGuid != null)
            {
                var animator = instance.GetComponent<Animator>() ?? instance.AddComponent<Animator>();
                animator.Controller = new AssetRef<AnimatorController>(
                    ZonezeroAssetCopier.StableGuid(controllerUnityGuid));
            }

            string? unityPrefabGuid = ZonezeroAssetCopier.ReadUnityGuid(sourcePrefabPath + ".meta");
            Guid guid = unityPrefabGuid != null ? ZonezeroAssetCopier.StableGuid(unityPrefabGuid) : Guid.NewGuid();
            string destPath = Path.Combine(destinationDir, "Prefab", charName + ".prefab");
            WriteGameObjectAsPrefab(instance, destPath, guid);
            instance.Dispose();
            result.Prefabs++;
            Debug.Log($"[Zonezero] Generated native prefab '{charName}': {bound} skinned material bindings, " +
                      $"controller {(controllerUnityGuid != null ? "wired" : "none")}.");
            return true;
        }
        catch (Exception ex)
        {
            result.Warnings.Add($"Prefab '{charName}': {ex.Message}");
            return false;
        }
    }

    /// <summary>Builds the Claymore native prefab straight from its FBX (no Unity prefab exists):
    /// instantiate, bind the generated native material to every renderer, save.</summary>
    public static bool GenerateModelPrefab(string modelRelativePath, string materialUnityGuid,
        string prefabName, string destinationDir, EditorAssetBackend backend, GenerateResult result)
    {
        try
        {
            var entry = backend.GetEntry(modelRelativePath);
            if (entry == null || AssetDatabase.Get(entry.Guid) is not Model model)
            {
                result.Warnings.Add($"Prefab '{prefabName}': model '{modelRelativePath}' not imported.");
                return false;
            }
            GameObject? instance = model.Instantiate();
            if (instance == null) return false;
            instance.Name = prefabName;

            var matRef = new AssetRef<Material>(ZonezeroAssetCopier.StableGuid(materialUnityGuid));
            foreach (var smr in instance.GetComponentsInChildren<SkinnedMeshRenderer>(includeSelf: true, includeInactive: true))
            {
                smr.Materials.Clear();
                smr.Materials.Add(matRef);
            }
            foreach (var mr in instance.GetComponentsInChildren<MeshRenderer>(includeSelf: true, includeInactive: true))
            {
                mr.Materials.Clear();
                mr.Materials.Add(matRef);
            }

            string destPath = Path.Combine(destinationDir, "Prefab", prefabName + ".prefab");
            // Derive from the model's own guid so regeneration stays stable.
            Guid guid = XEngine.Editor.AssetEntry.DeriveSubAssetGuid(entry.Guid, "zonezero:prefab");
            WriteGameObjectAsPrefab(instance, destPath, guid);
            instance.Dispose();
            result.Prefabs++;
            return true;
        }
        catch (Exception ex)
        {
            result.Warnings.Add($"Prefab '{prefabName}': {ex.Message}");
            return false;
        }
    }

    private static string? DominantMeshGuid(UnityPrefabData reference)
    {
        var counts = new Dictionary<string, int>(StringComparer.OrdinalIgnoreCase);
        foreach (PrefabSkinnedMesh smr in reference.SkinnedMeshes)
        {
            if (string.IsNullOrEmpty(smr.MeshGuid)) continue;
            counts[smr.MeshGuid] = counts.GetValueOrDefault(smr.MeshGuid) + 1;
        }
        string? best = null;
        int bestCount = 0;
        foreach (var kvp in counts)
            if (kvp.Value > bestCount) { best = kvp.Key; bestCount = kvp.Value; }
        return best;
    }

    /// <summary>The Unity prefab's Animator (class 95) m_Controller guid, or null.</summary>
    public static string? ReadAnimatorControllerGuid(string prefabPath)
    {
        foreach (UnityYamlDocument doc in UnityYaml.ParseFile(prefabPath))
        {
            if (doc.ClassId != 95) continue;
            string? guid = doc.Root["m_Controller"]?.ScalarOf("guid");
            if (!string.IsNullOrEmpty(guid)) return guid;
        }
        return null;
    }

    private static GameObject? FindByName(GameObject root, string name)
    {
        if (string.Equals(root.Name, name, StringComparison.OrdinalIgnoreCase)) return root;
        foreach (GameObject child in root.Children)
        {
            if (FindByName(child, name) is { } found) return found;
        }
        return null;
    }

    // ================================================================
    //  Echo write helpers
    // ================================================================

    /// <summary>Serializes an EngineObject with a full $type envelope and writes file + meta with
    /// the given deterministic guid.</summary>
    public static void WriteNativeAsset(EngineObject asset, string destAbsolutePath, Guid guid, string importerName)
    {
        Directory.CreateDirectory(Path.GetDirectoryName(destAbsolutePath)!);
        EchoObject echo = Serializer.Serialize(typeof(object), asset)
            ?? throw new InvalidOperationException($"failed to serialize '{asset.Name}'.");
        File.WriteAllText(destAbsolutePath, echo.WriteToString());
        WriteMeta(destAbsolutePath, guid, importerName);
    }

    /// <summary>Serializes a GameObject tree as a native .prefab (same shape as
    /// PrefabUtility.CreatePrefab, without stamping the source or dirtying the scene).</summary>
    public static void WriteGameObjectAsPrefab(GameObject source, string destAbsolutePath, Guid guid)
    {
        Directory.CreateDirectory(Path.GetDirectoryName(destAbsolutePath)!);
        source.ClearPrefabDataRecursive();
        Guid savedId = source.AssetID;
        source.AssetID = Guid.Empty;
        EchoObject echo = Serializer.Serialize(typeof(object), source)
            ?? throw new InvalidOperationException($"failed to serialize prefab '{source.Name}'.");
        source.AssetID = savedId;
        File.WriteAllText(destAbsolutePath, echo.WriteToString());
        WriteMeta(destAbsolutePath, guid, "PrefabImporter");
    }

    private static void WriteMeta(string destAbsolutePath, Guid guid, string importerName)
    {
        EditorRegistries.Initialize();
        var importer = EditorRegistries.CreateImporterByName(importerName);
        MetaFile.Write(MetaFile.GetMetaPath(destAbsolutePath), new MetaFileData
        {
            Guid = guid,
            ImporterType = importerName,
            ImporterVersion = importer?.Version ?? 1,
        });
    }

    internal static Dictionary<string, string> LoadGuidMap(string destinationDir, List<string> warnings)
    {
        string mapPath = Path.Combine(destinationDir, UnityGuidMap.MapFileName);
        if (!File.Exists(mapPath))
        {
            warnings.Add($"unity-guid-map.json missing at '{mapPath}' — run the copy step first.");
            return new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
        }
        var map = JsonSerializer.Deserialize<Dictionary<string, string>>(File.ReadAllText(mapPath));
        return map != null
            ? new Dictionary<string, string>(map, StringComparer.OrdinalIgnoreCase)
            : new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
    }
}
