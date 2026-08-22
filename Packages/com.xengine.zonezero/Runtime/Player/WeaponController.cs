// This file is part of the XEngine Game Engine
// Licensed under the MIT License. See the LICENSE file in the project root for details.

using System;
using System.Collections.Generic;

using XEngine.Cinemachine;
using XEngine.Runtime;
using XEngine.Vector;

namespace XEngine.Zonezero;

/// <summary>The ZZZ demo's IHurt marker — anything damageable.</summary>
public interface IHurt
{
    void OnHit(GameObject attacker);
}

/// <summary>
/// Port of the ZZZ WeaponController (zonezero M8): a trigger hitbox on the weapon that
/// damages each enemy at most once per swing (StartHit/StopHit driven by the attack clips'
/// animation events, armed in <see cref="PlayerStateBase.PlayAnimation"/>).
/// </summary>
[AddComponentMenu("Zonezero/Weapon Controller")]
public sealed class WeaponController : MonoBehaviour
{
    /// <summary>Enemy layer name filter (the demo tags dummies as Enemy).</summary>
    public string EnemyTag = "Enemy";

    /// <summary>Known enemy roots (the scene build registers the dummies; the tag match
    /// stays as a fallback for projects that define an "Enemy" tag).</summary>
    public static readonly System.Collections.Generic.HashSet<GameObject> EnemySet = new();


    private readonly HashSet<IHurt> _hitThisSwing = new();
    private Action<IHurt>? _onHit;
    private bool _hitboxActive;

    public void Init(Action<IHurt> onHit) => _onHit = onHit;

    /// <summary>M10 default hit wiring: sparks at the impact + a flash burst on the target
    /// (combat code overrides with Init for custom handling).</summary>
    public void InitDefaultVfx()
    {
        Init(hurt =>
        {
            if (hurt is MonoBehaviour behaviour && behaviour.IsValid())
            {
                XEngine.Zonezero.Vfx.ZonezeroVfx.HitSparks(behaviour.Transform.Position);
                XEngine.Zonezero.Vfx.ZonezeroVfx.HitFlash(behaviour.GameObject!);
            }
        });
    }

    /// <summary>Opens the damage window (clip event StartHit).</summary>
    public void StartHit()
    {
        _hitboxActive = true;
        _hitThisSwing.Clear();
    }

    /// <summary>Closes the damage window (clip event StopHit).</summary>
    public void StopHit()
    {
        _hitboxActive = false;
        _hitThisSwing.Clear();
    }

    public bool IsHitting => _hitboxActive;

    /// <summary>Damage test — the engine trigger dispatch calls this for every overlap.</summary>
    public void ReportOverlap(GameObject other)
    {
        if (!_hitboxActive) return;
        bool isEnemy = EnemySet.Contains(other)
            || string.Equals(other.Tag, EnemyTag, System.StringComparison.OrdinalIgnoreCase);
        if (!isEnemy) return;
        IHurt? hurt = other.GetComponent(typeof(IHurt)) as IHurt;
        if (hurt == null || _hitThisSwing.Contains(hurt)) return;
        _hitThisSwing.Add(hurt);
        _onHit?.Invoke(hurt);
    }

    public override void OnTriggerEnter(Collider other)
    {
        ReportOverlap(other.GameObject!);
    }

    public override void OnTriggerStay(Collider other)
    {
        ReportOverlap(other.GameObject!);
    }
}
