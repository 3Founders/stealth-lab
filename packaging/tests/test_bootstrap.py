import sys
import types
from pathlib import Path

import pytest

import stealthlab_connect as slc


def _make_fake_backend(root: Path) -> Path:
    backend = root / "backend"
    (backend / "app" / "mcp_server").mkdir(parents=True)
    (backend / "app" / "services").mkdir(parents=True)
    (backend / "app" / "mcp_server" / "server.py").write_text("# fake\n")
    (backend / "app" / "services" / "trace_collector.py").write_text("# fake\n")
    return backend


def test_auto_discovery_finds_this_repo_backend(monkeypatch):
    monkeypatch.delenv("STEALTHLAB_BACKEND_ROOT", raising=False)
    root = slc.find_backend_root()
    assert (root / "app" / "mcp_server" / "server.py").is_file()
    assert (root / "app" / "services" / "trace_collector.py").is_file()
    assert root.name == "backend"


def test_explicit_root_beats_env_var(monkeypatch, tmp_path):
    fake = _make_fake_backend(tmp_path)
    monkeypatch.setenv("STEALTHLAB_BACKEND_ROOT", str(tmp_path / "not-a-backend"))
    assert slc.find_backend_root(fake) == fake.resolve()


def test_env_var_root_respected(monkeypatch, tmp_path):
    fake = _make_fake_backend(tmp_path)
    monkeypatch.setenv("STEALTHLAB_BACKEND_ROOT", str(fake))
    assert slc.find_backend_root() == fake.resolve()


def test_invalid_explicit_root_raises_instead_of_falling_back(monkeypatch, tmp_path):
    monkeypatch.delenv("STEALTHLAB_BACKEND_ROOT", raising=False)
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(slc.BackendRootNotFound) as excinfo:
        slc.find_backend_root(empty)
    assert str(empty) in str(excinfo.value)


def test_invalid_env_root_raises_instead_of_falling_back(monkeypatch, tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv("STEALTHLAB_BACKEND_ROOT", str(empty))
    with pytest.raises(slc.BackendRootNotFound):
        slc.find_backend_root()


def test_no_candidates_at_all_raises_with_guidance(monkeypatch, tmp_path):
    monkeypatch.delenv("STEALTHLAB_BACKEND_ROOT", raising=False)
    monkeypatch.chdir(tmp_path)
    import stealthlab_connect._bootstrap as bootstrap_module

    monkeypatch.setattr(bootstrap_module, "__file__",
                        str(tmp_path / "elsewhere" / "_bootstrap.py"))
    with pytest.raises(slc.BackendRootNotFound) as excinfo:
        slc.find_backend_root()
    message = str(excinfo.value)
    assert "STEALTHLAB_BACKEND_ROOT" in message
    assert "--backend-root" in message or "backend-root" in message


def test_ensure_fronts_sys_path(monkeypatch, tmp_path):
    monkeypatch.delitem(sys.modules, "app", raising=False)
    fake = _make_fake_backend(tmp_path)
    before = list(sys.path)
    try:
        returned = slc.ensure_backend_importable(fake)
        assert returned == fake.resolve()
        assert sys.path[0] == str(fake.resolve())
    finally:
        sys.path[:] = before


def test_ensure_rejects_non_backend_dir(tmp_path):
    with pytest.raises(slc.BackendRootNotFound):
        slc.ensure_backend_importable(tmp_path)


def test_ensure_detects_foreign_app_already_imported(monkeypatch, tmp_path):
    fake = _make_fake_backend(tmp_path)
    foreign = types.ModuleType("app")
    foreign.__path__ = [str(tmp_path / "somewhere-else" / "app")]
    monkeypatch.setitem(sys.modules, "app", foreign)
    with pytest.raises(RuntimeError, match="already imported"):
        slc.ensure_backend_importable(fake)


def test_ensure_accepts_matching_app_already_imported(monkeypatch, tmp_path):
    fake = _make_fake_backend(tmp_path)
    matching = types.ModuleType("app")
    matching.__path__ = [str(fake.resolve() / "app")]
    monkeypatch.setitem(sys.modules, "app", matching)
    before = list(sys.path)
    try:
        slc.ensure_backend_importable(fake)
        assert sys.path[0] == str(fake.resolve())
    finally:
        sys.path[:] = before


def test_get_backend_root_fronts_real_backend(monkeypatch):
    monkeypatch.delenv("STEALTHLAB_BACKEND_ROOT", raising=False)
    root = slc.get_backend_root()
    assert sys.path[0] == str(root)
