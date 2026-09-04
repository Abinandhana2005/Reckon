"""The committed fixture's bytes are part of the repo, so regeneration must reproduce them.

A drifting fixture would silently change what every classifier test asserts against.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from scripts.generate_fixture import build

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "market.json"


def test_same_seed_produces_the_same_market() -> None:
    assert build() == build()


def test_regenerating_reproduces_the_committed_bytes(tmp_path: Path) -> None:
    # Goes through the CLI rather than build(), because the newline and encoding used to
    # write the file are as much a part of reproducibility as the data itself: the default
    # write_text() emits CRLF in cp1252 on Windows and LF in UTF-8 on Linux.
    out = tmp_path / "market.json"
    subprocess.run(
        [sys.executable, "-m", "scripts.generate_fixture", "--out", str(out)],
        cwd=FIXTURE.parents[1],
        check=True,
        capture_output=True,
    )
    assert out.read_bytes() == FIXTURE.read_bytes()
