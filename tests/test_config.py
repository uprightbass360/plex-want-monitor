from pathlib import Path

from app.config import Config


def test_defaults(tmp_path):
    cfg = Config.from_env({})
    assert cfg.port == 8787 and cfg.log_level == "INFO"
    assert cfg.data_dir == Path("data")


def test_env_overrides(tmp_path):
    cfg = Config.from_env({"WM_DATA_DIR": str(tmp_path), "WM_PORT": "9000", "WM_LOG_LEVEL": "debug"})
    assert cfg.data_dir == tmp_path and cfg.port == 9000 and cfg.log_level == "DEBUG"
    assert cfg.db_path == tmp_path / "want.db" and cfg.plex_path == tmp_path / "plex.json"
