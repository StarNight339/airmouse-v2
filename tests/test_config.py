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
    {"gesture": {"switcher_step": 0}},
    {"gesture": {"switcher_step": -0.4}},
    {"gesture": {"switcher_hold_ms": -1}},
])
def test_invalid_values_raise(data):
    with pytest.raises(ValueError):
        config.from_dict(data)


def test_save_roundtrip(tmp_path):
    cfg = config.from_dict({"gesture": {"pinch_down": 0.03}, "app": {"sounds": False}})
    path = tmp_path / "c.toml"
    config.save(cfg, path)
    assert config.load(path) == cfg


# ---------------------------------------------------------------- update_toml_text / update_values
SAMPLE = (
    "[gesture]\n"
    "  curl_down = 0.70          # กำมือ (clutch)\n"
    "curl_up = 0.82            # ออก\n"
    "\n"
    "[snap]\n"
    "curl_down = 9.0   # ชื่อซ้ำคนละ section\n"
    "enabled = true\n"
    "\n"
    "[camera]\n"
    'hand = "left"             # "right" | "left"\n'
)


def test_update_replaces_only_value_token():
    out = config.update_toml_text(SAMPLE, {("gesture", "curl_down"): 0.0225})
    assert out == SAMPLE.replace("0.70 ", "0.0225 ", 1)  # ต่างกันแค่ token เดียว
    assert "  curl_down = 0.0225          # กำมือ (clutch)\n" in out
    assert "curl_down = 9.0   # ชื่อซ้ำคนละ section" in out  # section อื่นไม่ถูกแตะ


def test_update_formats_types():
    out = config.update_toml_text(SAMPLE, {("snap", "enabled"): False,
                                           ("camera", "hand"): "right",
                                           ("gesture", "curl_up"): 1})
    assert "enabled = false\n" in out
    assert 'hand = "right"             # "right" | "left"\n' in out
    assert "curl_up = 1            # ออก\n" in out


def test_update_preserves_crlf():
    text = SAMPLE.replace("\n", "\r\n")
    out = config.update_toml_text(text, {("gesture", "curl_up"): 0.8, ("gesture", "new_key"): 3})
    assert "\n" not in out.replace("\r\n", "")
    assert "curl_up = 0.8            # ออก\r\n" in out
    assert "new_key = 3\r\n" in out


def test_update_appends_missing_key_to_section_end():
    out = config.update_toml_text(SAMPLE, {("gesture", "extra"): 5})
    assert "curl_up = 0.82            # ออก\nextra = 5\n\n[snap]" in out
    # ท้ายไฟล์ไม่มี newline: ต้องไม่เติมให้
    out = config.update_toml_text('[a]\nx = 1', {("a", "y"): 2})
    assert out == "[a]\nx = 1\ny = 2"


def test_update_appends_missing_section():
    out = config.update_toml_text(SAMPLE, {("meta", "calibrated"): "2026-10-27T10:00:00"})
    assert out == SAMPLE + '\n[meta]\ncalibrated = "2026-10-27T10:00:00"\n'
    assert config.update_toml_text("", {("a", "b"): 1}) == "[a]\nb = 1\n"


def test_update_string_with_hash_in_old_value():
    out = config.update_toml_text('[a]\nk = "x # y"   # c\n', {("a", "k"): "z"})
    assert out == '[a]\nk = "z"   # c\n'


def test_update_values_writes_and_backs_up(tmp_path):
    path = tmp_path / "config.toml"
    path.write_bytes(SAMPLE.encode("utf-8"))
    config.update_values(path, {("gesture", "curl_down"): 0.6, ("meta", "calibrated"): "t"})
    assert path.with_suffix(".toml.bak").read_bytes() == SAMPLE.encode("utf-8")
    cfg = config.load(path)
    assert cfg.gesture.curl_down == 0.6 and cfg.meta.calibrated == "t"
    assert "# กำมือ (clutch)" in path.read_text(encoding="utf-8")
    assert not (tmp_path / "config.toml.tmp").exists()


@pytest.mark.parametrize("updates", [
    {("camera", "hand"): "both"},
    {("gesture", "confirm_frames"): 2.5},
    {("gesture", "no_such_key"): 1},
    {("nope", "x"): 1},
])
def test_update_values_invalid_leaves_file_untouched(tmp_path, updates):
    path = tmp_path / "config.toml"
    path.write_bytes(SAMPLE.encode("utf-8"))
    with pytest.raises(ValueError):
        config.update_values(path, updates)
    assert path.read_bytes() == SAMPLE.encode("utf-8")
    assert not path.with_suffix(".toml.bak").exists()


def test_project_root_next_to_exe_when_frozen(tmp_path):
    exe = tmp_path / "dist" / "airmouse" / "airmouse.exe"
    mod = tmp_path / "repo" / "src" / "airmouse" / "config.py"
    assert config.project_root(True, str(exe), str(mod)) == exe.parent.resolve()
    assert config.project_root(False, str(exe), str(mod)) == (tmp_path / "repo").resolve()
