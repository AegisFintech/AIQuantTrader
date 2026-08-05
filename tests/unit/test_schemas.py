from __future__ import annotations

from pathlib import Path

import pytest

from aiquanttrader.schemas import SCHEMAS, export_schemas


def test_schema_export_and_check_are_deterministic(tmp_path: Path) -> None:
    written = export_schemas(tmp_path, check=False)

    assert len(written) == len(SCHEMAS)
    assert export_schemas(tmp_path, check=True) == written


def test_schema_check_detects_stale_and_unexpected_files(tmp_path: Path) -> None:
    written = export_schemas(tmp_path, check=False)
    written[0].write_text("{}\n", encoding="utf-8")
    (tmp_path / "unexpected.schema.json").write_text("{}\n", encoding="utf-8")

    with pytest.raises(ValueError, match=r"stale schema.*unexpected schema"):
        export_schemas(tmp_path, check=True)


def test_checked_in_schemas_are_current(project_root: Path) -> None:
    export_schemas(project_root / "schemas", check=True)
