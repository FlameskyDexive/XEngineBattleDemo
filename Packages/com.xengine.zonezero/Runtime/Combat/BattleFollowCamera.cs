// This file is part of the XEngine Game Engine
// Licensed under the MIT License. See the LICENSE file in the project root for details.

using System;

using System;

using XEngine.Runtime;
using XEngine.Runtime.Resources;
using XEngine.Vector;

namespace XEngine.Zonezero.Combat;

/// <summary>
/// Traditional RPG third-person rig: a fixed 40° down-tilt orbit locked behind its target's
/// horizontal heading, smoothed follow (no user orbiting). Deliberately plain — no Cinemachine
/// pipeline, just deterministic geometry every frame.
/// </summary>
[AddComponentMenu("Zonezero/Battle Follow Camera")]
public sealed class BattleFollowCamera : MonoBehaviour
{
    /// <summary>Followed body.</summary>
    public GameObject? Target;

    /// <summary>Horizontal distance behind the target.</summary>
    public float Distance = 6f;

    /// <summary>Pitch in degrees (down-tilt). Height derives from it so the look vector angle matches.</summary>
    public float PitchDeg = 40f;

    public float FollowSpeed = 9f;
    private bool _snapped;

    public override void Start()
    {
        Target ??= FindHero();
    }

    public override void LateUpdate()
    {
        if (Target == null || !Target.IsValid())
        {
            Target = FindHero();
            if (Target == null) return;
        }

        Float3 heroPos = Target.Transform.Position;

        // Lock to the hero's horizontal heading: camera sits behind, above by tan(pitch)*distance.
        Float3 forward = Target.Transform.Forward;
        Float2 flat = new(forward.X, forward.Z);
        if (Float2.LengthSquared(flat) < 1e-4f) flat = new Float2(0f, 1f);
        flat /= MathF.Sqrt(Float2.LengthSquared(flat));

        float dist = Distance;
        float height = dist * MathF.Tan(PitchDeg * MathF.PI / 180f) + 0.8f;
        Float3 desired = heroPos - new Float3(flat.X, 0f, flat.Y) * dist + new Float3(0f, height, 0f);

        Transform self = Transform;
        if (!_snapped)
        {
            self.Position = desired;
            _snapped = true;
        }
        else
        {
            Float3 current = self.Position;
            float k = Math.Clamp(FollowSpeed * Time.DeltaTime, 0f, 1f);
            self.Position = current + (desired - current) * k;
        }

        // Look at a chest-height point slightly ahead so the hero sits low-center like classic RPGs.
        Float3 lookAt = heroPos + new Float3(0f, 1.15f, 0f) + new Float3(flat.X, 0f, flat.Y) * 0.6f;
        Quaternion look = Quaternion.LookRotation(lookAt - self.Position, Float3.UnitY);
        self.Rotation = look;
    }

    internal static GameObject? FindHero()
    {
        Scene? scene = Scene.Current;
        if (scene == null) return null;
        foreach (GameObject root in scene.RootObjects)
            if (root.GetComponent<HeroCombatController>() != null && root.EnabledInHierarchy)
                return root;
        return null;
    }
}
