from importlib.resources import files
from pathlib import Path
import hashlib
import importlib.util
import json
import shutil
import pytest

from our_little_days.models import load_apartment_settings, load_characters


ROOT = Path(__file__).resolve().parents[1]


def test_all_thirteen_standalone_profiles_have_complete_original_sections():
    characters = load_characters()
    directory = files("our_little_days").joinpath("data/characters")
    assert len([item for item in directory.iterdir() if item.name.endswith(".json")]) == 13
    source = (ROOT / "docs/CHARACTER_BRIEF.md").read_text(encoding="utf-8")
    markers = {
        "order": "# 序角色定義", "noel": "# NOEL角色定義", "haru": "# HARU角色定義",
        "reo": "# REO角色定義", "luoyang": "# 洛陽角色定義", "rixo": "# RIXO角色定義",
        "senya": "# 千爺角色定義", "mero": "# MERO角色定義", "sensei": "# 先生角色定義",
        "hao": "# 阿豪角色定義", "yuri": "# 尤理角色定義", "light": "# 💡光｜角色設定",
        "shogo": "# 📖聖護｜かみそりちゃん｜角色設定",
    }
    positions = sorted((source.index(marker), key) for key, marker in markers.items())
    by_id = {c.id: c for c in characters}
    for index, (start, key) in enumerate(positions):
        end = positions[index + 1][0] if index + 1 < len(positions) else len(source)
        if key == "yuri":
            end = source.index("【13聲道規則】", start)
        assert by_id[key].source_text == source[start:end]
        assert json.loads(directory.joinpath(f"{key}.json").read_text(encoding="utf-8"))["id"] == key


def test_original_documents_match_imported_hashes():
    settings = load_apartment_settings()
    for item in settings["source_documents"]:
        assert hashlib.sha256((ROOT / item["path"]).read_bytes()).hexdigest() == item["sha256"]
    assert settings["importance_ranking"] is None
    assert settings["rules"]["image_generation_requires_explicit_create_image_button"]


def test_full_import_audit():
    path = ROOT / "scripts/audit_characters.py"
    spec = importlib.util.spec_from_file_location("audit_characters", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    report = module.audit(ROOT)
    assert report["status"] == "passed"
    assert report["source_text_exact_substrings"] == 13


def test_missing_details_are_unknown_not_invented():
    characters = {c.id: c for c in load_characters()}
    light, shogo = characters["light"], characters["shogo"]
    assert light.identity["age"] is None and light.identity["background"] is None
    assert light.personality_core is None
    assert light.appearance["height_cm"] == 186
    assert light.appearance["hair"] == "黑色短髮"
    assert light.appearance["eyes"] == "琥珀色眼睛"
    assert shogo.identity["age"] is None
    assert shogo.identity["apparent_age"] == "約二十多歲"
    assert shogo.appearance["height_cm"] == 180
    assert shogo.appearance["hair"] == "白色偏銀的中長髮"
    assert shogo.appearance["eyes"] == "金色眼睛"
    assert shogo.appearance["clothing"] is None
    assert shogo.relationships["worldlines"]["brothers"]["dating_confirmed"] is None


@pytest.mark.parametrize("role,field,value", [
    ("light", "decision_rules", []),
    ("light", "behavior", []),
    ("shogo", "personality_core", None),
    ("shogo", "language_rules", {}),
    ("shogo", "relationships", {}),
    ("shogo", "source_text", ""),
    ("light", "appearance", {"height_cm": 180}),
    ("noel", "personality_core", None),
])
def test_integrity_audit_detects_loss_without_repairing_or_inventing(tmp_path, role, field, value):
    spec = importlib.util.spec_from_file_location("integrity_audit", ROOT / "scripts/audit_characters.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    shutil.copytree(ROOT / "src/our_little_days/data/characters", tmp_path / "src/our_little_days/data/characters")
    (tmp_path / "docs").mkdir()
    shutil.copyfile(ROOT / "docs/character-integrity-baseline.json", tmp_path / "docs/character-integrity-baseline.json")
    profile = tmp_path / f"src/our_little_days/data/characters/{role}.json"
    data = json.loads(profile.read_text(encoding="utf-8"))
    data[field] = value
    profile.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    damaged = profile.read_bytes()
    with pytest.raises(ValueError, match=role):
        module.check_profile_integrity(tmp_path)
    assert profile.read_bytes() == damaged
