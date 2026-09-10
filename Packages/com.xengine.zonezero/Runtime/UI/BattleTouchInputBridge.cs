// This file is part of the XEngine Game Engine
// Licensed under the MIT License. See the LICENSE file in the project root for details.

using XEngine.Runtime;
using XEngine.Vector;

namespace XEngine.Zonezero.UI;

/// <summary>
/// Bridges the touch HUD (virtual joystick + skill buttons) into the engine input pipeline via
/// <see cref="InputInjector"/>: injected keys are OR-composed into the <c>Input</c> facade reads,
/// so <c>HeroCombatController</c>'s existing Move/SkillJ/K/L/I polling sees virtual presses
/// exactly like physical ones, and the physical keyboard keeps working in parallel.
/// The joystick vector synthesizes WASD holds with per-axis hysteresis (enter 0.35 / exit 0.25),
/// producing the same normalized 8-direction composite the keyboard yields.
/// </summary>
public static class BattleTouchInputBridge
{
    public const float EnterThreshold = 0.35f;
    public const float ExitThreshold = 0.25f;

    private static readonly bool[] _wasdHeld = new bool[4]; // W, A, S, D
    private static readonly KeyCode[] _wasdKeys = { KeyCode.W, KeyCode.A, KeyCode.S, KeyCode.D };
    private static readonly KeyCode[] _skillKeys = { KeyCode.J, KeyCode.K, KeyCode.L, KeyCode.I };

    /// <summary>Feeds the virtual joystick vector; synthesizes WASD holds with hysteresis.</summary>
    public static void SetMove(Float2 v)
    {
        SetAxis(0, v.Y, enterAbove: EnterThreshold); // W
        SetAxis(1, -v.X, enterAbove: EnterThreshold); // A
        SetAxis(2, -v.Y, enterAbove: EnterThreshold); // S
        SetAxis(3, v.X, enterAbove: EnterThreshold); // D
    }

    /// <summary>Releases every synthesized WASD hold (joystick released).</summary>
    public static void ReleaseMove()
    {
        for (int i = 0; i < _wasdKeys.Length; i++)
        {
            if (!_wasdHeld[i]) continue;
            InputInjector.Release(_wasdKeys[i]);
            _wasdHeld[i] = false;
        }
    }

    /// <summary>Press-and-release edge for a virtual skill key (J/K/L/I by battle slot index).</summary>
    public static void TapSkill(int skillIndex)
    {
        if (skillIndex < 0 || skillIndex >= _skillKeys.Length) return;
        InputInjector.Tap(_skillKeys[skillIndex]);
    }

    /// <summary>Holds a virtual skill key (for buttons that fire while held).</summary>
    public static void HoldSkill(int skillIndex, bool down)
    {
        if (skillIndex < 0 || skillIndex >= _skillKeys.Length) return;
        if (down) InputInjector.Press(_skillKeys[skillIndex]);
        else InputInjector.Release(_skillKeys[skillIndex]);
    }

    /// <summary>Releases all virtual keys — called when the HUD disables so nothing sticks.</summary>
    public static void ReleaseAll()
    {
        ReleaseMove();
        foreach (KeyCode key in _skillKeys)
            InputInjector.Release(key);
    }

    private static void SetAxis(int wasdIndex, float value, float enterAbove)
    {
        bool shouldHold = _wasdHeld[wasdIndex]
            ? value > ExitThreshold
            : value > enterAbove;
        if (_wasdHeld[wasdIndex] == shouldHold) return;
        _wasdHeld[wasdIndex] = shouldHold;
        if (shouldHold) InputInjector.Press(_wasdKeys[wasdIndex]);
        else InputInjector.Release(_wasdKeys[wasdIndex]);
    }
}
