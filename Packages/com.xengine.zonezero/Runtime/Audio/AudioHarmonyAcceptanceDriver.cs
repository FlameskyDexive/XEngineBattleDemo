// This file is part of the XEngine Game Engine
// Licensed under the MIT License. See the LICENSE file in the project root for details.

//
// Audio HarmonyOS acceptance driver (plan N9 ohos leg, mirrors the Windows N1 sequence).
// Runs a compact automated stage sequence — silence floor, 440 Hz tone, -20 dB fader
// propagation, 20 dB duck attack/release — through an AudioMixerInstance while capturing
// the device output, verifies the same numeric expectations as the Windows runs, writes
// mono WAV recordings plus a JSON report into the app's writable directory, and logs
// every metric to Debug.Log (visible via hilog).
//



using System.Collections.Generic;
using System.IO;
using System.Threading;
using System.Text;

using System;

using XEngine.Runtime;
using XEngine.Runtime.Audio;
using XEngine.Runtime.Audio.Diagnostics;
using XEngine.Runtime.Audio.Native;
using XEngine.Vector;

namespace XEngine.Zonezero.Audio;

public sealed class AudioHarmonyAcceptanceDriver : MonoBehaviour
{
    private sealed record Stage(string Name, double Seconds);

    private static readonly Stage[] Stages =
    {
        new("floor",  1.5),
        new("tone",   2.5),
        new("fader",  2.5),
        new("duck",   6.0),
    };

    private readonly AudioCaptureBuffer _capture = new();
    private AudioMixer _mixer = null!;
    private AudioMixerInstance _mixerInstance = null!;
    private AudioSource? _tone;
    private double _duckReleasedAt = -1;
    private AudioDuckHandle _duckHandle;

    private int _stageIndex = -1;
    private double _stageTime;
    private long _callbacks;
    private long _callbacksAtStageStart;
    private long _underrunsAtStageStart;
    private readonly List<string> _checks = new();
    private bool _passed = true;
    private bool _done;
    private bool _booted;
    private double _tonePhase;
    private double _holdTime;
    private string _outDir = "";
    private float[] _lastCaptured = Array.Empty<float>();
    private float _toneRms;

    public override void Update()
    {
        if (!_booted)
        {
            _booted = true;
            Boot();
            if (_done) return;
        }

        if (_done)
        {
            _holdTime += Time.DeltaTime;
            if (_holdTime >= 8.0)
                Game.Quit();
            return;
        }

        if (_stageIndex < 0 || _stageIndex >= Stages.Length)
            return;

        _stageTime += Time.DeltaTime;

        // Duck envelope: acquire 0.5 s in (0.4 attack), release after 3 s (0.6 release).
        if (Stages[_stageIndex].Name == "duck")
        {
            if (_duckReleasedAt < 0 && _stageTime >= 0.5)
            {
                _duckHandle = _mixerInstance.Automation.AcquireDuck(
                    _mixerInstance.GetIndex("Music"), 20f, attackSeconds: 0.4f, releaseSeconds: 0.6f);
                _duckReleasedAt = 0;
                Debug.Log("[AUDIO] duck acquired (20 dB, attack 0.4)");
            }
            else if (_duckReleasedAt == 0 && _stageTime >= 3.5)
            {
                _duckReleasedAt = _stageTime;
                _ = _mixerInstance.Automation.ReleaseDuck(ref _duckHandle);
                Debug.Log("[AUDIO] duck released (release 0.6)");
            }
        }

        if (_stageTime >= Stages[_stageIndex].Seconds)
            FinishStage();
    }

    private void Boot()
    {
        // The HarmonyOS player host boots with initializeAudio:false (the backend used to be a
        // no-op), so the driver brings the device audio up itself before the stages run.
        if (!AudioContext.IsInitialized)
        {
            bool ok = AudioContext.TryInitialize(44100, 2, 2048);
            Debug.Log($"[AUDIO] try-init ok={ok} initialized={AudioContext.IsInitialized}");
        }

        _outDir = Application.PersistentDataPath;
        if (string.IsNullOrWhiteSpace(_outDir))
            _outDir = AppContext.BaseDirectory; // player fallback
        Directory.CreateDirectory(_outDir);

        Debug.Log($"[AUDIO] outDir={_outDir}");
        Debug.Log($"[AUDIO] backend={AudioContext.Backend.Name} initialized={AudioContext.IsInitialized} " +
                  $"rate={AudioContext.SampleRate} channels={AudioContext.Channels} " +
                  $"caps={AudioContext.Backend.Capabilities}");

        _mixer = new AudioMixer { Name = "DeviceMixer" };
        _mixerInstance = AudioMixerInstance.Get(_mixer);
        _mixer.Groups.Add(new AudioMixerGroupData { Name = "Music", Parent = AudioMixer.MasterGroupName, VolumeDB = 0f });
        _mixer.Groups.Add(new AudioMixerGroupData { Name = "SFX", Parent = AudioMixer.MasterGroupName, VolumeDB = 0f });
        _mixerInstance.ForceRecompile();

        AudioContext.DataProcess += OnDeviceCallback;

        var cameraGo = new GameObject("AcceptanceCamera");
        _ = cameraGo.AddComponent<Camera>();
        XEngine.Runtime.Resources.Scene.Current?.Add(cameraGo);

        _tone = CreateSource("Tone", "Music", 0.8f);
        _tone.Read += OnToneRead;

        AdvanceStage();
    }

    private AudioSource CreateSource(string name, string group, float volume)
    {
        var go = new GameObject(name);
        var source = go.AddComponent<AudioSource>();
        source.Spatial = false;
        source.Volume = volume;
        source.Mixer = _mixer;
        source.MixerGroup = group;
        XEngine.Runtime.Resources.Scene.Current?.Add(go);
        return source;
    }

    private void OnDeviceCallback(NativeArray<float> data, uint frameCount)
    {
        Interlocked.Increment(ref _callbacks);
        _capture.OnDeviceData(data, frameCount);
    }

    private void OnToneRead(NativeArray<float> framesOut, ulong frameCount, int channels)
    {
        double step = 2.0 * Math.PI * 440.0 / AudioContext.SampleRate;
        int written = 0;
        for (ulong f = 0; f < frameCount; f++)
        {
            float s = (float)(0.8 * Math.Sin(_tonePhase));
            _tonePhase += step;
            if (_tonePhase > 2.0 * Math.PI)
                _tonePhase -= 2.0 * Math.PI;
            for (int c = 0; c < channels; c++)
                framesOut[written++] = s;
        }
    }

    private void AdvanceStage()
    {
        _stageIndex++;
        if (_stageIndex >= Stages.Length)
        {
            WriteReport();
            return;
        }

        _stageTime = 0;
        _callbacksAtStageStart = Interlocked.Read(ref _callbacks);
        _underrunsAtStageStart = AudioContext.UnderrunCount;
        _capture.Begin(AudioContext.Channels, AudioContext.SampleRate);

        switch (Stages[_stageIndex].Name)
        {
            case "tone":
                _tonePhase = 0;
                _tone?.PlayProcedural();
                break;
            case "fader":
                _mixerInstance.SetGroupVolumeDB(_mixerInstance.GetIndex("Music"), -20f);
                break;
            case "duck":
                // Fader left Music at -20 dB: restore the full level so the duck envelope
                // measures one 20 dB reduction against the tone-stage reference.
                _mixerInstance.SetGroupVolumeDB(_mixerInstance.GetIndex("Music"), 0f);
                break;
        }

        Debug.Log($"[AUDIO] stage {_stageIndex}/{Stages.Length - 1} '{Stages[_stageIndex].Name}' begin");
    }

    private void FinishStage()
    {
        string name = Stages[_stageIndex].Name;

        _capture.End();
        float[] captured = _capture.ToInterleavedArray();
        int trimSamples = 2048 * AudioContext.Channels;
        if (captured.Length > trimSamples)
            captured = captured[trimSamples..];
        _lastCaptured = captured;

        var result = AudioAnalysis.Analyze(captured, AudioContext.Channels, AudioContext.SampleRate);
        long stageCallbacks = Interlocked.Read(ref _callbacks) - _callbacksAtStageStart;
        long stageUnderruns = AudioContext.UnderrunCount - _underrunsAtStageStart;

        Debug.Log($"[AUDIO] metric stage={name} peak={result.Peak:0.000000} rms={result.Rms:0.000000} " +
                  $"freq={result.FrequencyHz:0.0} callbacks={stageCallbacks} underruns={stageUnderruns}");

        void Check(string checkName, bool passed, string detail)
        {
            _checks.Add($"{{ \"name\": \"{checkName}\", \"passed\": {(passed ? "true" : "false")}, \"detail\": \"{detail}\" }}");
            Debug.Log($"[AUDIO] check {(passed ? "PASS" : "FAIL")} {checkName}: {detail}");
            if (!passed) _passed = false;
        }

        switch (name)
        {
            case "floor":
                Check("floor.silent", result.Peak < 0.05f, $"peak={result.Peak:0.0000} (expect < 0.05)");
                break;
            case "tone":
                Check("tone.frequency", result.FrequencyHz is > 420 and < 460, $"freq={result.FrequencyHz:0.0} (expect 420..460)");
                Check("tone.level", result.Peak is > 0.3f and < 0.95f && result.Rms > 0.15f, $"peak={result.Peak:0.000} rms={result.Rms:0.000}");
                Check("tone.native-bus", _mixerInstance.UsesNativeBus, $"nativeBus={_mixerInstance.UsesNativeBus}");
                _toneRms = result.Rms; // measured full-level reference for the later stages
                break; // the tone keeps playing through fader and duck
            case "fader":
            {
                float ratio = _toneRms > 0 ? result.Rms / _toneRms : 0f;
                Check("fader.attenuation", ratio is > 0.085f and < 0.115f,
                    $"rms={result.Rms:0.000} ratio={ratio:0.000} vs full {_toneRms:0.000} (expect ~0.1 = -20 dB)");
                break;
            }
            case "duck":
            {
                var ducked = Slice(captured, 1.4, 3.3);   // attack done, release pending
                var recovered = Slice(captured, 4.6, 5.8);// release envelope done
                float duckRatio = _toneRms > 0 ? ducked.Rms / _toneRms : 0f;
                float backRatio = _toneRms > 0 ? recovered.Rms / _toneRms : 0f;
                Check("duck.reduction", duckRatio is > 0.08f and < 0.13f,
                    $"ducked/full={duckRatio:0.000} (20 dB duck)");
                Check("duck.recovered", backRatio is > 0.85f and < 1.15f,
                    $"recovered/full={backRatio:0.000} (release 0.6 s)");
                _tone?.Stop();
                break;
            }
        }

        SaveEvidenceWav($"n0-{_stageIndex:00}-{name}", captured);
        AdvanceStage();
    }

    private AudioAnalysisResult Slice(float[] captured, double fromSec, double toSec)
    {
        int channels = AudioContext.Channels;
        int rate = AudioContext.SampleRate;
        int from = (int)(fromSec * rate) * channels;
        int to = Math.Min((int)(toSec * rate) * channels, captured.Length);
        if (from >= to) from = Math.Max(0, to - channels);
        return AudioAnalysis.Analyze(captured.AsSpan(from, to - from), channels, rate);
    }

    private void SaveEvidenceWav(string stem, float[] data)
    {
        if (data.Length == 0) return;
        int channels = AudioContext.Channels;
        var mono = new float[data.Length / channels];
        for (int i = 0; i < mono.Length; i++)
        {
            float sum = 0;
            for (int c = 0; c < channels; c++)
                sum += data[i * channels + c];
            mono[i] = sum / channels;
        }
        string path = Path.Combine(_outDir, stem + ".wav");
        if (AudioCaptureBuffer.TryWriteWav16(path, mono, 1, AudioContext.SampleRate))
            Debug.Log($"[AUDIO] wav written: {path}");
    }

    private void WriteReport()
    {
        var sb = new StringBuilder();
        sb.Append("{\n  \"node\": \"N0-device\",\n");
        sb.Append($"  \"backend\": \"{AudioContext.Backend.Name}\",\n");
        sb.Append($"  \"sampleRate\": {AudioContext.SampleRate},\n");
        sb.Append($"  \"channels\": {AudioContext.Channels},\n");
        sb.Append($"  \"totalCallbacks\": {Interlocked.Read(ref _callbacks)},\n");
        sb.Append($"  \"underruns\": {AudioContext.UnderrunCount},\n");
        sb.Append($"  \"passed\": {(_passed ? "true" : "false")},\n");
        sb.Append("  \"checks\": [\n    " + string.Join(",\n    ", _checks) + "\n  ]\n}\n");

        string reportPath = Path.Combine(_outDir, "n0-device-report.json");
        File.WriteAllText(reportPath, sb.ToString());
        File.WriteAllText(Path.Combine(_outDir, "n0-device-result.txt"), _passed ? "PASS" : "FAIL");
        _done = true;
        Debug.Log($"[AUDIO] report written: {reportPath} result={(_passed ? "PASS" : "FAIL")}");
    }
}
