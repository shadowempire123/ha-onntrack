"""Version numbers that must agree, and the release notes that come from them.

Three files carry the version: manifest.json is what HACS reads, const.VERSION
goes into the User-Agent Nominatim sees, and CHANGELOG.md is what the release
workflow publishes. A mismatch between the first two ships a wrong User-Agent;
a missing changelog section fails the release after the tag is already pushed.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = json.loads((ROOT / "custom_components" / "onntrack" / "manifest.json").read_text())

sys.path.insert(0, str(ROOT / "scripts"))
from changelog_section import section  # noqa: E402


def test_manifest_and_const_agree():
    from onntrack.const import VERSION

    assert MANIFEST["version"] == VERSION


def test_hacs_manifest_is_valid_json_with_a_name():
    hacs = json.loads((ROOT / "hacs.json").read_text())
    assert hacs["name"]
    assert "homeassistant" in hacs


@pytest.mark.parametrize("key", ["documentation", "issue_tracker"])
def test_manifest_links_point_at_the_repository(key):
    assert MANIFEST[key].startswith("https://github.com/")


def test_manifest_has_what_hassfest_and_hacs_require():
    for key in ("domain", "name", "version", "documentation", "issue_tracker", "codeowners"):
        assert MANIFEST.get(key), f"manifest.json is missing {key}"


def test_the_current_version_has_release_notes():
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    notes = section(changelog, MANIFEST["version"])
    assert notes.strip(), "the release workflow would publish an empty release"


def test_an_unknown_version_fails_loudly():
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "changelog_section.py"), "99.0.0"],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
