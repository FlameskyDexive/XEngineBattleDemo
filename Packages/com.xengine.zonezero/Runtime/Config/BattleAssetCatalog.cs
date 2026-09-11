using System;
using System.Collections.Generic;
using XEngine.Runtime;
using XEngine.Runtime.Resources;

namespace XEngine.Zonezero.Config;

/// <summary>Explicit build dependencies for assets selected by gameplay paths.</summary>
public sealed class BattleAssetCatalog : ScriptableObject
{
    public List<string> EffectPaths = new();
    public List<AssetRef<PrefabAsset>> Effects = new();
    public List<string> HudNames = new();
    public List<AssetRef<Texture2D>> HudTextures = new();
    public AssetRef<Texture2D> HudAtlas;
    public List<SpriteRect> HudAtlasRects = new();
    public AssetRef<PrefabAsset> HudPrefab;
    public string HudPrefabAddress = "BattleHUD";
    public List<string> EffectAddresses = new();
    public List<string> HudAddresses = new();

    public static BattleAssetCatalog? Load()
        => BattleAddressables.Load<BattleAssetCatalog>("Vfx/BattleAssetCatalog");

    public Guid FindEffect(string path)
        => LoadEffect(path)?.AssetID ?? Guid.Empty;

    public PrefabAsset? LoadEffect(string path)
    {
        for (int i = 0; i < EffectPaths.Count && i < EffectAddresses.Count; i++)
            if (string.Equals(EffectPaths[i], path, StringComparison.OrdinalIgnoreCase))
                return BattleAddressables.Load<PrefabAsset>(EffectAddresses[i]);
        return null;
    }

    public Sprite? LoadHudSprite(string name)
    {
        for (int i = 0; i < HudNames.Count && i < HudAddresses.Count; i++)
        {
            if (!string.Equals(HudNames[i], name, StringComparison.OrdinalIgnoreCase)) continue;
            if (HudAtlas.AssetID != Guid.Empty && i < HudAtlasRects.Count)
            {
                HudAtlas.EnsureLoaded();
                if (Scene.Current is { } atlasScene) HudAtlas.LockToScene(atlasScene);
                if (HudAtlas.Res is { } atlas)
                    return Sprite.Create(atlas, HudAtlasRects[i], new XEngine.Vector.Float2(.5f, .5f), name: name);
            }
            var reference = new AssetRef<Texture2D>(BattleAddressables.Load<Texture2D>(HudAddresses[i]));
            reference.EnsureLoaded();
            // Joystick visuals stay hidden until touched, so an idle sweep must not evict
            // their source texture while this HUD's scene is still alive.
            if (Scene.Current is { } scene) reference.LockToScene(scene);
            return reference.Res is { } texture ? Sprite.CreateFullTexture(texture) : null;
        }
        return null;
    }
}
