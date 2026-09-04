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
/// deltas), like the reference demo's mouse look. When no look action is available the rig remains
/// fixed so a missing input binding cannot silently rotate the camera-relative movement basis.
/// </summary>
[AddComponentMenu("Zonezero/FreeLook Look Driver")]
public sealed class ZonezeroFreeLookDriver : MonoBehaviour
{
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

        // Missing input is a configuration problem, not permission to move the camera. Keep the
        // authored axes unchanged and report it once without adding steady-state log/allocation cost.
        if (!_warnedNoInput && Application.IsPlaying)
        {
            Debug.LogWarning("[Zonezero] FreeLook: no PlayerInput/Look found — camera remains fixed.");
            _warnedNoInput = true;
        }
    }
}
