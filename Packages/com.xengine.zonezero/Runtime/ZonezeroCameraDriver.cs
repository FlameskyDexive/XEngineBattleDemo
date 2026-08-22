// This file is part of the XEngine Game Engine
// Licensed under the MIT License. See the LICENSE file in the project root for details.

using XEngine.Cinemachine;
using XEngine.Runtime;
using XEngine.Vector;

namespace XEngine.Zonezero;

/// <summary>
/// Demo orbit driver for the combat scene's FreeLook rig (zonezero M6 acceptance): spins the
/// X axis and bobs the Y axis so camera motion reads in play-mode captures. The real game
/// feeds the axes from mouse deltas (M7 player controller); this stands in until then.
/// </summary>
[AddComponentMenu("Zonezero/FreeLook Orbit Driver (Demo)")]
public sealed class ZonezeroFreeLookDriver : MonoBehaviour
{
    public float OrbitSpeedDegreesPerSecond = 40f;
    public float VerticalBobSpeed = 0.4f;

    private CinemachineFreeLook? _freeLook;

    public override void Start()
    {
        _freeLook = GameObject?.GetComponent<CinemachineFreeLook>();
    }

    public override void Update()
    {
        if (_freeLook == null) return;
        _freeLook.XAxis.Value += OrbitSpeedDegreesPerSecond * Time.DeltaTime;
        float y = _freeLook.YAxis.Value + VerticalBobSpeed * Time.DeltaTime;
        _freeLook.YAxis.Value = _freeLook.YAxis.Clamp(y);
    }
}
