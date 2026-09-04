// This file is part of the XEngine Game Engine
// Licensed under the MIT License. See the LICENSE file in the project root for details.

using System;

using XEngine.Animation;
using XEngine.Runtime;
using XEngine.Runtime.Resources;
using XEngine.Zonezero.Vfx;
using XEngine.Vector;

namespace XEngine.Zonezero.Combat;

/// <summary>
/// Battle-actor target lookup: the practice-dummy registry the hero and ally AI pick targets from.
/// Populated by the battle scene builder (every Claymore dummy registers itself) with a lazy scene
/// fallback for play sessions that outlive a builder invocation.
/// </summary>
public static class BattleTargets
{
    private static readonly System.Collections.Generic.List<GameObject> s_enemies = new();
    private static float _rescanAt;

    public static void Register(GameObject enemy)
    {
        if (!s_enemies.Contains(enemy))
            s_enemies.Add(enemy);
    }

    public static void Unregister(GameObject enemy) => s_enemies.Remove(enemy);

    /// <summary>Alive dummy nearest to <paramref name="from"/>. Play-mode clones invalidate the
/// editor-side registrations, so dead entries are pruned and the scene rescanned periodically.</summary>
    public static GameObject? FindNearest(Float3 from, float maxDistance = 40f)
    {
        PruneAndRescan();

        GameObject? best = null;
        float bestSqr = maxDistance * maxDistance;
        for (int i = s_enemies.Count - 1; i >= 0; i--)
        {
            GameObject e = s_enemies[i];
            if (e == null || e.IsDisposed || !e.EnabledInHierarchy)
            {
                s_enemies.RemoveAt(i);
                continue;
            }
            float sqr = Float3.LengthSquared(e.Transform.Position - from);
            if (sqr < bestSqr)
            {
                bestSqr = sqr;
                best = e;
            }
        }

        if (best == null && TimeSinceStartup() > _rescanAt)
        {
            _rescanAt = TimeSinceStartup() + 1f;
            ScanScene();
            return FindNearest(from, maxDistance);
        }
        return best;
    }

    /// <summary>
    /// Picks one active practice dummy without allocating. The caller owns the xorshift state so
    /// multiple allies have independent, deterministic random streams seeded from their identifiers.
    /// </summary>
    public static GameObject? FindRandom(ref uint randomState)
    {
        PruneAndRescan();

        int activeCount = 0;
        for (int i = 0; i < s_enemies.Count; i++)
        {
            GameObject enemy = s_enemies[i];
            if (enemy.EnabledInHierarchy && IsEnemy(enemy))
                activeCount++;
        }

        if (activeCount == 0)
        {
            if (TimeSinceStartup() > _rescanAt)
            {
                _rescanAt = TimeSinceStartup() + 0.5f;
                ScanScene();
            }
            return null;
        }

        uint value = randomState;
        value ^= value << 13;
        value ^= value >> 17;
        value ^= value << 5;
        randomState = value == 0 ? 0xA341316Cu : value;
        int selected = (int)(randomState % (uint)activeCount);

        for (int i = 0; i < s_enemies.Count; i++)
        {
            GameObject enemy = s_enemies[i];
            if (!enemy.EnabledInHierarchy || !IsEnemy(enemy))
                continue;
            if (selected-- == 0)
                return enemy;
        }
        return null;
    }

    private static float TimeSinceStartup() => XEngine.Runtime.Time.TimeSinceStartup;

/// <summary>Allies must never be selected: only components flagged as enemies qualify.</summary>
    public static bool IsEnemy(GameObject go) => go.GetComponent<EnemyController>() != null;

    private static void PruneAndRescan()
    {
        for (int i = s_enemies.Count - 1; i >= 0; i--)
        {
            GameObject e = s_enemies[i];
            if (e == null || e.IsDisposed)
                s_enemies.RemoveAt(i);
        }
        if (s_enemies.Count == 0 && TimeSinceStartup() > _rescanAt)
        {
            _rescanAt = TimeSinceStartup() + 0.5f;
            ScanScene();
        }
    }

    /// <summary>Fallback discovery after a fresh play session (dummy GOs carry EnemyController).</summary>
    private static void ScanScene()
    {
        Scene? scene = Scene.Current;
        if (scene == null) return;
        foreach (GameObject root in scene.RootObjects)
            Collect(root);
    }

    private static void Collect(GameObject go)
    {
        if (go.GetComponent<EnemyController>() != null && !s_enemies.Contains(go))
            s_enemies.Add(go);
        var children = go.Children;
        for (int i = 0; i < children.Count; i++)
            Collect(children[i]);
    }
}

/// <summary>
/// Shared combat-motor plumbing for the hero controller and ally AI: gravity, grounded movement,
/// animator clip playback helpers, damage-window querying and the swing/hit VFX pair.
/// </summary>
public static class CombatMotor
{
    private const float MaxMovementDeltaTime = 1f / 30f;
    public const float HitWindowStart = 0.32f;   // normalized time the blade starts counting
    public const float HitWindowEnd = 0.72f;

    public static void MoveGrounded(CharacterController cc, Float3 direction, float speed)
    {
        // The battle actors are root-motion-free and remain on a walkable arena, so locomotion is
        // intentionally horizontal. CharacterController.Move performs its own ground probe and
        // slope snap; adding downward velocity to the same sweep can turn a floor contact into a
        // start-of-cast side hit on mesh floors and freeze the actor. One horizontal solve per
        // frame also keeps step/snap correction deterministic. Clamp stalls (asset imports,
        // debugger pauses) so a delayed frame cannot become a visible teleport.
        float dt = Math.Clamp(Time.DeltaTime, 0f, MaxMovementDeltaTime);
        cc.Move(direction * speed * dt);
    }

    /// <summary>Smoothly turns a body's flat rotation toward a world direction.</summary>
    public static bool TurnToward(Transform transform, Float3 direction, float degreesPerSecond, float dt)
    {
        Float2 flat = new(direction.X, direction.Z);
        if (Float2.LengthSquared(flat) < 1e-5f) return true;
        Quaternion target = Quaternion.LookRotation(new Float3(flat.X, 0f, flat.Y), Float3.UnitY);
        transform.Rotation = Quaternion.Slerp(transform.Rotation, target,
            Math.Clamp(degreesPerSecond * dt / 180f, 0f, 1f));
        float yawA = Quaternion.ToEuler(transform.Rotation).Y;
        float yawB = Quaternion.ToEuler(target).Y;
        float diff = MathF.Abs(yawA - yawB) % 360f;
        if (diff > 180f) diff = 360f - diff;
        return diff < 8f;
    }

    /// <summary>Crossfades into a named state on the actor's animator; returns false when absent.</summary>
    public static bool Play(Animator animator, string stateName, float fade = 0.08f)
    {
        if (animator == null || !animator.HasState(stateName)) return false;

        // Re-issuing the state that is already current restarts its clip at frame zero and replaces
        // its fade. Locomotion asks every frame, so this guard must also hold while the destination
        // is fading in; otherwise the transition asymptotically restarts forever and the actor
        // moves while visibly frozen on the first/static pose.
        var rt = animator.Runtime;
        if (rt != null && rt.CurrentStateIndex >= 0
            && rt.Constant.States[rt.CurrentStateIndex].NameHash == AnimationNameHash.Hash(stateName))
            return true;

        animator.CrossFade(stateName, fade);
        return true;
    }

    /// <summary>The freshly crossfaded clip finished playing (reached end, fade-in complete).</summary>
    public static bool ClipFinished(Animator animator)
    {
        if (animator == null || animator.CurrentClip == null) return true;
        AnimatorStateInfo info = animator.GetCurrentAnimatorStateInfo();
        bool fading = animator.GetState(animator.CurrentClip) is { } state && state.Weight < 0.99f;
        return info.normalizedTime >= 1f && !fading;
    }

    public static float NormalizedTime(Animator animator)
        => animator?.GetCurrentAnimatorStateInfo().normalizedTime ?? 1f;

    public static bool InHitWindow(Animator animator)
    {
        float t = NormalizedTime(animator);
        return t is >= HitWindowStart and <= HitWindowEnd;
    }

    /// <summary>Normal-chain VFX once per swing.</summary>
    public static void SpawnNormalSwingVfx(GameObject attacker, int stage)
    {
        Float3 origin = attacker.Transform.Position + new Float3(0f, 1.05f, 0f)
                        + attacker.Transform.Forward * 0.55f;
        ZonezeroVfx.NormalSlash(origin, attacker.Transform.Forward, stage);
    }

    /// <summary>K skill: crossed energy cuts and a contact ring.</summary>
    public static void SpawnSkillKVfx(GameObject attacker)
    {
        Float3 origin = attacker.Transform.Position + new Float3(0f, 1.05f, 0f);
        ZonezeroVfx.SkillK(origin, attacker.Transform.Forward);
    }

    /// <summary>L skill: dash streak and oversized finishing cut.</summary>
    public static void SpawnSkillLVfx(GameObject attacker)
    {
        Float3 origin = attacker.Transform.Position + new Float3(0f, 0.18f, 0f);
        ZonezeroVfx.SkillL(origin, attacker.Transform.Forward);
    }

    /// <summary>Hit VFX + hurt bookkeeping on the struck dummy.</summary>
    public static void ApplyHit(GameObject attacker, GameObject victim)
    {
        Float3 chest = victim.Transform.Position + new Float3(0f, 0.9f, 0f);
        ZonezeroVfx.HitSparks(chest, victim.Transform.Position - attacker.Transform.Position);
        if (victim.GetComponent(typeof(IHurt)) is IHurt hurt)
            hurt.OnHit(attacker);
    }

    /// <summary>Forward-cone overlap test used instead of trigger colliders — the skinned weapon
    /// meshes have no colliders, and range/angle read better than physics shape approximations.</summary>
    public static bool InAttackCone(GameObject attacker, GameObject victim, float range, float halfAngleDeg)
    {
        Float3 toVictim = victim.Transform.Position - attacker.Transform.Position;
        Float2 flat = new(toVictim.X, toVictim.Z);
        if (Float2.LengthSquared(flat) > range * range) return false;
        Float3 forward = attacker.Transform.Forward;
        Float2 fwd = new(forward.X, forward.Z);
        if (Float2.LengthSquared(fwd) < 1e-5f) return false;
        float dot = (flat.X * fwd.X + flat.Y * fwd.Y) / (MathF.Sqrt(Float2.LengthSquared(flat)) * MathF.Sqrt(Float2.LengthSquared(fwd)));
        return dot >= MathF.Cos(halfAngleDeg * MathF.PI / 180f);
    }
}
