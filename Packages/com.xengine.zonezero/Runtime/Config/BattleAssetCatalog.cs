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
    public AssetRef<PrefabAsset> HudPrefab;

    public static BattleAssetCatalog? Load()
        => GameResources.Load<BattleAssetCatalog>("Zonezero/BattleAssetCatalog");

    public Guid FindEffect(string path)
    {
        for (int i = 0; i < EffectPaths.Count && i < Effects.Count; i++)
            if (string.Equals(EffectPaths[i], path, StringComparison.OrdinalIgnoreCase))
                return Effects[i].AssetID;
        return Guid.Empty;
    }

    public Sprite? LoadHudSprite(string name)
    {
        for (int i = 0; i < HudNames.Count && i < HudTextures.Count; i++)
        {
            if (!string.Equals(HudNames[i], name, StringComparison.OrdinalIgnoreCase)) continue;
            var reference = HudTextures[i];
            reference.EnsureLoaded();
            return reference.Res is { } texture ? Sprite.CreateFullTexture(texture) : null;
        }
        return null;
    }
}
