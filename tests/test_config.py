import pytest

from airmouse import config


def test_repo_config_has_every_key():
    # ค่าใน config.toml ผู้ใช้ปรับเองได้ (เช่น hand) แต่ต้องมีครบทุก key ของ dataclass
    import dataclasses
    import tomllib
    with config.DEFAULT_CONFIG_PATH.open("rb") as fh:
        data = tomllib.load(fh)
    for section in dataclasses.fields(config.Config):
        expected = {f.name for f in dataclasses.fields(getattr(config.Config(), section.name))}
        assert set(data[section.name]) == expected, section.name
    config.load(config.DEFAULT_CONFIG_PATH)  # ต้องโหลดได้ไม่ error


def test_missing_file_uses_defaults(tmp_path):
    assert config.load(tmp_path / "nope.toml") == config.Config()


def test_partial_file_overrides_only_given_keys():
    cfg = config.from_dict({"camera": {"hand": "left"}, "gesture": {"curl_down": 1}})
    assert cfg.camera.hand == "left"
    assert cfg.camera.width == 640
    assert cfg.gesture.curl_down == 1.0
    assert isinstance(cfg.gesture.curl_down, float)


def test_unknown_keys_are_ignored():
    cfg = config.from_dict({"camera": {"zoom": 2}, "nope": {"a": 1}})
    assert cfg == config.Config()


@pytest.mark.parametrize("data", [
    {"camera": {"hand": "both"}},
    {"camera": {"width": "640"}},
    {"camera": {"mirror": 1}},
    {"gesture": {"confirm_frames": 2.5}},
])
def test_invalid_values_raise(data):
    with pytest.raises(ValueError):
        config.from_dict(data)


def test_save_roundtrip(tmp_path):
    cfg = config.from_dict({"gesture": {"pinch_down": 0.03}, "app": {"sounds": False}})
    path = tmp_path / "c.toml"
    config.save(cfg, path)
    assert config.load(path) == cfg
