// This file is part of the XEngine Game Engine
// Licensed under the MIT License. See the LICENSE file in the project root for details.

using XEngine.Cinemachine;
using XEngine.Runtime;
using XEngine.Runtime.Resources;
using XEngine.Vector;

namespace XEngine.Zonezero;

/// <summary>
/// Port of the ZZZ EnemyController (zonezero M8): the training-dummy hurt target. OnHit
/// logs and pulses the material tint red for 0.15 s so hits read in captures.
/// </summary>
[AddComponentMenu("Zonezero/Enemy Controller")]
public sealed class EnemyController : MonoBehaviour, IHurt
{
    public int HitCount { get; private set; }
    private float _flashTimer;

    public void OnHit(GameObject attacker)
    {
        HitCount++;
        _flashTimer = 0.15f;
        Debug.Log($"[Zonezero] {GameObject?.Name} hit by {attacker.Name} (#{HitCount})");
    }

    public override void Update()
    {
        if (_flashTimer <= 0f) return;
        _flashTimer -= Time.DeltaTime;
        MeshRenderer? renderer = GetComponentInChildren<MeshRenderer>();
        Material? material = renderer?.Material.Res;
        if (material != null)
        {
            bool flashing = _flashTimer > 0f;
            material.SetColor("_MainColor", new Vector.Color(
                flashing ? 1f : 0.9f, flashing ? 0.3f : 0.35f, flashing ? 0.3f : 0.2f, 1f));
        }
    }
}
