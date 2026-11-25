"""Per-stage timing instrumentation (ULTRAAI_STAGE_TIMING): off by default, records when on."""

import json
import time

import ultraai.generate as generate
from ultraai import config, models


def test_option_registered_default_off():
    assert config.env_of("stage_timing") == "ULTRAAI_STAGE_TIMING"
    assert config.get("stage_timing") == "0"
    assert config.get_bool("stage_timing") is False


def test_stage_records_and_accumulates_when_enabled(monkeypatch):
    monkeypatch.setenv("ULTRAAI_STAGE_TIMING", "1")
    generate.reset_stage_times()
    assert generate.STAGE_TIMES == {}
    with generate.stage("x"):
        time.sleep(0.01)
    assert generate.STAGE_TIMES["x"] >= 0
    assert generate.STAGE_TIMES["x"] > 0
    first = generate.STAGE_TIMES["x"]
    with generate.stage("x"):  # a second block accumulates into the same stage
        time.sleep(0.01)
    assert generate.STAGE_TIMES["x"] >= first
    generate.reset_stage_times()
    assert generate.STAGE_TIMES == {}


def test_stage_records_even_on_exception(monkeypatch):
    monkeypatch.setenv("ULTRAAI_STAGE_TIMING", "1")
    generate.reset_stage_times()
    try:
        with generate.stage("x"):
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    assert generate.STAGE_TIMES["x"] >= 0
    generate.reset_stage_times()


def test_stage_records_nothing_when_disabled(monkeypatch):
    monkeypatch.setenv("ULTRAAI_STAGE_TIMING", "0")
    generate.reset_stage_times()
    with generate.stage("x"):
        time.sleep(0.01)
    assert generate.STAGE_TIMES == {}
    assert "x" not in generate.STAGE_TIMES


def test_stage_rates_empty_without_bench_data(tmp_path, monkeypatch):
    monkeypatch.setattr(models, "BENCH_FILE", tmp_path / "no-such-file.json")
    assert models.stage_rates("pitch", "swift") == {}
    assert models.stage_rates("separation", "htdemucs") == {}


def test_stage_rates_reads_bench_stages(tmp_path, monkeypatch):
    f = tmp_path / "model_bench.json"
    f.write_text(json.dumps({"pitch": {"swift": {"stages": {"analyse": 12.3, "align": 4.0}}}}))
    monkeypatch.setattr(models, "BENCH_FILE", f)
    assert models.stage_rates("pitch", "swift") == {"analyse": 12.3, "align": 4.0}
    assert models.stage_rates("pitch", "rmvpe") == {}
