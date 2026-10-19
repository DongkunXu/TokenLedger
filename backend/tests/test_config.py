from tokenledger import config
from tokenledger.config import Paths, load_settings


def test_first_run_takes_codexbar_timezone(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "codexbar_timezone", lambda: "Asia/Shanghai")
    monkeypatch.setenv("TZ", "America/New_York")
    assert load_settings(Paths(data=tmp_path)).timezone == "Asia/Shanghai"


def test_first_run_falls_back_to_system_timezone(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "codexbar_timezone", lambda: None)
    monkeypatch.setenv("TZ", "America/New_York")
    settings = load_settings(Paths(data=tmp_path))
    assert settings.timezone == "America/New_York"
    # written once, then kept
    monkeypatch.setenv("TZ", "Asia/Tokyo")
    assert load_settings(Paths(data=tmp_path)).timezone == "America/New_York"


def test_unknown_system_timezone_is_utc(monkeypatch):
    monkeypatch.setenv("TZ", "Not/AZone")
    assert config.system_timezone() == "UTC"
