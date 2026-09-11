using XEngine.Runtime;
using XEngine.Runtime.UI;
using XEngine.Zonezero.UI;
using XEngine.Vector;

using Xunit;

namespace XEngine.Zonezero.Runtime.Tests;

public sealed class BattleHudBindingTests
{
    [Theory]
    [InlineData(ImageType.Simple)]
    [InlineData(ImageType.Sliced)]
    [InlineData(ImageType.Filled)]
    public void AtlasImage_PreservesNonZeroSubregionUv(ImageType type)
    {
        using var root = new GameObject("AtlasImage");
        root.EnsureRectTransform().ComputedRect = new Rect(0, 0, 80, 40);
        using var texture = new XEngine.Runtime.Resources.Texture2D(512, 512);
        var sprite = XEngine.Runtime.Resources.Sprite.Create(texture,
            new XEngine.Runtime.Resources.SpriteRect(32, 64, 128, 96), new Float2(.5f, .5f));
        var image = root.AddComponent<Image>();
        image.Sprite = sprite;
        image.Type = type;
        var builder = new UIMeshBuilder();
        image.GenerateMesh(builder, UIContext.Default);
        using var mesh = new XEngine.Runtime.Resources.Mesh();
        typeof(UIMeshBuilder).GetMethod("Bake", System.Reflection.BindingFlags.Instance |
            System.Reflection.BindingFlags.NonPublic)!.Invoke(builder, new object[] { mesh });
        Assert.Equal(32f / 512, mesh.UV.Min(uv => uv.X));
        Assert.Equal(160f / 512, mesh.UV.Max(uv => uv.X));
        Assert.Equal(64f / 512, mesh.UV.Min(uv => uv.Y));
        Assert.Equal(160f / 512, mesh.UV.Max(uv => uv.Y));
    }

    [Fact]
    public void Joystick_TopLeftAnchoredVisualsStayAtPointerAndFollowUpwardDrag()
    {
        using var zone = new GameObject("JoystickZone");
        zone.EnsureRectTransform().ComputedRect = new Rect(0, 0, 960, 1080);
        var stick = zone.AddComponent<Joystick>();
        stick.BaseRect = AddImage(zone, "Base").GameObject.EnsureRectTransform();
        stick.ThumbRect = AddImage(zone, "Thumb").GameObject.EnsureRectTransform();
        var pointer = new PointerEventData { DesignPosition = new Float2(260, 250) };
        stick.OnPointerDown(pointer);
        Assert.Equal(new Float2(260, -830), stick.BaseRect.AnchoredPosition);
        pointer.DesignPosition = new Float2(310, 280);
        stick.OnDrag(pointer);
        Assert.Equal(new Float2(310, -800), stick.ThumbRect.AnchoredPosition);
        Assert.True(stick.Value.X > 0 && stick.Value.Y > 0);
        stick.Release();
        Assert.False(stick.BaseRect.GameObject.Enabled);
        Assert.False(stick.ThumbRect.GameObject.Enabled);
    }

    [Theory]
    [InlineData("btn_attack.png")]
    [InlineData("joystick_base.png")]
    [InlineData("cd_mask.png")]
    public void HudAssetPath_IsProjectRelative(string fileName)
    {
        string path = BattleHUD.ResolveHudAssetPath(fileName);

        Assert.Equal("ZZZ/Arts/UI/HUD/" + fileName, path);
        Assert.DoesNotContain("Assets/", path, System.StringComparison.OrdinalIgnoreCase);
    }

    [Fact]
    public void HudAssetPath_TrimsLeadingSeparators()
    {
        Assert.Equal("ZZZ/Arts/UI/HUD/btn_skill_i.png",
            BattleHUD.ResolveHudAssetPath("/\\btn_skill_i.png"));
    }

    [Fact]
    public void SkillButton_BindsAuthoredVisualChildren()
    {
        using var root = new GameObject("SkillButton");
        SkillButton button = root.AddComponent<SkillButton>();

        Image background = AddImage(root, "Background");
        Image icon = AddImage(root, "Icon");
        Image cooldown = AddImage(root, "CdMask");
        AddText(root, "CdLabel");
        Text key = AddText(root, "KeyLabel");
        cooldown.GameObject.Enabled = false;

        button.BindExistingVisuals();

        Assert.Same(background, button.Background);
        Assert.Same(icon, button.Icon);
        Assert.Same(cooldown, button.CdMask);
        Assert.Same(key, button.KeyLabel);
    }

    private static Image AddImage(GameObject parent, string name)
    {
        var go = new GameObject(name);
        go.SetParent(parent, worldPositionStays: false);
        return go.AddComponent<Image>();
    }

    private static Text AddText(GameObject parent, string name)
    {
        var go = new GameObject(name);
        go.SetParent(parent, worldPositionStays: false);
        return go.AddComponent<Text>();
    }
}
