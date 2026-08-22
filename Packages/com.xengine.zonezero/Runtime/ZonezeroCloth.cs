// This file is part of the XEngine Game Engine
// Licensed under the MIT License. See the LICENSE file in the project root for details.

using XEngine.MagicaCloth2;

namespace XEngine.Zonezero;

/// <summary>
/// zonezero M11 — attaches the P0 bone cloth to a character's skirt chains (Skirt_01..08
/// topology shared by Anbi/Corin) with thigh sphere colliders. One component per character.
/// </summary>
public static class ZonezeroCloth
{
    /// <summary>Builds the cloth for every Skirt_NN root found under the character.</summary>
    public static int AttachSkirtCloth(XEngine.Runtime.GameObject character)
    {
        var cloth = character.GetComponent<XEngine.MagicaCloth2.BoneCloth>();
        if (cloth == null)
            cloth = character.AddComponent<XEngine.MagicaCloth2.BoneCloth>();
        int chains = 0;
        for (int i = 1; i <= 8; i++)
        {
            string rootName = $"Skirt_{i:00}";
            XEngine.Vector.Transform? root = FindDeep(character.Transform, rootName);
            if (root == null) continue;
            cloth.AddRootBone(root);
            chains++;
        }
        if (chains == 0) return 0;

        // Thigh colliders keep the hem off the legs ( spheres on the L/R thigh bones).
        foreach (string thigh in new[] { "Bip001 L Thigh", "Bip001 R Thigh" })
        {
            XEngine.Vector.Transform? bone = FindDeep(character.Transform, thigh);
            if (bone == null) continue;
            cloth.AddCollider(new XEngine.MagicaCloth2.ClothSphereCollider
            {
                Bone = bone,
                Radius = 0.09f,
            });
        }
        cloth.Initialize();
        return chains;
    }

    private static XEngine.Vector.Transform? FindDeep(XEngine.Vector.Transform t, string name)
    {
        if (t.GameObject.Name == name) return t;
        for (int i = 0; i < t.ChildCount; i++)
        {
            var found = FindDeep(t.GetChild(i), name);
            if (found != null) return found;
        }
        return null;
    }
}
