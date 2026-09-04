// This file is part of the XEngine Game Engine
// Licensed under the MIT License. See the LICENSE file in the project root for details.

using System.Collections.Generic;

using XEngine.Cinemachine;
using XEngine.Runtime;
using XEngine.Vector;

namespace XEngine.Zonezero;

/// <summary>
/// Port of the ZZZ EnemyController (zonezero M8): the training-dummy hurt target. OnHit
/// logs and pulses per-renderer tint for 0.15 s so hits read clearly without mutating shared assets.
/// </summary>
[AddComponentMenu("Zonezero/Enemy Controller")]
public sealed class EnemyController : MonoBehaviour, IHurt
{
    public int HitCount { get; private set; }
    private float _flashTimer;
    private SkinnedMeshRenderer[] _renderers = System.Array.Empty<SkinnedMeshRenderer>();
    private Color[] _baseColors = System.Array.Empty<Color>();
    private bool _isFlashing;

    public override void Start()
    {
        var renderers = new List<SkinnedMeshRenderer>();
        foreach (SkinnedMeshRenderer renderer in GetComponentsInChildren<SkinnedMeshRenderer>())
            renderers.Add(renderer);
        _renderers = renderers.ToArray();
        _baseColors = new Color[_renderers.Length];
        for (int i = 0; i < _renderers.Length; i++)
            _baseColors[i] = _renderers[i].MainColor;
    }

    public void OnHit(GameObject attacker)
    {
        HitCount++;
        _flashTimer = 0.15f;
        SetFlash(true);
        Debug.Log($"[Zonezero] {GameObject?.Name} hit by {attacker.Name} (#{HitCount})");
    }

    public override void Update()
    {
        if (_flashTimer <= 0f) return;
        _flashTimer -= Time.DeltaTime;
        if (_flashTimer <= 0f)
            SetFlash(false);
    }

    public override void OnDisable() => SetFlash(false);

    private void SetFlash(bool flashing)
    {
        if (_isFlashing == flashing) return;
        _isFlashing = flashing;
        for (int i = 0; i < _renderers.Length; i++)
        {
            SkinnedMeshRenderer renderer = _renderers[i];
            if (renderer == null || renderer.IsDisposed) continue;
            Color source = _baseColors[i];
            renderer.MainColor = flashing
                ? new Color(source.R * 1.45f + 0.35f, source.G * 1.45f + 0.35f,
                    source.B * 1.45f + 0.35f, source.A)
                : source;
        }
    }
}
