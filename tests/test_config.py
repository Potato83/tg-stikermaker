from pathlib import Path

import pytest

from sticker_maker.config import load_config, with_overrides


def test_load_partial_toml_uses_dataclass_section_defaults(tmp_path: Path) -> None:
    path = tmp_path / "settings.toml"
    path.write_text(
        """
[platform]
max_size_kib = 300

[crop]
fill_ratio = 0.95

[encoding]
quality = "balanced"

[runtime]
workers = 2
""",
        encoding="utf-8",
    )

    config = load_config(path)

    assert config.platform.max_size_bytes == 300 * 1024
    assert config.platform.width == 512
    assert config.crop.fill_ratio == 0.95
    assert config.quality_profile.name == "balanced"
    assert config.runtime.workers == 2


def test_cli_overrides_leave_other_values_untouched() -> None:
    config = load_config(Path("does-not-exist.toml")) if False else load_config(None)
    updated = with_overrides(config, quality="fast", zoom="fit", threads=7)

    assert updated.encoding.quality == "fast"
    assert updated.crop.zoom == "fit"
    assert updated.runtime.threads == 7
    assert updated.platform == config.platform


def test_invalid_fill_ratio_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "invalid.toml"
    path.write_text("[crop]\nfill_ratio = 1.2\n", encoding="utf-8")

    with pytest.raises(ValueError, match="fill_ratio"):
        load_config(path)
