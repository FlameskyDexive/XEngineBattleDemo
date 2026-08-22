using XEngine.Zonezero;

namespace XEngine.Zonezero.Editor;

/// <summary>Marker used by package/build audits to prove Zonezero Editor is present.</summary>
public static class ZonezeroPackageEditor
{
    public const string PackageName = ZonezeroRuntime.PackageName;
    public const string Version = ZonezeroRuntime.Version;
}
