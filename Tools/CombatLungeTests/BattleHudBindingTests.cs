using XEngine.Runtime;
using XEngine.Runtime.UI;
using XEngine.Zonezero.UI;

using Xunit;

namespace XEngine.Zonezero.Runtime.Tests;

public sealed class BattleHudBindingTests
{
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
