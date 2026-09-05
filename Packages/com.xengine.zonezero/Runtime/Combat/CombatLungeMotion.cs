// This file is part of the XEngine Game Engine
// Licensed under the MIT License. See the LICENSE file in the project root for details.

using System;
using System.Runtime.CompilerServices;

using XEngine.Runtime;

namespace XEngine.Zonezero.Combat;

internal enum CombatAction
{
    None,
    Normal,
    SkillK,
    SkillL,
    SkillIStart,
    SkillIBody,
    SkillIEnd,
}

/// <summary>Pure, allocation-free timing for the code-driven L-skill lunge.</summary>
internal static class CombatLungeMotion
{
    [HotPath]
    [MethodImpl(MethodImplOptions.AggressiveInlining)]
    internal static float ConsumeStepSeconds(
        CombatAction action, float deltaTime, ref float remainingSeconds)
    {
        if (action != CombatAction.SkillL || deltaTime <= 0f || remainingSeconds <= 0f)
            return 0f;

        float movementDeltaTime = CombatMotor.ClampMovementDeltaTime(deltaTime);
        float stepSeconds = Math.Min(movementDeltaTime, remainingSeconds);
        remainingSeconds -= stepSeconds;
        return stepSeconds;
    }
}
