"""Offline documentation integrity and cross-checks against AIR M0 constants."""
from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import unquote, urlsplit

import pytest

from protocol import air
from protocol.common import AirAckResult, AirCmdId, AirType

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
MIRRORS = (
    "COORDINATE_FRAMES", "SYSTEM_LIFECYCLE", "SYSTEM_CALIBRATION", "SYSTEM_ALIGNMENT",
    "SYSTEM_INERTIAL", "CALIBRATION_AND_ALIGNMENT", "TELEMETRY_INTERFACE", "SYSTEM_PROFILE",
)


def markdown_destinations(text: str) -> list[str]:
    # Code examples are not links. Include images and reference-style targets.
    text = re.sub(r"(?ms)^ *(`{3,}|~{3,})[^\n]*\n.*?^ *\1 *$", "", text)
    inline = re.findall(r"!?\[[^\]\n]*\]\(\s*(<[^>]+>|[^\s)]+)(?:\s+[^)]*)?\)", text)
    references = re.findall(r"(?m)^ {0,3}\[[^\]]+\]:\s*(<[^>]+>|\S+)", text)
    return [value.strip("<>") for value in inline + references]


@pytest.mark.parametrize("source", sorted(DOCS.rglob("*.md")) + [ROOT / name for name in (
    "README.md", "AGENTS.md", "TARGETS.md", "CHANGELOG.md", "VALIDATION.md", "tests/validation/README.md",
)], ids=lambda path: str(path.relative_to(ROOT)))
def test_markdown_relative_links_exist(source):
    missing = []
    for target in markdown_destinations(source.read_text(encoding="utf-8")):
        url = urlsplit(target)
        if url.scheme or url.netloc or not url.path:
            continue  # No network dependency; fragments remain within the file.
        path = (source.parent / unquote(url.path)).resolve()
        if not path.exists():
            missing.append(target)
    assert not missing, f"{source.relative_to(ROOT)}: missing {missing}"


def test_markdown_link_scan_covers_images_titles_references_and_ignores_code():
    text = '''[guide](guide.md#steps) ![figure](image.png "title")
[ref]: <a%20b.md>
```text
[example](not-a-real-link.md)
```
'''
    assert markdown_destinations(text) == ["guide.md#steps", "image.png", "a%20b.md"]


def test_platform_mirrors_are_complete_and_declare_fccg_authority():
    assert {p.stem for p in (DOCS / "platform").glob("*.md")} == {"README", *MIRRORS}
    for name in MIRRORS:
        heading = (DOCS / "platform" / f"{name}.md").read_text(encoding="utf-8").splitlines()[:5]
        assert any("只读" in line and "SilverStar_FCCG" in line for line in heading), name


def test_current_docs_do_not_reintroduce_obsolete_calibration_rules():
    patterns = (
        r"SilverStar\s+0\.0\.9", r"SILV0009", r"`?Existing`?\s*(?:calibration|校准|mode|Mode)",
        r"使用现有校准", r"NONE[^。\n]*(?:永不|永远不)",
        r"(?:calibration_mode_mask|CapabilityMaskGet|校准能力)[^。\n]*(?:固定|当前为)[^。\n]*0x07",
        r"BAD_PARAM[^。\n]*(?:build.{0,10}不支持|本工程不支持)",
    )
    for source in DOCS.rglob("*.md"):
        if "history" in source.parts:
            continue
        text = source.read_text(encoding="utf-8")
        for pattern in patterns:
            assert not re.search(pattern, text), f"{source.name}: {pattern}"


def test_calibration_contract_and_guide_define_explicit_default():
    contract = (DOCS / "AIR_CALIBRATION_CONTRACT.md").read_text(encoding="utf-8")
    guide = (DOCS / "CALIBRATION_USER_GUIDE.md").read_text(encoding="utf-8")
    for mask in ("0x03", "0x05", "0x07"):
        for text in (contract, guide):
            row = next(line for line in text.splitlines() if line.startswith("|") and mask in line)
            assert "默认校正" in row
    assert "CAL_START(NONE)" in guide
    assert "calibration_ready=1" in guide
    assert "自动" in next(line for line in guide.splitlines() if line.startswith("|") and "0x01" in line)
    assert "REJECTED" in contract and "BAD_PARAM" in contract


def test_air_document_frame_lengths_and_ack_values_match_code():
    text = (DOCS / "AIR_PROTOCOL.md").read_text(encoding="utf-8")
    for frame in AirType:
        length = getattr(air, f"AIR_{frame.name}_LEN")
        assert f"| `0x{frame.value:02X}` | `{frame.name}` | {length} |" in text
    for result in AirAckResult:
        assert f"| `0x{result.value:02X}` | `{result.name}` |" in text
    tokens = {
        "START_MISSION": air.TOKEN_START_MISSION, "LOCK": air.TOKEN_LOCK, "UNLOCK": air.TOKEN_UNLOCK,
        **{name: air.TOKEN_CALIBRATION for name in ("CAL_START", "CAL_FACE", "CAL_STOP", "CAL_RESET")},
        **{name: air.TOKEN_ALIGNMENT for name in ("ALIGN_START", "ALIGN_STOP", "ALIGN_RESET")},
    }
    for name, token in tokens.items():
        command = AirCmdId[name]
        assert f"| `0x{command.value:02X}` | `{name}` | `0x{token:08X}` |" in text


def test_progress_links_validation_without_duplicating_snapshot_numbers():
    text = (DOCS / "CURRENT_PROGRESS.md").read_text(encoding="utf-8")
    assert "(../VALIDATION.md)" in text
    assert not re.search(r"\b\d+\s+(?:passed|subtests)|\b[0-9a-f]{64}\b|(?:FLASH|RAM)\s*[:=]", text)
