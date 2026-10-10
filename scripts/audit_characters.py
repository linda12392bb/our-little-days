"""Verify import against the retained original text, without any dialogue API."""
from dataclasses import asdict
import hashlib
import json
from pathlib import Path

from our_little_days.models import load_apartment_settings, load_characters, taipei_now


def check_profile_integrity(root):
    """Compare every supplied field with the retained pre-appearance snapshot."""
    root = Path(root).resolve()
    baseline = json.loads((root / "docs/character-integrity-baseline.json").read_text(encoding="utf-8"))
    folder = root / "src/our_little_days/data/characters"
    paths = {path.stem: path for path in folder.glob("*.json")}
    if set(paths) != set(baseline["profiles"]):
        raise ValueError("角色名單與原始快照不一致。")
    results = {}
    for key, original in baseline["profiles"].items():
        raw = paths[key].read_bytes()
        current = json.loads(raw)
        expected = {**original, **baseline["confirmed_updates"].get(key, {})}
        changed = sorted(field for field in set(original) | set(current)
                         if original.get(field) != current.get(field) or (field in original) != (field in current))
        unexpected = sorted(field for field in set(expected) | set(current)
                            if expected.get(field) != current.get(field) or (field in expected) != (field in current))
        if unexpected:
            raise ValueError(f"{key} 設定遺失或出現未確認修改：{', '.join(unexpected)}")
        digest = hashlib.sha256(raw).hexdigest()
        if key not in baseline["confirmed_updates"] and digest != baseline["hashes"][key]:
            raise ValueError(f"{key} 原始檔案位元組已變更。")
        results[key] = {
            "path": str(paths[key]),
            "sha256": digest,
            "original_sha256": baseline["hashes"][key],
            "changed_fields_since_original": changed,
            "missing_fields": [],
            "unexpected_changes": [],
            "source_text_unchanged": current["source_text"] == original["source_text"],
            "protected_fields_unchanged": all(current.get(field) == value and field in current
                                              for field, value in original.items()
                                              if field not in baseline["authorized_changed_fields"].get(key, [])),
            "personality_core_pending": current["personality_core"] is None,
            "relationship_storage": "獨立結構與原文" if "relationships" in current else "原文保留；未新增結構化關係",
        }
    return results


def audit(root=Path(".")):
    root = root.resolve()
    folder = root / "src/our_little_days/data/characters"
    integrity = check_profile_integrity(root)
    profiles = {c.id: c for c in load_characters()}
    assert len(list(folder.glob("*.json"))) == len(profiles) == 13
    settings = load_apartment_settings()
    assert settings["importance_ranking"] is None
    assert settings["all_channels_enabled"] is True
    document_results = []
    for item in settings["source_documents"]:
        digest = hashlib.sha256((root / item["path"]).read_bytes()).hexdigest()
        assert digest == item["sha256"], f"原文雜湊不符：{item['path']}"
        document_results.append({"path": str(root / item["path"]), "sha256": digest, "matches_retained_original": True})
    source = (root / "docs/CHARACTER_BRIEF.md").read_text(encoding="utf-8")
    markers = {
        "order": "# 序角色定義", "noel": "# NOEL角色定義", "haru": "# HARU角色定義",
        "reo": "# REO角色定義", "luoyang": "# 洛陽角色定義", "rixo": "# RIXO角色定義",
        "senya": "# 千爺角色定義", "mero": "# MERO角色定義", "sensei": "# 先生角色定義",
        "hao": "# 阿豪角色定義", "yuri": "# 尤理角色定義", "light": "# 💡光｜角色設定",
        "shogo": "# 📖聖護｜かみそりちゃん｜角色設定",
    }
    positions = sorted((source.index(marker), key) for key, marker in markers.items())
    for index, (start, key) in enumerate(positions):
        end = positions[index + 1][0] if index + 1 < len(positions) else len(source)
        if key == "yuri":
            end = source.index("【13聲道規則】", start)
        assert profiles[key].source_text == source[start:end], f"{key} 的原文段落不完整"
    for character in profiles.values():
        assert character.source_text and character.source_text in source, character.id
        assert character.speech_label.endswith("：")
    assert profiles["luoyang"].language == "ja"
    assert profiles["luoyang"].language_rules["fixed_japanese_override"] is True
    assert profiles["reo"].language_rules["register"] == "全敬語"
    assert profiles["reo"].self_reference == "僕"
    assert profiles["rixo"].language_rules["register"] == "粗魯不良"
    assert profiles["rixo"].self_reference == "俺"
    assert profiles["order"].language == "ja-Kansai"
    assert profiles["order"].language_rules["register"] == "自然關西腔"
    for key in ["order", "noel", "haru", "reo", "luoyang", "rixo", "mero", "sensei", "shogo"]:
        assert profiles[key].translation_required
        assert profiles[key].narration_language == profiles[key].language
    worlds = profiles["shogo"].relationships["worldlines"]
    assert set(worlds) == {"main", "eleven_months", "glasshouse", "brothers"}
    assert settings["active_worldline"] == "main"
    assert worlds["main"]["dating_confirmed"] and worlds["main"]["date_agreement"]
    assert "之前" in worlds["main"]["timeline_anchor"]
    assert worlds["eleven_months"]["cohabiting"]
    assert not worlds["eleven_months"]["engaged"] and not worlds["eleven_months"]["married"]
    assert not worlds["glasshouse"]["dating_confirmed"]
    assert worlds["brothers"]["dating_confirmed"] is None
    assert worlds["brothers"]["memories"]
    assert profiles["light"].personality_core is None
    assert profiles["light"].appearance["height_cm"] == 186
    assert profiles["light"].appearance["height_confirmed"] is True
    assert profiles["shogo"].appearance["height_cm"] == 180
    assert profiles["shogo"].appearance["height_confirmed"] is True
    assert profiles["shogo"].identity["age"] is None
    return {
        "checked_at_taipei": taipei_now().isoformat(),
        "status": "passed",
        "profile_count": len(profiles),
        "files": [str(folder / f"{key}.json") for key in profiles],
        "source_documents": document_results,
        "source_text_exact_substrings": 13,
        "profile_integrity": integrity,
        "language_checks": {key: asdict(profiles[key])["language_rules"] for key in ["luoyang", "reo", "rixo", "order"]},
        "active_worldline": settings["active_worldline"],
        "shogo_worldlines": worlds,
        "pending": {key: list(profiles[key].pending_details) for key in ["light", "shogo"]},
        "appearances": {key: profiles[key].appearance for key in ["light", "shogo"]},
        "limitations": ["未匯入未提供的外部互動記憶", "設定驗證不代表模型實際發言已通過人格／方言驗收", "未啟動對話引擎或呼叫 API"],
    }


if __name__ == "__main__":
    report = audit()
    destination = Path("artifacts/character-import-report.json")
    destination.parent.mkdir(exist_ok=True)
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
