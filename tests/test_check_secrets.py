from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check_secrets.sh"


def _run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def _init_tracked_file(repo: Path, relative_path: str, content: str) -> Path:
    subprocess.run(
        ["git", "init"],
        cwd=repo,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )
    path = repo / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    subprocess.run(
        ["git", "add", relative_path],
        cwd=repo,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )
    return path


def test_check_secrets_clean_repo():
    result = _run([str(SCRIPT)], ROOT)
    assert result.returncode == 0, result.stdout + result.stderr


def test_check_secrets_flags_hardcoded_key(tmp_path):
    secret_line = "T3N_API" + "_KEY=0xdeadbeef\n"
    _init_tracked_file(tmp_path, "app.py", secret_line)

    result = _run([str(SCRIPT)], tmp_path)

    combined = result.stdout + result.stderr
    assert result.returncode == 1
    assert "app.py:1" in result.stdout
    assert "T3N_API_KEY" in result.stdout
    assert "0xdeadbeef" not in combined


def test_check_secrets_allow_in_tests(tmp_path):
    secret_line = "T3N_API" + "_KEY=0xdeadbeef\n"
    _init_tracked_file(tmp_path, "tests/fixture.py", secret_line)

    result = _run([str(SCRIPT), "--allow-in-tests"], tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr


def test_check_secrets_no_leak_in_repo():
    result = _run(
        [
            "git",
            "grep",
            "-IlnE",
            r"(^|[^0-9A-Fa-f])[0-9A-Fa-f]{64}([^0-9A-Fa-f]|$)",
            "--",
            "*.py",
            "*.sh",
            "*.md",
            "*.json",
        ],
        ROOT,
    )

    if result.returncode == 1:
        return

    assert result.returncode == 0, result.stderr
    files = "\n".join(line for line in result.stdout.splitlines() if line)
    raise AssertionError(f"leak detected in tracked files:\n{files}")


def test_check_secrets_masks_output(tmp_path):
    secret = "a" * 64
    _init_tracked_file(tmp_path, "leak.md", f"token={secret}\n")

    result = _run([str(SCRIPT)], tmp_path)

    combined = result.stdout + result.stderr
    assert result.returncode == 1
    assert secret not in combined
    assert "aaaa" in result.stdout
