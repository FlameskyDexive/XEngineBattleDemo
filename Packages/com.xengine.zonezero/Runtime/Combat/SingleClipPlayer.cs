// This file is part of the XEngine Game Engine
// Licensed under the MIT License. See the LICENSE file in the project root for details.

using XEngine.Runtime;
using XEngine.Runtime.Resources;

namespace XEngine.Zonezero.Combat;

/// <summary>
/// Minimal animation single-test player: plays ONE clip through the pure code-driven path
/// (no AnimatorController / no FSM) on an Animator whose Controller reference has been cleared.
/// Used by the AnimSingleTest scene to bisect "no animation" into FSM-layer vs render-layer.
/// </summary>
[AddComponentMenu("Zonezero/Single Clip Player")]
public sealed class SingleClipPlayer : MonoBehaviour
{
    public AssetRef<AnimationClip> Clip = new();
    public bool Loop = true;

    private Animator? _animator;
    private bool _started;

    /// <summary>Debug/telemetry: normalized time of the played clip.</summary>
    public float DbgNt { get; private set; }

    public override void Start()
    {
        _animator = GetComponent<Animator>();
        var clip = Clip.Res;
        if (_animator == null || clip == null)
        {
            Debug.LogError($"[SingleClipPlayer] missing animator or clip on {GameObject.Name}");
            return;
        }
        // Code-driven play: builds a clip playable on the base mixer, full weight, loop.
        _animator.Play(clip, 0f);
        _started = true;
        Debug.Log($"[SingleClipPlayer] {GameObject.Name} playing '{clip.Name}' dur={clip.Duration:F2}s");
    }

    public override void Update()
    {
        if (!_started || _animator == null) return;
        if (Clip.Res != null && Loop)
            _animator.Wrap = AnimationWrapMode.Loop;
        var state = _animator.GetState(_animator.CurrentClip);
        DbgNt = state != null ? (float)state.NormalizedTime : -1f;
    }
}
