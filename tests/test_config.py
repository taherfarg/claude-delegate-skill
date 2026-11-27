import pytest

from delegate.cli import main
from delegate.config import DEFAULT_RISK_HIGH, ConfigError, load_config, match_glob


def test_defaults_without_file(tmp_path):
    config = load_config(tmp_path)

    assert config.gates == {}
    assert config.gemini_model == "gemini-3.8-flash-high"
    assert config.timeouts.worker == 900
    assert config.project == tmp_path.name


def test_toml_values_and_risk_extends_defaults(tmp_path):
    (tmp_path / "delegate.toml").write_text(
        '[gates]\ntest = "pytest -q"\n'
        '[risk]\nhigh = ["src/billing/**"]\n'
        '[timeouts]\ngate = 30\n',
        encoding="utf-8",
    )

    config = load_config(tmp_path)

    assert config.gates == {"test": "pytest -q"}
    assert "src/billing/**" in config.risk_high
    assert all(pattern in config.risk_high for pattern in DEFAULT_RISK_HIGH)
    assert config.timeouts.gate == 30
    assert config.timeouts.worker == 900


def test_match_glob():
    assert match_glob("a/b/auth/x.py", "**/auth/**")
    assert match_glob("README.md", "**/*.md")
    assert not match_glob("src/a.py", "*.py")
    assert match_glob("db/migrations/001.sql", "**/migrations/**")


def test_invalid_toml_is_clean_error(tmp_path):
    (tmp_path / "delegate.toml").write_text("[gates", encoding="utf-8")

    with pytest.raises(ConfigError, match=r"delegate\.toml"):
        load_config(tmp_path)


def test_version(capsys):
    main(["--version"])
    assert capsys.readouterr().out.strip() == "2.1.0"
