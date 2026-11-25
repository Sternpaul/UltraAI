"""The central option registry (ultraai/config.py): defaults, env overrides, helpers."""

import pytest

from ultraai import config


def test_defaults():
    assert config.get("pitch") == "swift"
    assert config.get("notes") == "singer"
    assert config.get_bool("melisma") is True
    assert config.get_float("minseg") == 0.15
    assert config.get_int("maxseg") == 6
    assert config.get("lead") == ""
    assert config.get_list("pitch_check") == []


def test_env_override_and_call_time_read(monkeypatch):
    # read at call time, not import time: changing the env now must take effect
    monkeypatch.setenv("ULTRAAI_PITCH", "rmvpe")
    assert config.get("pitch") == "rmvpe"
    monkeypatch.setenv("ULTRAAI_M3_TIMES", "asr")
    assert config.get("m3_times") == "asr"
    monkeypatch.setenv("ULTRAAI_PITCH_CHECK", "rmvpe, fcpe ,")
    assert config.get_list("pitch_check") == ["rmvpe", "fcpe"]
    monkeypatch.setenv("ULTRAAI_MINSEG", "0.3")
    assert config.get_float("minseg") == 0.3


def test_default_override_param():
    # call-site-specific historical default (cli.py eval --cached-only uses htdemucs)
    assert config.get("separation", default="htdemucs") == "htdemucs"
    assert config.get("separation") == "melband"


def test_unknown_key_lists_known():
    with pytest.raises(KeyError, match="unknown option"):
        config.get("no_such_option")


def test_env_of_matches_registry():
    assert config.env_of("pitch") == "ULTRAAI_PITCH"
    assert config.env_of("background_units") == "ULTRAAI_BACKGROUND_UNITS"


def test_table_documents_every_option():
    t = config.table()
    for o in config.OPTS:
        assert o.env in t and o.key in t
    # every registered env var is a real ULTRAAI_* var
    assert all(o.env.startswith("ULTRAAI_") for o in config.OPTS)
