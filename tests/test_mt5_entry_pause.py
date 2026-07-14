from __future__ import annotations

from mt5_entry_pause import entries_paused, flag_path, main, pause_entries, resume_entries


def test_pause_and_resume_entries(tmp_path):
    path = pause_entries(tmp_path)

    assert path == flag_path(tmp_path)
    assert path.read_text(encoding="ascii") == "entry_pause\n"
    assert entries_paused(tmp_path)

    resume_entries(tmp_path)

    assert not entries_paused(tmp_path)


def test_cli_reports_pause_state(tmp_path, capsys):
    assert main(["pause", "--common-dir", str(tmp_path)]) == 0
    assert "entry_trading=paused" in capsys.readouterr().out

    assert main(["status", "--common-dir", str(tmp_path)]) == 0
    assert "entry_trading=paused" in capsys.readouterr().out

    assert main(["resume", "--common-dir", str(tmp_path)]) == 0
    assert "entry_trading=enabled" in capsys.readouterr().out
