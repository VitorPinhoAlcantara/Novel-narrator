from pathlib import Path

import pytest

from novel_narrator.config import DEFAULTS, load_config, save_config, save_env


@pytest.fixture(autouse=True)
def no_token_in_environment(monkeypatch):
    monkeypatch.delenv("ABS_TOKEN", raising=False)


def test_defaults_without_files(tmp_path):
    config = load_config(tmp_path / "config.toml", tmp_path / ".env")
    assert config == DEFAULTS
    assert config is not DEFAULTS


def test_toml_overrides_only_given_keys(tmp_path):
    file = tmp_path / "config.toml"
    file.write_text('[tts.xtts]\nspeed = 1.0\n[outputs]\npdf = true\n[tts.asr.retry_params."2"]\ntop_p = 0.9\n')
    config = load_config(file, tmp_path / ".env")
    assert config["tts"]["xtts"]["speed"] == 1.0
    assert config["tts"]["xtts"]["temperature"] == 0.55
    assert config["outputs"] == {"audiobook": True, "cbz": True, "pdf": True}
    assert config["tts"]["asr"]["retry_params"]["2"] == {"temperature": 0.7, "top_p": 0.9}


def test_example_file_matches_defaults(tmp_path):
    example = Path(__file__).parent.parent / "config.example.toml"
    assert load_config(example, tmp_path / ".env") == DEFAULTS


def test_save_config_keeps_other_keys(tmp_path):
    file = tmp_path / "config.toml"
    file.write_text('[abs]\nurl = "http://velho"\nssh = "eu@servidor"\n[tts]\nworkers = 2\n')
    save_config({"abs": {"url": "http://novo", "library": "Laura"}}, file)
    config = load_config(file, tmp_path / ".env")
    assert config["abs"]["url"] == "http://novo" and config["abs"]["library"] == "Laura"
    assert config["abs"]["ssh"] == "eu@servidor" and config["tts"]["workers"] == 2
    assert sorted(p.name for p in tmp_path.iterdir()) == ["config.toml"]


def test_save_env_replaces_only_its_key(tmp_path):
    env = tmp_path / ".env"
    env.write_text("OUTRA=1\nABS_TOKEN=velho\n")
    save_env("ABS_TOKEN", "novo", env)
    assert env.read_text().splitlines() == ["OUTRA=1", "ABS_TOKEN=novo"]
    assert load_config(tmp_path / "config.toml", env)["abs"]["token"] == "novo"


def test_token_from_env_file(tmp_path):
    (tmp_path / ".env").write_text('# segredo\nABS_TOKEN="abc123"\n')
    assert load_config(tmp_path / "config.toml", tmp_path / ".env")["abs"]["token"] == "abc123"


def test_environment_wins_over_env_file(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("ABS_TOKEN=do_arquivo\n")
    monkeypatch.setenv("ABS_TOKEN", "do_ambiente")
    assert load_config(tmp_path / "config.toml", tmp_path / ".env")["abs"]["token"] == "do_ambiente"


def test_scalar_in_place_of_table_is_rejected(tmp_path):
    file = tmp_path / "config.toml"
    file.write_text('tts = "x"\n')
    with pytest.raises(ValueError, match="'tts' must be a table"):
        load_config(file, tmp_path / ".env")


def test_invalid_toml_names_the_file(tmp_path):
    file = tmp_path / "config.toml"
    file.write_text("[tts\n")
    with pytest.raises(ValueError, match="config.toml"):
        load_config(file, tmp_path / ".env")
