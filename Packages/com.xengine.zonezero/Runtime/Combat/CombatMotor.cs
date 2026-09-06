// This file is part of the XEngine Game Engine
// Licensed under the MIT License. See the LICENSE file in the project root for details.

using System;
using System.Runtime.CompilerServices;

using XEngine.Animation;
using XEngine.Runtime;
using XEngine.Runtime.Resources;
using XEngine.Zonezero.Config;
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

    // ---- rpgvfx hero-skill config plumbing -------------------------------------------
    // Config lookup is best-effort: no library asset, unknown hero id, or a failed prefab
    // resolution all degrade to the previous procedural ZonezeroVfx behavior.

    private static readonly System.Collections.Generic.Dictionary<GameObject, HeroSkillConfig?> s_configCache = new();

    /// <summary>The hero's skill config, or null when unconfigured (procedural fallback).</summary>
    public static HeroSkillConfig? ConfigFor(GameObject actor)
    {
        if (s_configCache.TryGetValue(actor, out var cached)) return cached;
        string heroId = ResolveHeroId(actor);
        HeroSkillConfig? config = HeroSkillLibrary.LoadDefault()?.Find(heroId);
        s_configCache[actor] = config;
        return config;
    }

    /// <summary>Explicit HeroId from the controller when set, else inferred from the name.
    /// Controllers' HeroId properties lazily initialize from <see cref="InferHeroIdFromName"/>
    /// (NOT from this method) so this lookup can never recurse.</summary>
    internal static string ResolveHeroId(GameObject actor)
    {
        string explicitId = actor.GetComponent<HeroCombatController>()?.HeroId
                             ?? actor.GetComponent<AllyCombatAI>()?.HeroId
                             ?? "";
        return explicitId.Length > 0 ? explicitId : InferHeroIdFromName(actor.Name);
    }

    /// <summary>Pure name-based hero inference (no component reads — recursion-safe).</summary>
    public static string InferHeroIdFromName(string name)
    {
        if (name.Contains("Corin", StringComparison.OrdinalIgnoreCase)) return "Corin";
        if (name.Contains("Nike", StringComparison.OrdinalIgnoreCase)
            || name.Contains("Nostradamus", StringComparison.OrdinalIgnoreCase)) return "Nostradamus";
        return "Anbi";
    }

    /// <summary>Test hook — drops cached per-actor configs (e.g. after authoring the library).</summary>
    public static void InvalidateConfigCache() => s_configCache.Clear();

    /// <summary>
    /// The imported battle avatars face -Z. Adapt their complete visual hierarchy to the +Z
    /// gameplay convention once, outside the animation binding so clip root channels cannot
    /// overwrite the correction or retarget it into the individual bones.
    /// </summary>
    internal static void ConfigureVisualForward(Animator animator)
    {
        if (animator.SkeletonRoot != null) return;

        GameObject actor = animator.GameObject!;
        GameObject? model = null;
        for (int i = 0; i < actor.Children.Count; i++)
        {
            GameObject candidate = actor.Children[i];
            if (!ContainsSkinnedRenderer(candidate)) continue;
            model = candidate;
            break;
        }
        if (model == null) return;

        bool wasEnabled = animator.Enabled;
        animator.Enabled = false;
        var visual = new GameObject("BattleVisual");
        visual.SetParent(actor, worldPositionStays: false);
        model.SetParent(visual, worldPositionStays: false);
        animator.SkeletonRoot = visual.Transform;
        visual.Transform.LocalRotation = new Quaternion(0f, 1f, 0f, 0f);
        animator.Enabled = wasEnabled;
    }

    private static bool ContainsSkinnedRenderer(GameObject root)
    {
        if (root.GetComponent<SkinnedMeshRenderer>() != null) return true;
        for (int i = 0; i < root.Children.Count; i++)
            if (ContainsSkinnedRenderer(root.Children[i])) return true;
        return false;
    }

    internal static void ConfigureRootMotion(Animator animator)
    {
        // Advance the animation and its displacement on the same bounded clock. Clipping only
        // the capsule delta would discard authored travel while the pose jumps ahead after a stall.
        animator.MaximumDeltaTime = MaxMovementDeltaTime;
        animator.RootMotionPlanar = true;
        animator.ApplyRootMotionRotation = false;
        animator.ApplyRootMotion = true;
    }

    public static void MoveGrounded(CharacterController cc, Float3 direction, float speed)
    {
        // Manual locomotion remains horizontal. Combat consumes animation distances through
        // CombatRootMotion instead. CharacterController.Move performs its own ground probe and
        // slope snap; adding downward velocity to the same sweep can turn a floor contact into a
        // start-of-cast side hit on mesh floors and freeze the actor. One horizontal solve per
        // frame also keeps step/snap correction deterministic. Clamp stalls (asset imports,
        // debugger pauses) so a delayed frame cannot become a visible teleport.
        float dt = ClampMovementDeltaTime(Time.DeltaTime);
        MoveGrounded(cc, direction, speed, dt);
    }

    [HotPath]
    [MethodImpl(MethodImplOptions.AggressiveInlining)]
    internal static float ClampMovementDeltaTime(float deltaTime)
        => Math.Clamp(deltaTime, 0f, MaxMovementDeltaTime);

    /// <summary>Moves for an explicitly consumed duration, used by finite timed movement.</summary>
    [HotPath]
    internal static void MoveGrounded(
        CharacterController cc, Float3 direction, float speed, float stepSeconds)
    {
        cc.Move(direction * speed * Math.Max(stepSeconds, 0f));
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

    /// <summary>Clips the moved segment to the part of this frame inside the hit window.</summary>
    [HotPath]
    internal static bool TryGetHitSweep(Float3 previousPosition, Float3 position,
        float previousTime, float time, out Float3 start, out Float3 end)
        => TryGetHitSweep(previousPosition, position, previousTime, time,
            HitWindowStart, HitWindowEnd, out start, out end);

    [HotPath]
    internal static bool TryGetHitSweep(Float3 previousPosition, Float3 position,
        float previousTime, float time, float windowStart, float windowEnd,
        out Float3 start, out Float3 end)
    {
        start = previousPosition;
        end = position;
        if (time < windowStart || previousTime > windowEnd || time < previousTime || windowEnd < windowStart)
            return false;
        float span = time - previousTime;
        if (span > 1e-6f)
        {
            Float3 delta = position - previousPosition;
            start += delta * Math.Clamp((windowStart - previousTime) / span, 0f, 1f);
            end = previousPosition + delta * Math.Clamp((windowEnd - previousTime) / span, 0f, 1f);
        }
        return true;
    }

    /// <summary>Normal-chain VFX once per swing.</summary>
    public static void SpawnNormalSwingVfx(GameObject attacker, int stage)
    {
        Float3 origin = attacker.Transform.Position + new Float3(0f, 1.05f, 0f)
                        + attacker.Transform.Forward * 0.55f;
        var config = ConfigFor(attacker);
        string path = config?.NormalAttackVfxPath(stage - 1) ?? "";
        if (path.Length > 0
            && RpgVfxSpawner.Spawn(path, origin, attacker.Transform.Forward, config!.VfxScale, config.VfxLifetime) != null)
            return;
        ZonezeroVfx.NormalSlash(origin, attacker.Transform.Forward, stage);
    }

    /// <summary>K skill: crossed energy cuts and a contact ring.</summary>
    public static void SpawnSkillKVfx(GameObject attacker)
    {
        Float3 origin = attacker.Transform.Position + new Float3(0f, 1.05f, 0f);
        var config = ConfigFor(attacker);
        if (config?.SkillKVfxPath.Length > 0
            && RpgVfxSpawner.Spawn(config.SkillKVfxPath, origin, attacker.Transform.Forward, config.VfxScale, config.VfxLifetime) != null)
            return;
        ZonezeroVfx.SkillK(origin, attacker.Transform.Forward);
    }

    /// <summary>L skill: dash streak and oversized finishing cut.</summary>
    public static void SpawnSkillLVfx(GameObject attacker)
    {
        Float3 origin = attacker.Transform.Position + new Float3(0f, 0.18f, 0f);
        var config = ConfigFor(attacker);
        if (config?.SkillLVfxPath.Length > 0
            && RpgVfxSpawner.Spawn(config.SkillLVfxPath, origin, attacker.Transform.Forward, config.VfxScale, config.VfxLifetime) != null)
            return;
        ZonezeroVfx.SkillL(origin, attacker.Transform.Forward);
    }

    /// <summary>Ultimate charge loop (skill I start); config path is expected to be looping.</summary>
    public static void SpawnUltimateChargeVfx(GameObject caster)
    {
        var config = ConfigFor(caster);
        if (config?.SkillIChargeVfxPath.Length > 0
            && RpgVfxSpawner.Spawn(config.SkillIChargeVfxPath, caster.Transform.Position,
                caster.Transform.Forward, config.VfxScale, config.VfxLifetime) != null)
            return;
        ZonezeroVfx.UltimateCharge(caster.Transform.Position);
    }

    /// <summary>Ultimate burst (skill I body).</summary>
    public static void SpawnBigSkillBurstVfx(GameObject caster)
    {
        var config = ConfigFor(caster);
        Float3 origin = caster.Transform.Position + new Float3(0f, 0.9f, 0f);
        if (config?.SkillIBurstVfxPath.Length > 0
            && RpgVfxSpawner.Spawn(config.SkillIBurstVfxPath, origin, caster.Transform.Forward,
                config.VfxScale, config.VfxLifetime) != null)
            return;
        ZonezeroVfx.BigSkillBurst(origin);
    }

    /// <summary>Hit VFX + hurt bookkeeping on the struck dummy.</summary>
    public static void ApplyHit(GameObject attacker, GameObject victim)
    {
        Float3 chest = victim.Transform.Position + new Float3(0f, 0.9f, 0f);
        var config = ConfigFor(attacker);
        if (config?.HitVfxPath.Length > 0
            && RpgVfxSpawner.Spawn(config.HitVfxPath, chest,
                victim.Transform.Position - attacker.Transform.Position, config.VfxScale, config.VfxLifetime) != null)
        {
            // rpgvfx hit effect played — skip the procedural sparks.
        }
        else
        {
            ZonezeroVfx.HitSparks(chest, victim.Transform.Position - attacker.Transform.Position);
        }
        if (victim.GetComponent(typeof(IHurt)) is IHurt hurt)
            hurt.OnHit(attacker);
    }

    /// <summary>Forward-cone overlap test used instead of trigger colliders — the skinned weapon
    /// meshes have no colliders, and range/angle read better than physics shape approximations.</summary>
    public static bool InAttackCone(GameObject attacker, GameObject victim, float range, float halfAngleDeg)
        => InAttackSweep(attacker, victim, attacker.Transform.Position, attacker.Transform.Position,
            range, halfAngleDeg);

    /// <summary>The attack cone swept along the capsule's actual movement, including a crossed target.</summary>
    [HotPath]
    internal static bool InAttackSweep(GameObject attacker, GameObject victim,
        Float3 start, Float3 end, float range, float halfAngleDeg)
    {
        Float3 forward = attacker.Transform.Forward;
        Float2 fwd = new(forward.X, forward.Z);
        float forwardSqr = Float2.LengthSquared(fwd);
        if (forwardSqr < 1e-5f) return false;
        fwd /= MathF.Sqrt(forwardSqr);
        float cosine = MathF.Cos(halfAngleDeg * MathF.PI / 180f);
        Float3 victimPosition = victim.Transform.Position;
        Float2 offset = new(victimPosition.X - start.X, victimPosition.Z - start.Z);
        Float2 segment = new(end.X - start.X, end.Z - start.Z);
        float segmentSqr = Float2.LengthSquared(segment);
        float distanceSqr = Float2.LengthSquared(offset);
        float radiusSqr = range * range;
        if (segmentSqr <= 1e-8f)
            return distanceSqr <= radiusSqr && InFlatCone(offset, fwd, cosine);

        // Restrict the swept origins to those within attack range of the target.
        float along = Float2.Dot(offset, segment);
        float discriminant = along * along - segmentSqr * (distanceSqr - radiusSqr);
        if (discriminant < 0f) return false;
        float root = MathF.Sqrt(discriminant);
        float lower = Math.Max(0f, (along - root) / segmentSqr);
        float upper = Math.Min(1f, (along + root) / segmentSqr);
        if (lower > upper) return false;
        if (InFlatCone(offset - segment * lower, fwd, cosine)
            || InFlatCone(offset - segment * upper, fwd, cosine)) return true;

        // The forward-angle cosine has at most one interior extremum on this segment.
        // Checking it also covers sideways movement where the closest point is outside the cone.
        float toward = Float2.Dot(offset, fwd);
        float travel = Float2.Dot(segment, fwd);
        float denominator = travel * along - toward * segmentSqr;
        if (MathF.Abs(denominator) <= 1e-8f) return false;
        float peak = (travel * distanceSqr - toward * along) / denominator;
        return peak >= lower && peak <= upper && InFlatCone(offset - segment * peak, fwd, cosine);
    }

    [HotPath]
    private static bool InFlatCone(Float2 offset, Float2 forward, float cosine)
    {
        float square = Float2.LengthSquared(offset);
        return square <= 1e-8f || Float2.Dot(offset, forward) >= cosine * MathF.Sqrt(square);
    }
}
