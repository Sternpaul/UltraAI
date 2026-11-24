"""report.csv handling of ultraai.batch: append-only, resumable, no duplicate rows."""

import csv

from ultraai import batch


def _row(file, status="ok", **kw):
    row = {f: "" for f in batch.REPORT_FIELDS}
    row.update(file=file, artist="Artist", title="Title", status=status, **kw)
    return row


def _lines(report):
    return report.read_text(encoding="utf-8").splitlines()


def test_two_songs_append_header_once(tmp_path):
    report = tmp_path / "report.csv"
    existing = batch.load_report(report)
    assert existing == {}
    assert batch.append_report_row(report, _row("a.mp3"), existing)
    assert batch.append_report_row(report, _row("b.mp3", status="error", note="no lyrics"), existing)
    lines = _lines(report)
    assert lines[0] == ",".join(batch.REPORT_FIELDS)
    assert len(lines) == 3  # header + exactly one row per song


def test_rerun_does_not_duplicate_rows(tmp_path):
    report = tmp_path / "report.csv"
    existing = batch.load_report(report)
    row = _row("a.mp3")
    assert batch.append_report_row(report, row, existing)
    # same row again (resumed run): nothing appended
    assert not batch.append_report_row(report, dict(row), existing)
    assert len(_lines(report)) == 2
    # a changed row (song re-processed with another outcome) is appended, last row wins on load
    changed = _row("a.mp3", status="error", note="boom", seconds=5)
    assert batch.append_report_row(report, changed, existing)
    assert len(_lines(report)) == 3
    loaded = batch.load_report(report)
    assert set(loaded) == {"a.mp3"}
    assert loaded["a.mp3"]["status"] == "error"
    assert loaded["a.mp3"]["note"] == "boom"


def test_load_report_keyed_by_file_and_ok_resume(tmp_path):
    report = tmp_path / "report.csv"
    with open(report, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=batch.REPORT_FIELDS)
        w.writeheader()
        w.writerow(_row("a.mp3", status="error"))
        w.writerow(_row("a.mp3", status="ok"))  # re-run: last row wins
        w.writerow(_row("b.mp3", status="error"))
    loaded = batch.load_report(report)
    assert set(loaded) == {"a.mp3", "b.mp3"}
    assert loaded["a.mp3"]["status"] == "ok"
    # resume semantics: only songs with status "ok" are skipped (run_batch uses exactly this)
    done = {k for k, r in loaded.items() if r.get("status") == "ok"}
    assert done == {"a.mp3"}
