using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;

using XEngine.Animation;
using XEngine.Editor;
using XEngine.Editor.GUI.SceneView;
using XEngine.Editor.Projects;
using XEngine.Runtime;
using XEngine.InputSystem;
using XEngine.Runtime.Resources;
using XEngine.Vector;


namespace XEngine.Zonezero.Editor;

/// <summary>
/// One-click editor workflow for the Zonezero combat demo, FBX-first:
///  1. Copy ZZZ Assets Into Project — raw FBX + textures only (engine metas + sidecars).
///  2. Generate Native Assets — native .mat/.controller/.prefab authored from the Unity
///     reference files read in place (never imported).
///  3. Build Combat Demo Scene — instantiates the generated native prefabs.
/// Designed to be invocable both by a human and by an AI agent via the `runtime_menu` MCP tool.
/// </summary>
public static class ZonezeroMenu
{
    public const string AnbiPrefabPath = "ZZZ/Prefab/Anbi.prefab";
    public const string CorinPrefabPath = "ZZZ/Prefab/Corin.prefab";
    public const string NostradamusPrefabPath = "ZZZ/Prefab/Nostradamus.prefab";
    public const string ClaymorePrefabPath = "ZZZ/Prefab/Claymore.prefab";
    public const string ClaymoreModelPath = "ZZZ/Arts/EnemyModel/Claymore/Claymore.fbx";
    /// <summary>Unity .mat (in the ZZZ source project) whose generated native twin is bound to
    /// the Claymore prefab; its Unity guid is read from the source .meta at generation time.</summary>
    public const string ClaymoreMaterialSourceRelPath =
        @"Arts\EnemyModel\Claymore\Texture\Materials\Monster_Claymored_D.mat";

    /// <summary>
    /// Every character is now instantiated from an engine FBX-backed prefab, and Clay FBX
    /// characters look along +Z (the same way the GameView camera looks) — identity roots show
    /// backs. Yaw everyone 180° (Forward = −Z) so all six face the camera.
    /// </summary>
    public static readonly Float3 LineupForward = -Float3.UnitZ;
    public static readonly (string PrefabPath, Float3 Position)[] CharacterSlots =
    {
        // Lineup sits in open floor: the original z=0 row put the capsule's 0.5 m radius 1 cm from
        // the Structure's south face — swept collision reads that as a wall and freezes movement.
        (CorinPrefabPath, new Float3(-2.4f, 0f, -4.5f)),
        (AnbiPrefabPath, new Float3(0f, 0f, -6f)),
        (NostradamusPrefabPath, new Float3(2.4f, 0f, -4.5f)),
    };

    [MenuItem("Zonezero/Copy ZZZ Assets Into Project")]
    public static void CopyZzzAssets()
    {
        var backend = EditorAssetBackend.Instance;
        if (backend == null)
        {
            Debug.LogError("[Zonezero] No asset database — open a project first.");
            return;
        }

        string destination = Path.Combine(Project.Current!.AssetsPath, "ZZZ");
        ZonezeroAssetCopier.CopyResult result =
            ZonezeroAssetCopier.CopyUnits(ZonezeroAssetCopier.DefaultSourceRoot, ZonezeroAssetCopier.DemoUnits, destination);
        foreach (string warning in result.Warnings)
            Debug.LogWarning($"[Zonezero] {warning}");

        backend.RefreshAll();
        Debug.Log($"[Zonezero] Copied {result.FilesCopied} raw files ({result.TexturesAligned} texture settings aligned, " +
                  $"{result.ClipsWithEvents} clips with animation events) into Assets/ZZZ and refreshed the asset database.");
    }

    [MenuItem("Zonezero/Generate Native Assets")]
    public static void GenerateNativeAssets()
    {
        var backend = EditorAssetBackend.Instance;
        if (backend == null)
        {
            Debug.LogError("[Zonezero] No asset database — open a project first.");
            return;
        }

        string destination = Path.Combine(Project.Current!.AssetsPath, "ZZZ");
        string sourceRoot = ZonezeroAssetCopier.DefaultSourceRoot;

        // 1. Materials + controllers from the Unity reference files (read in place).
        ZonezeroNativeAssets.GenerateResult result = ZonezeroNativeAssets.GenerateAll(
            sourceRoot, ZonezeroAssetCopier.DemoUnits, destination, "ZZZ", backend);

        // 2. Character prefabs: engine FBX instantiate + Unity reference bindings.
        var guidMap = ZonezeroNativeAssets.LoadGuidMap(destination, result.Warnings);
        foreach (string character in new[] { "Anbi", "Corin", "Nostradamus" })
        {
            string sourcePrefab = Path.Combine(sourceRoot, "Prefab", character + ".prefab");
            if (!File.Exists(sourcePrefab))
            {
                result.Warnings.Add($"Unity reference prefab missing: {sourcePrefab}");
                continue;
            }
            ZonezeroNativeAssets.GenerateCharacterPrefab(sourcePrefab, destination, "ZZZ", guidMap, backend, result);
        }

        // 3. Claymore has no Unity prefab — build straight from its FBX + generated material.
        string? claymoreMatGuid = ZonezeroAssetCopier.ReadUnityGuid(
            Path.Combine(sourceRoot, ClaymoreMaterialSourceRelPath) + ".meta");
        if (claymoreMatGuid != null)
            ZonezeroNativeAssets.GenerateModelPrefab(ClaymoreModelPath, claymoreMatGuid,
                "Claymore", destination, backend, result);
        else
            result.Warnings.Add($"Claymore material .meta missing under {sourceRoot} — dummy prefab skipped.");

        foreach (string warning in result.Warnings)
            Debug.LogWarning($"[Zonezero] {warning}");

        // 3b. Demo outline material (M3): the ripped characters don't ship outline materials —
        //     author one natively (YSA "Perfect Outline" look: near-black hull, camera-fix width,
        //     vertex-color smoothed normals from the bake menu above).
        var outlineMat = new Material(ResolveToonOutlineShader());
        outlineMat.Name = "AnbiOutline";
        outlineMat.SetColor("_OutlineColor", new Color(0.02f, 0.02f, 0.04f, 1f));
        outlineMat.SetFloat("_OutlineWidth", 0.35f);
        outlineMat.SetFloat("_VertexColorNormals", 1f);
        ZonezeroNativeAssets.WriteNativeAsset(outlineMat,
            Path.Combine(destination, "Materials", "AnbiOutline.mat"),
            ZonezeroAssetCopier.StableGuid("zzz-outline-anbi"), "UnityMatImporter");

        backend.RefreshAll();
        Debug.Log($"[Zonezero] Generated native assets: {result.Materials} materials, " +
                  $"{result.Controllers} controllers, {result.Prefabs} prefabs ({result.Warnings.Count} warnings).");
    }

    /// <summary>
    /// Port of the YSA NormalsFix scripts (MeshFilterNormalsFix / SkinnedMeshNormalsFix):
    /// averages vertex normals per position and bakes them into vertex COLORS so the
    /// Default/ToonOutline inverted hull can expand along smoothed normals (hard-edged meshes
    /// stop showing outline seams). Idempotent — re-running recomputes the same colors.
    /// </summary>
    [MenuItem("Zonezero/Bake Smoothed Normals To Vertex Colors")]
    public static void BakeSmoothedNormals()
    {
        var backend = EditorAssetBackend.Instance;
        if (backend == null)
        {
            Debug.LogError("[Zonezero] No asset database — open a project first.");
            return;
        }

        int meshesFixed = 0, meshesSkipped = 0;
        foreach (AssetEntry entry in backend.GetAllEntries())
        {
            bool isZzzFbx = entry.Path.StartsWith("ZZZ/", StringComparison.OrdinalIgnoreCase) &&
                            entry.Path.EndsWith(".FBX", StringComparison.OrdinalIgnoreCase);
            if (!isZzzFbx || entry.SubAssets == null) continue;
            foreach (SubAssetEntry sub in entry.SubAssets)
            {
                if (AssetDatabase.Get(sub.Guid) is not Mesh mesh || mesh.IsDisposed) continue;
                Float3[] normals = mesh.Normals;
                Float3[] vertices = mesh.Vertices;
                if (normals.Length == 0 || vertices.Length == 0) { meshesSkipped++; continue; }

                // Sum normals per unique position (quantized key — mirrors the Unity scripts'
                // vertex welding by position), then normalize and write RGB = smoothed normal.
                var sums = new Dictionary<long, Float3>();
                var keys = new long[vertices.Length];
                for (int v = 0; v < vertices.Length; v++)
                {
                    long key = PositionKey(vertices[v]);
                    keys[v] = key;
                    sums.TryGetValue(key, out Float3 sum);
                    sums[key] = sum + normals[v];
                }

                var colors = new Color[vertices.Length];
                for (int v = 0; v < vertices.Length; v++)
                {
                    // YSA stores the raw normal (float vertex colors keep negatives); the
                    // outline shader reads vertexColor.rgb directly as an object-space normal.
                    Float3 smoothed = Normalize(sums[keys[v]]);
                    colors[v] = new Color(smoothed.X, smoothed.Y, smoothed.Z, 1f);
                }
                mesh.Colors = colors;
                backend.SaveAsset(mesh);
                meshesFixed++;
            }
        }
        Debug.Log($"[Zonezero] Smoothed normals baked into vertex colors: {meshesFixed} meshes " +
                  $"({meshesSkipped} skipped — no normals). Enable _VertexColorNormals on outline materials.");
    }

    private static long PositionKey(Float3 p)
    {
        // 2^-14 quantization welds coincident vertices without merging nearby-but-distinct ones.
        long x = (long)MathF.Round(p.X * 16384f), y = (long)MathF.Round(p.Y * 16384f), z = (long)MathF.Round(p.Z * 16384f);
        return (x * 73856093L) ^ (y * 19349663L) ^ (z * 83492791L);
    }

    /// <summary>Resolves the game-owned "Zonezero/ToonOutline" shader by its preserved GUID
    /// (formerly the engine built-in <c>Default/ToonOutline</c>). Falls back to Standard when the
    /// project shader isn't imported (e.g. before assets are generated).</summary>
    private static Shader ResolveToonOutlineShader()
    {
        if (AssetDatabase.Get(new Guid("d7af2277-7f05-3a57-b4ea-216e25ad79c6")) is Shader outline)
            return outline;
        return Shader.LoadDefault(DefaultShader.Standard);
    }

    /// <summary>Duplicates the hero's skinned renderer as a back-face hull wearing the generated
    /// Default/ToonOutline material (mesh + bones shared, so it follows animation identically).</summary>
    private static void AttachOutlineHull(GameObject characterInstance, EditorAssetBackend backend)
    {
        AssetEntry? outlineEntry = backend.GetEntry(OutlineMaterialPath);
        if (outlineEntry == null)
        {
            Debug.LogWarning($"[Zonezero] Outline material missing ({OutlineMaterialPath}) — run Generate Native Assets.");
            return;
        }
        SkinnedMeshRenderer? sourceSmr = characterInstance.GetComponentInChildren<SkinnedMeshRenderer>();
        if (sourceSmr == null) return;

        var outlineGo = new GameObject("OutlineHull");
        outlineGo.SetParent(sourceSmr.GameObject, worldPositionStays: false);
        var outlineSmr = outlineGo.AddComponent<SkinnedMeshRenderer>();
        outlineSmr.SharedMesh = sourceSmr.SharedMesh;
        outlineSmr.Materials = new List<AssetRef<Material>> { new AssetRef<Material>(outlineEntry.Guid) };
        outlineSmr.SetBones(sourceSmr.Bones, sourceSmr.RootBone);
    }

    /// <summary>Generated native outline material (written by Generate Native Assets).</summary>
    public const string OutlineMaterialPath = "ZZZ/Materials/AnbiOutline.mat";

    /// <summary>Builds one big-skill shot vcam: priority 10, follows the hero at the ZZZ
    /// reference offsets (Composer aim + Transposer offset), disabled initially.</summary>
    private static GameObject BuildShotVcam(Scene scene, GameObject hero, string name,
        Float3 localPosition, Float3 followOffset)
    {
        var vcamGo = new GameObject(name);
        vcamGo.Transform.Position = hero.Transform.Position + localPosition;
        var vcam = vcamGo.AddComponent<XEngine.Cinemachine.CinemachineVirtualCamera>();
        vcam.Priority = 10;
        vcam.Follow = hero;
        vcam.LookAt = hero;
        var transposer = vcamGo.AddComponent<XEngine.Cinemachine.CinemachineTransposer>();
        transposer.FollowOffset = followOffset;
        transposer.XDamping = 0f;
        transposer.YDamping = 0f;
        transposer.ZDamping = 0f;
        vcamGo.AddComponent<XEngine.Cinemachine.CinemachineComposer>();
        vcamGo.Enabled = false;
        scene.Add(vcamGo);
        return vcamGo;
    }

    /// <summary>ZZZ reference arena ground texture (dark set).</summary>
    private const string ArenaTextureSource =
        @"Arts\TestVardTextures\Dark\texture_03.png";
    private const string ArenaTextureDest = "ZZZ/Arts/TestVardTextures/texture_03.png";

    /// <summary>
    /// M5 — rebuilds the ZZZ SampleScene Environment with ProBuilder shapes: a 1000×1000
    /// textured ground grid (the reference's pb_Mesh-8580, 4×4 vertex grid), the ~52.8×5.7×17.5
    /// side structure (pb_Mesh-108716) at its reference transform, and the three ascending
    /// steps (one Stairs shape ≈ the reference's three step cubes). Every piece carries a
    /// static MeshCollider — no Rigidbody means world-static collision.
    /// </summary>
    public static void BuildProBuilderArena(Scene scene)
    {
        Material groundMaterial = BuildArenaMaterial();

        GameObject environment = new GameObject("Environment");
        scene.Add(environment);

        // Ground: reference = flat plane extent (500, 0, 500) with a 4×4 vertex grid.
        GameObject ground = new GameObject("Ground");
        var groundPb = ground.AddComponent<XEngine.ProBuilder.ProBuilderMesh>();
        groundPb.Shape = XEngine.ProBuilder.ProBuilderShape.Plane;
        groundPb.Size = new Float3(1000f, 0f, 1000f);
        groundPb.Segments = 3; // (segments+1)² = 16 verts, like the reference mesh
        ground.SetParent(environment, false);
        ground.GetComponent<MeshRenderer>()!.Material = groundMaterial;

        // Side structure: node (-26.48, 0, 4.87) + local center (5.07, 2.87, 4.39) with extent
        // (26.39, 2.87, 8.77) → world center (-21.41, 2.87, 9.26), full size ≈ (52.8, 5.7, 17.5).
        GameObject structure = new GameObject("Structure");
        var structurePb = structure.AddComponent<XEngine.ProBuilder.ProBuilderMesh>();
        structurePb.Shape = XEngine.ProBuilder.ProBuilderShape.Cube;
        structurePb.Size = new Float3(52.78f, 5.74f, 17.54f);
        structure.Transform.Position = new Float3(-21.41f, 2.87f, 9.26f);
        structure.SetParent(environment, false);
        structure.GetComponent<MeshRenderer>()!.Material = groundMaterial;

        // Steps: the reference's three step cubes (m_Size 5.04×0.3×7.86, parent Y-scale 0.5,
        // ascending +Z around x=4.3, z≈12.7-13.9) approximated by one Stairs shape.
        GameObject stairs = new GameObject("Stairs");
        var stairsPb = stairs.AddComponent<XEngine.ProBuilder.ProBuilderMesh>();
        stairsPb.Shape = XEngine.ProBuilder.ProBuilderShape.Stairs;
        stairsPb.Size = new Float3(5.04f, 0.9f, 7.86f);
        stairsPb.StepHeight = 0.3f;
        stairs.Transform.Position = new Float3(4.30f, 0f, 13.2f);
        stairs.SetParent(environment, false);
        stairs.GetComponent<MeshRenderer>()!.Material = groundMaterial;
    }

    /// <summary>Copies the ZZZ arena texture into the project once and builds a Standard
    /// material around it (URP/Lit ≈ Default/Standard for this dark tiling set).</summary>
    private static Material BuildArenaMaterial()
    {
        var backend = EditorAssetBackend.Instance;
        AssetEntry? entry = backend.GetEntry(ArenaTextureDest);
        if (entry == null)
        {
            string source = Path.Combine(ZonezeroAssetCopier.DefaultSourceRoot, ArenaTextureSource);
            if (!File.Exists(source))
            {
                Debug.LogWarning($"[Zonezero] Arena texture missing in reference project ({source}) — plain grey ground.");
                return new Material(Shader.LoadDefault(DefaultShader.Standard)) { Name = "ArenaGround" };
            }
            string dest = Path.Combine(Project.Current!.AssetsPath, ArenaTextureDest.Replace('/', Path.DirectorySeparatorChar));
            Directory.CreateDirectory(Path.GetDirectoryName(dest)!);
            File.Copy(source, dest, overwrite: true);
            backend.ImportFile(ArenaTextureDest);
            backend.RefreshAll();
            entry = backend.GetEntry(ArenaTextureDest);
        }

        var material = new Material(Shader.LoadDefault(DefaultShader.Standard)) { Name = "ArenaGround" };
        if (entry != null)
            material.SetTexture("_MainTex", new AssetRef<Texture2D>(entry.Guid));
        return material;
    }

    /// <summary>M4 — builds the input-system demo scene: a cube driven by the imported ZZZ
    /// .inputactions asset (Player map) via PlayerInput + ActionMover. The mock-handler
    /// acceptance pushes WASD through the engine Input layer and the cube must move.</summary>
    [MenuItem("Zonezero/Build Input Test Scene")]
    public static void BuildInputTestScene()
    {
        var backend = EditorAssetBackend.Instance;
        if (backend == null) return;

        AssetEntry? assetEntry = backend.GetEntry("ZZZ/Settings/InputSystem.inputactions");
        if (assetEntry == null || AssetDatabase.Get(assetEntry.Guid) is not XEngine.InputSystem.InputActionAsset inputAsset)
        {
            Debug.LogError("[Zonezero] ZZZ/Settings/InputSystem.inputactions not imported as InputActionAsset — copy the ZZZ settings file first.");
            return;
        }

        EditorSceneManager.NewScene();
        Scene scene = Scene.Current!;

        // Visible floor + lit cube so the movement reads on screenshots.
        GameObject floor = new GameObject("Floor");
        var floorRenderer = floor.AddComponent<MeshRenderer>();
        floorRenderer.Mesh = Mesh.CreateCube(new Float3(24f, 0.1f, 24f));
        floorRenderer.Material = new Material(Shader.LoadDefault(DefaultShader.Standard));
        floorRenderer.Material.Res?.SetColor("_MainColor", new Color(0.35f, 0.35f, 0.4f, 1f));
        floor.Transform.LocalPosition = new Float3(0f, -0.3f, 0f);
        scene.Add(floor);

        GameObject cube = new GameObject("InputCube");
        var cubeRenderer = cube.AddComponent<MeshRenderer>();
        cubeRenderer.Mesh = Mesh.CreateCube(Float3.One);
        cubeRenderer.Material = new Material(Shader.LoadDefault(DefaultShader.Standard));
        cubeRenderer.Material.Res?.SetColor("_MainColor", new Color(0.9f, 0.35f, 0.2f, 1f));
        scene.Add(cube);

        var playerInput = cube.AddComponent<XEngine.InputSystem.PlayerInput>();
        playerInput.Asset = new AssetRef<XEngine.InputSystem.InputActionAsset>(inputAsset);
        playerInput.DefaultActionMap = "Player";
        cube.AddComponent<XEngine.InputSystem.ActionMover>().Speed = 2f;

        bool saved = EditorSceneManager.SaveAs("ZZZ/Scenes/InputDemo.scene");
        Debug.Log($"[Zonezero] Input test scene built (cube + PlayerInput/ActionMover, map 'Player', saved={saved}).");
    }

    private static Float3 Normalize(Float3 v)
    {
        float lengthSq = Float3.LengthSquared(v);
        return lengthSq > 1e-12f ? v * (1f / MathF.Sqrt(lengthSq)) : Float3.UnitY;
    }

    [MenuItem("Zonezero/Build Combat Demo Scene")]
    public static void BuildCombatDemoScene()
    {        var backend = EditorAssetBackend.Instance;
        if (backend == null) return;

        EditorSceneManager.NewScene();
        Scene scene = Scene.Current!;

        // Reuse the Universal template objects (camera with UniversalAdditionalCameraData/HDR,
        // directional light with its plumbing, global volume) — the render pipeline expects them.
        GameObject? templateCamera = FindRoot(scene, "Main Camera");
        GameObject? templateLight = FindRoot(scene, "Directional Light");
        if (templateCamera == null || templateLight == null)
        {
            Debug.LogError("[Zonezero] Universal template objects missing after NewScene.");
            return;
        }

        int characters = 0;
        int characterNodes = 0;
        foreach ((string prefabPath, Float3 position) in CharacterSlots)
        {
            GameObject? instance = InstantiateNativePrefab(backend, prefabPath);
            if (instance == null) continue;
            instance.Transform.Position = position;
            instance.Transform.Forward = LineupForward;
            scene.Add(instance);
            characters++;
            characterNodes += CountNodes(instance);

            // M3 showcase: an inverted-hull outline on the hero (Anbi) — a duplicate skinned
            // renderer sharing mesh + bones with the generated Default/ToonOutline material.
            if (instance.Name == "Anbi")
                AttachOutlineHull(instance, backend);
        }
        if (characters == 0)
        {
            Debug.LogError("[Zonezero] No character prefabs found — run Copy ZZZ Assets then Generate Native Assets first.");
            return;
        }

        // Three Claymore training dummies around the origin.
        var dummyPositions = new[]
        {
            new Float3(3.5f, 0f, 4f),
            new Float3(-3.5f, 0f, 4.5f),
            new Float3(0f, 0f, 6.5f),
        };
        int dummies = 0;
        for (int i = 0; i < dummyPositions.Length; i++)
        {
            GameObject? dummy = InstantiateNativePrefab(backend, ClaymorePrefabPath);
            if (dummy == null) break;
            dummy.Name = $"Claymore_{i + 1}";
            WeaponController.EnemySet.Add(dummy);
            if (dummy.GetComponent<EnemyController>() == null)
                dummy.AddComponent<EnemyController>();
            dummy.Transform.Position = dummyPositions[i];
            dummy.Transform.Forward = LineupForward;
            scene.Add(dummy);
            dummies++;
        }
        if (dummies == 0)
            Debug.LogWarning("[Zonezero] Claymore prefab missing — scene built with the player only.");

        // ProBuilder arena (M5): faithful rebuild of the ZZZ SampleScene Environment —
        // textured ground grid, the big side structure, and the three ascending steps.
        BuildProBuilderArena(scene);

        // Sun: ZZZ SampleScene key light — euler (50, -30, 0), warm white (1.0, 0.957, 0.839).
        templateLight.Name = "Sun";
        templateLight.Transform.LocalEulerAngles = new Float3(-50f, 30f, 0f);
        if (templateLight.GetComponent<DirectionalLight>() is { } sun)
        {
            sun.Intensity = 1.0f;
            sun.Color = new Color(1f, 0.957f, 0.839f, 1f);
        }

        // Player rig (M7): PlayerInput (imported .inputactions) + the 3-character tag
        // team FSM controller. The character roots above become the team members.
        AssetEntry? inputAssetEntry = backend.GetEntry("ZZZ/Settings/InputSystem.inputactions");
        if (inputAssetEntry != null &&
            AssetDatabase.Get(inputAssetEntry.Guid) is XEngine.InputSystem.InputActionAsset inputAsset)
        {
            GameObject player = new GameObject("Player");
            var playerInput = player.AddComponent<XEngine.InputSystem.PlayerInput>();
            playerInput.Asset = new AssetRef<XEngine.InputSystem.InputActionAsset>(inputAsset);
            playerInput.DefaultActionMap = "Player";
            var controller = player.AddComponent<PlayerController>();
            // Big-skill shot vcams (ZZZ prefab Shots): intro + finishing cameras following the
            // active hero, disabled by default — the BigSkill states toggle them with camera cuts.
            GameObject heroRoot = null!;
            foreach (GameObject root in scene.RootObjects)
                if (root.Name == "Corin") heroRoot = root;
            if (heroRoot != null)
            {
                controller.BigSkillStartShot = BuildShotVcam(scene, heroRoot, "BigSkillStart Shot",
                    new Float3(-1.631f, 1.22f, 1.506f), new Float3(0f, 0.54f, -2.22f));
                controller.BigSkillShot = BuildShotVcam(scene, heroRoot, "BigSkill Shot",
                    new Float3(2.633f, 1.42f, -3.76f), new Float3(0f, 0.57f, -4.59f));
            }
            foreach ((string prefabPath, Float3 position) in CharacterSlots)
            {
                GameObject? instance = null;
                foreach (GameObject root in scene.RootObjects)
                {
                    string name = System.IO.Path.GetFileNameWithoutExtension(prefabPath);
                    if (root.Name == name) instance = root;
                }
                if (instance != null)
                    controller.TeamAddForTesting(instance);
            }
            scene.Add(player);
        }

        // Cinemachine rig (M6): ZZZ pattern — Brain on the Main Camera, a FreeLook rig
        // following the hero with the reference orbit numbers (Top 1.47/2.3, Middle
        // 0.14/3.5, Bottom -1.1/2.5, FOV 40), plus the demo orbit driver.
        GameObject hero = null!;
        foreach ((string prefabPath, Float3 position) in CharacterSlots)
        {
            if (!prefabPath.Contains("Anbi")) continue;
            foreach (GameObject root in scene.RootObjects)
                if (root.Name == "Anbi") hero = root;
        }
        if (hero != null)
        {
            XEngine.Cinemachine.CinemachineBrain brain = templateCamera!.AddComponent<XEngine.Cinemachine.CinemachineBrain>();
            brain.DefaultBlend = XEngine.Cinemachine.BlendDefinition.EaseInOut(2f);

            var freeLookGo = new GameObject("FreeLook Camera");
            var freeLook = freeLookGo.AddComponent<XEngine.Cinemachine.CinemachineFreeLook>();
            freeLook.Priority = 10;
            freeLook.Follow = hero;
            freeLook.LookAt = hero;
            freeLook.Top = new XEngine.Cinemachine.FreeLookOrbit { Height = 1.47f, Radius = 2.3f };
            freeLook.Middle = new XEngine.Cinemachine.FreeLookOrbit { Height = 0.14f, Radius = 3.5f };
            freeLook.Bottom = new XEngine.Cinemachine.FreeLookOrbit { Height = -1.1f, Radius = 2.5f };
            freeLook.YAxis.Value = 0.5f;
            freeLookGo.AddComponent<ZonezeroFreeLookDriver>();
            scene.Add(freeLookGo);
        }

        // Camera: pulled back so all three characters and the dummy line are in frame.
        templateCamera.Transform.LocalPosition = new Float3(0f, 2.4f, -10.5f);
        templateCamera.Transform.LocalEulerAngles = new Float3(10f, 0f, 0f);
        if (templateCamera.GetComponent<Camera>() is { } camera)
        {
            camera.FieldOfView = 42f;
            camera.ClearFlags = CameraClearFlags.SolidColor;
            camera.ClearColor = new Color(0.32f, 0.47f, 0.65f, 1f);
        }

        EditorSceneManager.SaveAs("Scenes/ZonezeroCombat.scene");
        Debug.Log($"[Zonezero] Built ZonezeroCombat.scene: {characters} characters ({characterNodes} nodes), " +
                  $"{dummies} dummies, ground/sun/camera from the Universal template.");
    }

    internal static GameObject? InstantiateNativePrefab(EditorAssetBackend backend, string prefabPath)
    {
        Guid guid = backend.GetEntry(prefabPath)?.Guid ?? Guid.Empty;
        if (guid == Guid.Empty)
        {
            Debug.LogError($"[Zonezero] '{prefabPath}' not found — run Zonezero/Generate Native Assets first.");
            return null;
        }
        if (AssetDatabase.Get(guid) is not PrefabAsset prefab || prefab.Instantiate() is not { } instance)
        {
            Debug.LogError($"[Zonezero] Failed to instantiate native prefab '{prefabPath}'.");
            return null;
        }
        return instance;
    }

    private static GameObject? FindRoot(Scene scene, string name)
    {
        foreach (GameObject root in scene.RootObjects)
            if (root.Name == name)
                return root;
        return null;
    }

    private static int CountNodes(GameObject go)
    {
        int count = 1;
        foreach (GameObject child in go.Children)
            count += CountNodes(child);
        return count;
    }

    // ================================================================
    //  Battle Arena v2 — new-design combat sandbox (not a ZZZ port)
    // ================================================================

    /// <summary>GUID of the imported ZZZ .inputactions asset (Player map with Move/SkillJ/SkillK).</summary>
    internal const string BattleInputActionsGuid = "1f57f6b5-bfad-4bc6-94f7-eaace3868732";

    /// <summary>English-directory Corin controller (the healthy copy with resolved motions).</summary>
    internal const string HealthyCorinControllerGuid = "58c28ec0-40bc-4ce5-82a7-132f6165880a";

    [MenuItem("Zonezero/Build Anim Single Test")]
    public static void BuildAnimSingleTest()
    {
        var backend = EditorAssetBackend.Instance;
        if (backend == null) return;

        EditorSceneManager.NewScene();
        Scene scene = Scene.Current!;
        if (FindRoot(scene, "Main Camera") == null || FindRoot(scene, "Directional Light") == null)
        {
            Debug.LogError("[Zonezero] Universal template objects missing after NewScene.");
            return;
        }
        BuildProBuilderArena(scene);

        // Resolve Corin's Run clip guid from the healthy controller (state "Run" -> Motion).
        var ctrlRef = new AssetRef<AnimatorController>(Guid.Parse(HealthyCorinControllerGuid));
        ctrlRef.EnsureLoaded();
        var ctrl = (AnimatorController?)ctrlRef.Res;
        if (ctrl == null) { Debug.LogError("[Zonezero] healthy Corin controller failed to load."); return; }
        var runState = ctrl.States.FirstOrDefault(s2 => s2.Name == "Run");
        if (runState == null || runState.Motion.AssetID == Guid.Empty)
        { Debug.LogError("[Zonezero] controller has no Run motion."); return; }
        Guid runClip = runState.Motion.AssetID;

        // LEFT: pure code-driven (controller cleared, SingleClipPlayer plays the Run clip).
        var codeGo = InstantiateNativePrefab(backend, CorinPrefabPath)!;
        codeGo.Name = "Test_CodeDriven";
        codeGo.Transform.Position = new Float3(-3f, 0f, -6f);
        codeGo.Transform.Forward = Float3.UnitZ;
        var codeAnim = codeGo.GetComponent<Animator>();
        codeAnim.Controller = default; // clear FSM: pure code-driven mode
        var player = codeGo.AddComponent<XEngine.Zonezero.Combat.SingleClipPlayer>();
        player.Clip = new AssetRef<AnimationClip>(runClip);
        scene.Add(codeGo);

        // RIGHT: FSM-driven (controller stays; FSM default state = Idle). Eval will Play("Run").
        var fsmGo = InstantiateNativePrefab(backend, CorinPrefabPath)!;
        fsmGo.Name = "Test_FsmDriven";
        fsmGo.Transform.Position = new Float3(3f, 0f, -6f);
        fsmGo.Transform.Forward = Float3.UnitZ;
        scene.Add(fsmGo);

        // Camera framing both.
        var camGo = new GameObject("TestCamera");
        scene.Add(camGo);
        camGo.Transform.Position = new Float3(0f, 2.2f, -11.5f);
        camGo.Transform.Forward = Float3.UnitZ;
        var cam = camGo.AddComponent<Camera>();
        cam.FieldOfView = 50f;
        cam.ClearFlags = CameraClearFlags.SolidColor;
        cam.ClearColor = new Color(0.10f, 0.12f, 0.16f, 1f);
        FindRoot(scene, "Main Camera")!.Enabled = false;

        bool saved = EditorSceneManager.SaveAs("Scenes/AnimSingleTest.scene");
        Debug.Log($"[Zonezero] AnimSingleTest built: code-driven @(-3) fsm-driven @(3), runClip={runClip.ToString()[..8]}, saved={saved}");
    }

    [MenuItem("Zonezero/Build Battle Arena v2")]
    public static void BuildBattleArenaScene()
    {
        var backend = EditorAssetBackend.Instance;
        if (backend == null) return;

        EditorSceneManager.NewScene();
        Scene scene = Scene.Current!;
        if (FindRoot(scene, "Main Camera") == null || FindRoot(scene, "Directional Light") == null)
        {
            Debug.LogError("[Zonezero] Universal template objects missing after NewScene.");
            return;
        }
        BuildProBuilderArena(scene);

        // ── hero + allies (one faction) ──
        var hero = InstantiateNativePrefab(backend, AnbiPrefabPath)!;
        hero.Name = "Battle_Hero";
        hero.Transform.Position = new Float3(0f, 0f, -4.5f);
        hero.Transform.Forward = Float3.UnitZ;
        scene.Add(hero);
        hero.AddComponent<XEngine.Zonezero.Combat.HeroCombatController>();

        SpawnAlly(backend, scene, CorinPrefabPath, "Battle_Ally_Corin", new Float3(-2.3f, 0f, -7.0f),
            new XEngine.Zonezero.Combat.AllyCombatAI.PatrolRoute(
                new Float3(-4.0f, 0f, -3.5f), new Float3(-0.5f, 0f, -8.5f)));
        SpawnAlly(backend, scene, NostradamusPrefabPath, "Battle_Ally_Nike", new Float3(2.3f, 0f, -7.0f),
            new XEngine.Zonezero.Combat.AllyCombatAI.PatrolRoute(
                new Float3(4.2f, 0f, -6.0f), new Float3(0.8f, 0f, -9.0f)));

        // ── enemy practice dummies ──
        SpawnDummy(backend, scene, "Dummy_A", new Float3(-2.6f, 0f, 3.4f));
        SpawnDummy(backend, scene, "Dummy_B", new Float3(0f, 0f, 5.2f));
        SpawnDummy(backend, scene, "Dummy_C", new Float3(2.6f, 0f, 3.9f));

        // ── fixed-angle follow camera on the hero ──
        var templateCam = FindRoot(scene, "Main Camera");
        if (templateCam != null) templateCam.Enabled = false;
        var camGo = new GameObject("BattleCamera");
        scene.Add(camGo);
        camGo.Transform.Position = hero.Transform.Position - Float3.UnitZ * 6f
                                  + new Float3(0f, 6f * MathF.Tan(40f * MathF.PI / 180f) + 0.8f, 0f);
        camGo.Transform.Forward = Float3.UnitZ;
        var cam = camGo.AddComponent<Camera>();
        cam.FieldOfView = 46f;
        cam.ClearFlags = CameraClearFlags.SolidColor;
        cam.ClearColor = new Color(0.10f, 0.12f, 0.16f, 1f);
        camGo.AddComponent<AudioListener>();
        var rig = camGo.AddComponent<XEngine.Zonezero.Combat.BattleFollowCamera>();
        rig.Target = hero;

        // ── shared input asset (Move WASD + SkillJ J + SkillK K) ──
        var inputGo = new GameObject("Battle_Input");
        scene.Add(inputGo);
        var input = inputGo.AddComponent<PlayerInput>();
        input.Asset = new AssetRef<InputActionAsset>(Guid.Parse(BattleInputActionsGuid));
        input.DefaultActionMap = "Player";

        bool saved = EditorSceneManager.SaveAs("Scenes/ZonezeroBattle.scene");
        Debug.Log($"[Zonezero] Battle Arena v2 built: 1 hero + 2 allies + 3 dummies; saved={saved}");
    }

    private static void SpawnAlly(EditorAssetBackend backend, Scene scene, string prefabPath,
        string name, Float3 position, XEngine.Zonezero.Combat.AllyCombatAI.PatrolRoute route)
    {
        var go = InstantiateNativePrefab(backend, prefabPath)!;
        go.Name = name;
        go.Transform.Position = position;
        go.Transform.Forward = Float3.UnitZ;
        scene.Add(go);
        var ai = go.AddComponent<XEngine.Zonezero.Combat.AllyCombatAI>();
        ai.PatrolPointA = route.PointA;
        ai.PatrolPointB = route.PointB;
    }

    private static void SpawnDummy(EditorAssetBackend backend, Scene scene, string name, Float3 position)
    {
        var dummy = InstantiateNativePrefab(backend, ClaymorePrefabPath);
        if (dummy == null) return;
        dummy.Name = name;
        WeaponController.EnemySet.Add(dummy);
        if (dummy.GetComponent<EnemyController>() == null)
            dummy.AddComponent<EnemyController>();
        dummy.Transform.Position = position;
        dummy.Transform.Forward = -Float3.UnitZ;
        scene.Add(dummy);
    }
}
