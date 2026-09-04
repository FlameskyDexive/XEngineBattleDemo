// This file is part of the XEngine Game Engine
// Licensed under the MIT License. See the LICENSE file in the project root for details.

using System;

using XEngine.Runtime;
using XEngine.Runtime.Resources;
using XEngine.Vector;

namespace XEngine.Zonezero.Combat;

/// <summary>
/// Fixed-heading 2.5D battle camera. It follows the controlled hero's position while preserving a
/// stable world yaw and oblique top-down pitch, so character turns never rotate the play field or
/// remap WASD under the player.
/// </summary>
[AddComponentMenu("Zonezero/Battle Follow Camera")]
public sealed class BattleFollowCamera : MonoBehaviour
{
    /// <summary>Followed hero; resolved by component when omitted from serialized data.</summary>
    public GameObject? Target;

    /// <summary>Horizontal distance from the target on the XZ plane.</summary>
    public float Distance = 8.5f;

    /// <summary>Downward viewing angle measured from the horizontal plane.</summary>
    public float PitchDeg = 50f;

    /// <summary>World-space heading of the camera look direction; zero looks along +Z.</summary>
    public float YawDeg;

    public float FollowSpeed = 8f;
    public float TargetHeight = 1.15f;
    public float LookAhead = 0.75f;

    private bool _snapped;

    public override void Start()
    {
        Target ??= FindHero();
    }

    [HotPath]
    public override void LateUpdate()
    {
        if (Target == null || !Target.IsValid())
        {
            Target = FindHero();
            if (Target == null) return;
        }

        float yaw = YawDeg * MathF.PI / 180f;
        Float3 flatForward = new(MathF.Sin(yaw), 0f, MathF.Cos(yaw));
        float pitch = Math.Clamp(PitchDeg, 20f, 75f) * MathF.PI / 180f;
        float distance = Math.Max(Distance, 0.5f);
        float height = distance * MathF.Tan(pitch);
        Float3 targetPosition = Target.Transform.Position;
        Float3 desiredPosition = targetPosition - flatForward * distance + new Float3(0f, height, 0f);

        Transform self = Transform;
        if (!_snapped)
        {
            self.Position = desiredPosition;
            _snapped = true;
        }
        else
        {
            // Exponential smoothing is frame-rate independent and still allocation-free.
            float blend = 1f - MathF.Exp(-Math.Max(FollowSpeed, 0f) * Time.DeltaTime);
            self.Position += (desiredPosition - self.Position) * blend;
        }

        Float3 lookAt = targetPosition + new Float3(0f, TargetHeight, 0f) + flatForward * LookAhead;
        self.Rotation = Quaternion.LookRotation(lookAt - self.Position, Float3.UnitY);
    }

    internal static GameObject? FindHero()
    {
        Scene? scene = Scene.Current;
        if (scene == null) return null;
        foreach (GameObject root in scene.RootObjects)
        {
            if (root.EnabledInHierarchy && root.GetComponent<HeroCombatController>() != null)
                return root;
        }
        return null;
    }
}
