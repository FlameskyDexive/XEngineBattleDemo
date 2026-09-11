using System;
using System.Collections.Generic;
using XEngine.Runtime;
using XEngine.Runtime.AssetBundles;

namespace XEngine.Zonezero.Config;

/// <summary>Loads only addresses explicitly collected in the default battle package.</summary>
public static class BattleAddressables
{
    private static AssetPackage? _package;
    private static readonly Dictionary<string, EngineObject> Cache = new(StringComparer.OrdinalIgnoreCase);
    public static T? Load<T>(string address) where T : EngineObject
    {
        var package = XAssets.TryGetPackage("default");
        if (!ReferenceEquals(package, _package)) { Cache.Clear(); _package = package; }
        if (Cache.TryGetValue(address, out var cached) && cached is T typed && !typed.IsDisposed) return typed;
        if (package?.ActiveManifest is not { EnableAddressable: true } manifest)
            throw new InvalidOperationException("Battle requires Addressable AssetBundles. Enable Editor Simulate and collect the battle resources.");
        if (!manifest.TryGetAssetByAddress(address, out _))
            throw new InvalidOperationException($"Battle Addressable '{address}' is missing from AssetBundleCollector.");
        using var handle = package.LoadAssetSync<T>(address);
        var asset = handle.GetAssetObject<T>() ?? throw new InvalidOperationException($"Cannot load Battle Addressable '{address}': {handle.Error}");
        Cache[address] = asset;
        return asset;
    }
}
