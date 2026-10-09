"""A flag that needs an optional extra is could-not-run (2) when the extra is
missing: `audit --signal` skipped its checks and exited on the structural
answer, and `lib index --features` indexed everything and called each file
"produced no features", exit 0."""
import pytest

from acidcat.cli import main
from acidcat.util import deps


@pytest.fixture
def no_extra(monkeypatch):
    monkeypatch.setattr(deps, "require", lambda *a, **k: False)


def _wav(tmp_path):
    p = tmp_path / "t.wav"
    p.write_bytes(b"RIFF" + (36).to_bytes(4, "little") + b"WAVEfmt "
                  + (16).to_bytes(4, "little") + bytes.fromhex("01000100401f0000803e000002001000")
                  + b"data" + bytes(4))
    return p


def test_audit_signal_without_numpy_is_2(tmp_path, no_extra):
    assert main(["audit", "--signal", str(_wav(tmp_path))]) == 2


def test_lib_index_features_without_analysis_is_2(tmp_path, monkeypatch, no_extra):
    monkeypatch.setenv("ACIDCAT_HOME", str(tmp_path / "home"))
    _wav(tmp_path)
    assert main(["lib", "index", str(tmp_path), "--features", "-q"]) == 2


def test_without_the_flag_the_extra_is_not_needed(tmp_path, no_extra):
    assert main(["audit", str(_wav(tmp_path))]) in (0, 1)
