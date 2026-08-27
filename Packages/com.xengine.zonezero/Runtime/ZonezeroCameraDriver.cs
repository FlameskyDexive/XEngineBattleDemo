// This file is part of the XEngine Game Engine
// Licensed under the MIT License. See the LICENSE file in the project root for details.

using XEngine.Cinemachine;
using XEngine.InputSystem;
using XEngine.Runtime;
using XEngine.Runtime.Resources;
using XEngine.Vector;

namespace XEngine.Zonezero;

/// <summary>
/// Feeds the combat scene's FreeLook rig from the player's <c>Look</c> action (pointer/gamepad
/// deltas), like the reference demo's mouse look. The old M6 acceptance stand-in auto-orbited the
/// rig at 40°/s, which silently fights the camera-relative run steering — keep the orbit behavior
/// ONLY as an opt-in fallback when no PlayerInput/Look exists (headless capture rigs).
/// </summary>
[AddComponentMenu("Zonezero/FreeLook Look Driver")]
public sealed class ZonezeroFreeLookDriver : MonoBehaviour
{
    /// <summary>Orbit speed used when no Look action is reachable (demo/headless mode).</summary>
    public float OrbitSpeedDegreesPerSecond = 40f;
    public float VerticalBobSpeed = 0.4f;

    /// <summary>Mouse-delta yaw scale (degrees per pixel), ZZZ MouseManager feel.</summary>
    public float LookSensitivity = 0.12f;

    private CinemachineFreeLook? _freeLook;
    private InputAction? _look;
    private bool _warnedNoInput;

    public override void Start()
    {
        _freeLook = GameObject?.GetComponent<CinemachineFreeLook>();
        PlayerInput? playerInput = null;
        Scene? scene = Scene.Current;
        if (scene != null)
        {
            foreach (GameObject root in scene.RootObjects)
            {
                playerInput = root.GetComponent<PlayerInput>();
                if (playerInput != null) break;
            }
        }
        _look = playerInput?.FindAction("Look");
    }

    public override void Update()
    {
        if (_freeLook == null) return;

        if (_look != null)
        {
            // Raw pixel delta → degrees, frame-rate independent (deltas are per-frame samples).
            Float2 delta = _look.ReadValue<Float2>();
            _freeLook.XAxis.Value += delta.X * LookSensitivity;
            float y = _freeLook.YAxis.Value - delta.Y * LookSensitivity;
            _freeLook.YAxis.Value = _freeLook.YAxis.Clamp(y);
            return;
        }

        // No Look action wired (e.g. headless screenshot rigs): legacy demo orbit.
        if (!_warnedNoInput && Application.IsPlaying)
        {
            Debug.LogWarning("[Zonezero] FreeLook: no PlayerInput/Look found — falling back to demo orbit.");
            _warnedNoInput = true;
        }
        _freeLook.XAxis.Value += OrbitSpeedDegreesPerSecond * Time.DeltaTime;
        float yBob = _freeLook.YAxis.Value + VerticalBobSpeed * Time.DeltaTime;
        _freeLook.YAxis.Value = _freeLook.YAxis.Clamp(yBob);
    }
}
