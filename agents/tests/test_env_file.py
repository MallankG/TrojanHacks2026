"""`agents/.env` is loaded at import without overriding the real environment."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from tracelayer_agents import config
from tracelayer_agents.config import ENV_FILE, AgentConfig, load_env_file

FROM_FILE = "TRACELAYER_TEST_FROM_FILE"
ALREADY_SET = "TRACELAYER_TEST_ALREADY_SET"


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch):
    # setenv first so monkeypatch restores (removes) whatever load_env_file adds
    for name in (FROM_FILE, ALREADY_SET, "TRACELAYER_PRECOMPUTED"):
        monkeypatch.setenv(name, "")
        monkeypatch.delenv(name)
    return monkeypatch


def test_env_file_is_agents_dotenv() -> None:
    assert ENV_FILE == Path(config.__file__).resolve().parent.parent / ".env"
    assert ENV_FILE.parent.joinpath("pyproject.toml").is_file()


def test_loads_file_without_overriding_real_env(tmp_path: Path, clean_env) -> None:
    env = tmp_path / ".env"
    env.write_text(
        f"{FROM_FILE}=from_file\n{ALREADY_SET}=from_file\nTRACELAYER_PRECOMPUTED=1\n"
    )
    clean_env.setenv(ALREADY_SET, "from_shell")

    assert load_env_file(env) is True
    assert os.environ[FROM_FILE] == "from_file"
    assert os.environ[ALREADY_SET] == "from_shell"  # the real env wins
    assert AgentConfig.from_env().use_precomputed is True  # settings read from the file


def test_missing_file_is_a_no_op(tmp_path: Path, clean_env) -> None:
    assert load_env_file(tmp_path / "missing.env") is False
    assert FROM_FILE not in os.environ
