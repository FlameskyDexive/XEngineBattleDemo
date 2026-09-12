// This file is part of the Zonezero Toon Combat Demo (XEngine test project).
// Licensed under the MIT License. See the LICENSE file in the project root for details.

using System;
using System.Diagnostics;

using XEngine.Async;
using XEngine.Runtime;
using XEngine.Runtime.Resources;
using XEngine.Runtime.UI;
using XEngine.Runtime.Video;
using XEngine.Vector;

namespace XEngine.Zonezero.Video;

/// <summary>
/// HarmonyOS video-playback acceptance driver (video plan N9). Builds the whole
/// acceptance rig procedurally — canvas, RawImage + VideoImageBinding, status text
/// and a VideoPlayer fed from <c>Resources/Video/…</c> clips — so the scene only
/// needs this one component plus a camera.
///
/// Touch contract (status text mirrors these hints on screen):
///   single tap          → pause / resume
///   swipe left / right  → seek ∓1.5 s
///   double tap          → switch source (H.264 MP4 ↔ VP9 WebM)
///   two-finger tap      → stop (next tap prepares + plays again)
///
/// Every state transition and a periodic heartbeat go through Debug.Log so the
/// on-device hilog captures the acceptance trail with timestamps.
/// </summary>
public class VideoHarmonyAcceptanceDriver : MonoBehaviour
{
    private static readonly string[] Sources = ["Video/n0-h264-aac", "Video/n0-vp9-opus"];
    private const float SwipeSeekSeconds = 1.5f;
    private const float TapMaxSeconds = 0.35f;
    private const float SwipeMinPixels = 60f;

    private VideoPlayer? _player;
    private Text? _status;
    private int _sourceIndex;
    private int _loopCount;
    private bool _built;
    private bool _buildFailed;
    private double _lastHeartbeat;
    private double _lastLogWall;
    private double _lastLoggedTime;
    private int _frameAdvances;

    // per-gesture tracking (first finger only; two-finger tap handled by count)
    private int _activePointerId = -1;
    private Float2 _touchStart;
    private double _touchStartTime;
    private double _lastTapTime = -10.0;
    private bool _twoFingerHandled;

    public override void Update()
    {
        if (!_built)
        {
            Build();
            return;
        }
        if (_buildFailed || _player is null)
            return;

        HandleTouch();

        double now = _player.Time;
        if (now > _lastLoggedTime)
        {
            _lastLoggedTime = now;
            _frameAdvances++;
        }

        float wall = Time.UnscaledTotalTime;
        if (_status != null && wall - _lastHeartbeat > 0.25)
        {
            _lastHeartbeat = wall;
            Texture2D? texture = _player.Texture;
            _status.TextValue =
                $"[XENGINE-VIDEO-N9] state={_player.State} t={_player.Time:F2}s/{_player.Length:F2}s " +
                $"loop={_loopCount} ticks={_frameAdvances} src={Sources[_sourceIndex]} " +
                $"res={(texture is null ? "-" : texture.Width + "x" + texture.Height)}\n" +
                "tap=pause/resume  swipe=seek±1.5s  dbl=switch source  2tap=stop";
        }

        if (_player.IsPlaying && wall - _lastLogWall > 5.0)
        {
            _lastLogWall = wall;
            Runtime.Debug.Log($"[VideoN9] heartbeat state={_player.State} t={_player.Time:F2}s ticks={_frameAdvances} loops={_loopCount}");
        }
    }

    private void Build()
    {
        _built = true;
        try
        {
            BuildUi();
            StartPlayback();
            Runtime.Debug.Log("[VideoN9] rig built; playback requested");
        }
        catch (Exception exception)
        {
            _buildFailed = true;
            Runtime.Debug.LogError($"[VideoN9] build failed: {exception.Message}\n{exception.StackTrace}");
        }
    }

    private void BuildUi()
    {
        var canvasGo = new GameObject("VideoAcceptanceCanvas");
        var canvas = canvasGo.AddComponent<GameCanvas>();
        canvas.UIScaleMode = ScaleMode.ScaleWithScreenSize;
        canvas.ReferenceResolution = new Float2(1280f, 720f);
        canvas.MatchWidthOrHeight = 1f;

        var imageGo = new GameObject("VideoRawImage");
        imageGo.SetParent(canvasGo, worldPositionStays: false);
        RectTransform imageRect = imageGo.EnsureRectTransform();
        imageRect.AnchorMin = new Float2(0.05f, 0.18f);
        imageRect.AnchorMax = new Float2(0.95f, 0.95f);
        imageRect.SizeDelta = Float2.Zero;
        imageRect.AnchoredPosition = Float2.Zero;
        var image = imageGo.AddComponent<RawImage>();
        image.Color = Color.White;

        var textGo = new GameObject("VideoStatusText");
        textGo.SetParent(canvasGo, worldPositionStays: false);
        RectTransform textRect = textGo.EnsureRectTransform();
        textRect.AnchorMin = new Float2(0f, 0f);
        textRect.AnchorMax = new Float2(1f, 0.16f);
        textRect.SizeDelta = Float2.Zero;
        textRect.AnchoredPosition = Float2.Zero;
        _status = textGo.AddComponent<Text>();
        _status.TextValue = "[XENGINE-VIDEO-N9] preparing…";
        _status.TextColor = Color.White;

        var playerGo = new GameObject("VideoPlayer");
        playerGo.SetParent(canvasGo, worldPositionStays: false);
        _player = playerGo.AddComponent<VideoPlayer>();
        _player.StateChanged += OnStateChanged;
        _player.PlaybackFailed += OnPlaybackFailed;
        _player.Ended += OnEnded;

        var binding = imageGo.AddComponent<VideoImageBinding>();
        binding.Player = _player;
        binding.Target = image;

        Runtime.Resources.Scene.Current?.Add(canvasGo);
    }

    private void StartPlayback()
    {
        _player!.AudioOutputMode = VideoAudioOutputMode.None; // HarmonyOS engine audio is a no-op; realtime clock drives A/V
        _player.RenderMode = VideoRenderMode.TextureOnly;
        _player.IsLooping = true;
        _player.Source = VideoSource.Clip;
        _player.Clip = GameResources.Load<VideoClip>(Sources[_sourceIndex]);
        if (_player.Clip is null)
            throw new InvalidOperationException("VideoClip resource not found: " + Sources[_sourceIndex]);
        _player.Play();
    }

    private void OnStateChanged(VideoPlaybackState state)
    {
        Runtime.Debug.Log($"[VideoN9] state -> {state} (src={Sources[_sourceIndex]})");
    }

    private void OnPlaybackFailed(VideoErrorCode code, string message)
    {
        Runtime.Debug.LogError($"[VideoN9] playback failed: {code}: {message}");
    }

    private void OnEnded()
    {
        _loopCount++;
        Runtime.Debug.Log($"[VideoN9] Ended event (loop {_loopCount})");
    }

    private void HandleTouch()
    {
        if (Input.TouchCount == 0)
        {
            _twoFingerHandled = false;
            return;
        }

        if (Input.TouchCount >= 2)
        {
            // Two-finger press: stop. Triggered on ANY frame with two touches (the second
            // finger's Began usually lands while the first already reports Stationary).
            // Any further single tap prepares + plays again.
            if (!_twoFingerHandled)
            {
                _twoFingerHandled = true;
                _activePointerId = -1;
                _player!.Stop();
                _frameAdvances = 0;
                _loopCount = 0;
                Runtime.Debug.Log("[VideoN9] two-finger tap → Stop");
            }
            return;
        }

        TouchPoint touch = Input.GetTouch(0);
        switch (touch.Phase)
        {
            case TouchPhase.Began:
                _activePointerId = touch.PointerId;
                _touchStart = touch.Position;
                _touchStartTime = Time.UnscaledTotalTime;
                break;
            case TouchPhase.Ended:
            case TouchPhase.Canceled:
                if (touch.PointerId != _activePointerId)
                    break;
                _activePointerId = -1;
                float dx = touch.Position.X - _touchStart.X;
                double duration = Time.UnscaledTotalTime - _touchStartTime;
                if (duration > TapMaxSeconds || MathF.Abs(dx) > SwipeMinPixels)
                {
                    if (MathF.Abs(dx) > SwipeMinPixels)
                    {
                        double target = Math.Clamp(
                            _player!.Time + (dx > 0f ? SwipeSeekSeconds : -SwipeSeekSeconds),
                            0.0,
                            Math.Max(0.0, _player.Length - 0.05));
                        Runtime.Debug.Log($"[VideoN9] swipe ({dx:F0}px) → SeekAsync({target:F2}s)");
                        SeekAsyncSafe(target).Forget();
                    }
                    break;
                }

                if (Time.UnscaledTotalTime - _lastTapTime < 1.0)
                {
                    _lastTapTime = -10.0;
                    SwitchSource();
                }
                else
                {
                    _lastTapTime = Time.UnscaledTotalTime;
                    TogglePauseResume();
                }
                break;
        }
    }

    private void TogglePauseResume()
    {
        if (_player is null)
            return;
        switch (_player.State)
        {
            case VideoPlaybackState.Playing:
                _player.Pause();
                Runtime.Debug.Log("[VideoN9] tap → Pause");
                break;
            case VideoPlaybackState.Paused:
            case VideoPlaybackState.Ready:
            case VideoPlaybackState.Idle:
            case VideoPlaybackState.Ended:
                _player.Play();
                Runtime.Debug.Log("[VideoN9] tap → Play");
                break;
        }
    }

    private void SwitchSource()
    {
        _sourceIndex = (_sourceIndex + 1) % Sources.Length;
        _frameAdvances = 0;
        _loopCount = 0;
        Runtime.Debug.Log("[VideoN9] double tap → switch source to " + Sources[_sourceIndex]);
        _player!.Stop();
        _player.Clip = GameResources.Load<VideoClip>(Sources[_sourceIndex]);
        if (_player.Clip is null)
            throw new InvalidOperationException("VideoClip resource not found: " + Sources[_sourceIndex]);
        _player.Play();
    }

    private async XTaskVoid SeekAsyncSafe(double seconds)
    {
        try
        {
            await _player!.SeekAsync(seconds);
            Runtime.Debug.Log($"[VideoN9] seek completed → t={_player.Time:F2}s state={_player.State}");
        }
        catch (Exception exception)
        {
            Runtime.Debug.LogError($"[VideoN9] seek failed: {exception.Message}");
        }
    }
}
