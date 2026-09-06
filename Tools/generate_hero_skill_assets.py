#!/usr/bin/env python3
"""Generates the zonezero hero-skill config assets (Echo .asset + .meta).

Outputs under ZonezeroTestProject/Assets/Resources/Zonezero/:
  HeroSkillAnbi.asset / HeroSkillCorin.asset / HeroSkillNostradamus.asset
  HeroSkillLibrary.asset        (references the three by deterministic GUID)

GUIDs are deterministic (uuid5 of the relative path) so re-runs are stable.
Effect paths address rpgvfx package prefabs; the per-hero assignment keeps all
18 effects unique across the three heroes (see the plan's assignment table).

Usage: py Tools/generate_hero_skill_assets.py   (from ZonezeroTestProject root)
"""
import json
import os
import re
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)
OUT_DIR = os.path.join(PROJECT, "Assets", "Resources", "Zonezero")

RPG = "Packages/com.xengine.rpgvfx/Assets/Prefabs/"


def guid_for(rel_path: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "zonezero:" + rel_path))


def meta(path: str, guid: str) -> None:
    with open(path + ".meta", "w", encoding="utf-8", newline="\n") as f:
        json.dump({"guid": guid, "importer": "ScriptableObjectImporter", "importerVersion": 1},
                  f, indent=2)
        f.write("\n")


def write_asset(name: str, fields: dict, class_name: str) -> str:
    rel = "Resources/Zonezero/" + name + ".asset"
    guid = guid_for(rel)
    body = {"$id": 1,
            "$type": "XEngine.Zonezero.Config." + class_name + ", XEngine.Zonezero.Runtime"}
    body.update(fields)
    body["AssetPath"] = rel
    body["AssetID"] = "00000000-0000-0000-0000-000000000000"
    body["Name"] = name
    path = os.path.join(OUT_DIR, name + ".asset")
    os.makedirs(OUT_DIR, exist_ok=True)
    # Echo float tokens need the "F" suffix ("4.6F"), which strict JSON can't express:
    # floats are emitted as sentinel strings ('"4.6F"') then unquoted in the text.
    dumped = json.dumps(body, indent=2)
    dumped = re.sub(r'"(-?[0-9]+(?:[.][0-9]+)?)F"', r'\1F', dumped)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(dumped + "\n")
    meta(path, guid)
    print("wrote", rel, guid)
    return guid


def hero_fields(hero_id, normal_paths, skill_k, skill_l, charge, burst, hit):
    return {
        "HeroId": hero_id,
        "RunSpeed": "4.6F", "TurnSpeedDeg": "540.0F", "AttackRange": "2.0F", "AttackHalfAngleDeg": "65.0F",
        "NormalAttackCooldown": "0.18F", "SkillKCooldown": "1.25F", "SkillLCooldown": "2.25F",
        "SkillICooldown": "7.0F",
        "HitWindowStart": "0.32F", "HitWindowEnd": "0.72F",
        "SkillLHitWindowStart": "0.04F", "SkillLHitWindowEnd": "0.40F",
        "Damage": 10,
        "NormalAttackVfxPaths": {"$values": [RPG + p + ".prefab" for p in normal_paths]},
        "SkillKVfxPath": RPG + skill_k + ".prefab",
        "SkillLVfxPath": RPG + skill_l + ".prefab",
        "SkillIChargeVfxPath": RPG + charge + ".prefab",
        "SkillIBurstVfxPath": RPG + burst + ".prefab",
        "HitVfxPath": RPG + hit + ".prefab",
        "VfxScale": "1.0F", "VfxLifetime": "4.0F",
    }


def main():
    # Assignment table — 18 distinct effects, no repeats across heroes.
    anbi = write_asset("HeroSkillAnbi", hero_fields(
        "Anbi",
        normal_paths=["AOE_Magic_spells_Vol.1_Prefabs_Flower_slash"],   # combo reuse is per-hero
        skill_k="AOE_Magic_spells_Vol.1_Prefabs_Knife_hit",
        skill_l="AAA_Projectiles_Vol_1_Prefabs_Hit_12",
        charge="Magic_circles_Prefabs_Loop_version_Magic_circle_1_loop",
        burst="AOE_Magic_spells_Vol.1_Prefabs_Meteor_2",
        hit="AAA_Projectiles_Vol_1_Prefabs_Hit_5",
    ), "HeroSkillConfig")
    corin = write_asset("HeroSkillCorin", hero_fields(
        "Corin",
        normal_paths=["AOE_Magic_spells_Vol.1_Prefabs_Front_spikes_attack"],
        skill_k="RPG_VFX_Bundle_Prefabs_Magic_buffs_and_hits_Punch_Hit",
        skill_l="AOE_Magic_spells_Vol.1_Prefabs_Knives",
        charge="Magic_circles_Prefabs_Loop_version_Magic_shield_1_loop",
        burst="AOE_Magic_spells_Vol.1_Prefabs_Meteor_hit",
        hit="AAA_Projectiles_Vol_1_Prefabs_Hit_14",
    ), "HeroSkillConfig")
    nostradamus = write_asset("HeroSkillNostradamus", hero_fields(
        "Nostradamus",
        normal_paths=["RPG_VFX_Bundle_Prefabs_Magic_buffs_and_hits_Magic_Sparks"],
        skill_k="AOE_Magic_spells_Vol.1_Prefabs_Lightning_strike",
        skill_l="AOE_Magic_spells_Vol.1_Prefabs_Energy_explosion",
        charge="Magic_circles_Prefabs_Loop_version_Magic_circle_10_loop",
        burst="AOE_Magic_spells_Vol.1_Prefabs_Meteor",
        hit="AAA_Projectiles_Vol_1_Prefabs_Hit_27",
    ), "HeroSkillConfig")

    write_asset("HeroSkillLibrary", {
        "Heroes": {"$values": [{"AssetID": g} for g in (anbi, corin, nostradamus)]},
    }, "HeroSkillLibrary")


if __name__ == "__main__":
    main()
