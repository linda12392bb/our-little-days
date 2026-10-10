import json
import pytest

from our_little_days.models import is_night, load_characters, taipei_now


def test_roster_exact_colors_languages_and_supplied_personas():
    characters = load_characters()
    assert [c.name for c in characters] == ["光", "序", "NOEL", "HARU", "REO", "洛陽", "RIXO", "千爺", "MERO", "先生", "阿豪", "聖護", "尤理"]
    assert characters[0].day_bubble == "#F9D48B"
    assert characters[1].language == "ja-Kansai"
    assert characters[2].language == "en"
    assert all(c.source_text and c.tone for c in characters)
    assert characters[0].personality_core is None
    assert characters[0].appearance["height_cm"] == 186


@pytest.mark.parametrize("hour,night", [(0, True), (5, True), (6, False), (23, False)])
def test_theme_boundaries(hour, night):
    assert is_night(hour) is night


def test_taiwan_timezone():
    assert taipei_now().utcoffset().total_seconds() == 8 * 3600


@pytest.mark.parametrize("change", ["missing", "duplicate", "bad_color", "bad_language"])
def test_bad_character_config_is_rejected(tmp_path, change):
    from dataclasses import asdict
    data = [asdict(c) for c in load_characters()]
    if change == "missing":
        data.pop()
    elif change == "duplicate":
        data[1]["id"] = data[0]["id"]
    elif change == "bad_color":
        data[0]["day_bubble"] = "red; invalid"
    else:
        data[0]["language"] = "invalid"
    path = tmp_path / "characters.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError):
        load_characters(path)
