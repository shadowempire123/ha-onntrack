#!/usr/bin/env python3
"""Print the CHANGELOG section for one version.

Used by the release workflow to turn a tag into release notes, so the notes
cannot drift from the changelog: there is only one place where they are
written.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

HEADING = re.compile(r"^##\s+v?(?P<version>\S+)")


def section(changelog: str, version: str) -> str:
    wanted = version.lstrip("v")
    lines = changelog.splitlines()
    collected: list[str] = []
    inside = False

    for line in lines:
        match = HEADING.match(line)
        if match:
            if inside:
                break
            inside = match.group("version").lstrip("v") == wanted
            continue
        if inside:
            collected.append(line)

    if not inside and not collected:
        raise SystemExit(f"No changelog section found for version {wanted}")
    return "\n".join(collected).strip("\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version", help="version to extract, with or without a leading v")
    parser.add_argument(
        "--changelog",
        type=Path,
        default=Path(__file__).resolve().parent.parent / "CHANGELOG.md",
    )
    args = parser.parse_args()
    sys.stdout.write(section(args.changelog.read_text(encoding="utf-8"), args.version) + "\n")


if __name__ == "__main__":
    main()
