"""The repo's metadata says the same thing in all the places it is written.

The Home Assistant floor in particular is written twice — once in `hacs.json`,
where it stops an install on too old a core, and once in the README, for
users. A number that moves in one file and not the other is worse than no
number at all, so they are checked against each other here rather than by
whoever notices. The *reason* the floor is what it is lives with the ticket
that set it, not in the README — that's implementation history, not something
a user installing the integration needs.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from custom_components.eversolo.const import DOMAIN

REPO = Path(__file__).parents[1]
COMPONENT_DIR = REPO / "custom_components" / DOMAIN
MANIFEST = COMPONENT_DIR / "manifest.json"
HACS = REPO / "hacs.json"
README = REPO / "README.md"
CHANGELOG = REPO / "CHANGELOG.md"

# The oldest core release this integration is allowed to claim without someone
# re-deriving the floor. Raising it is a deliberate act: the README has to say
# which newer API forced it.
FLOOR_IN_README = re.compile(r"\*\*Home Assistant (\d{4}\.\d+\.\d+) or newer\.\*\*")


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def test_the_home_assistant_floor_is_the_same_number_in_both_files() -> None:
    """`hacs.json` enforces the floor; the README explains it."""
    stated = FLOOR_IN_README.search(README.read_text(encoding="utf-8"))

    assert stated is not None, "the README no longer states a Home Assistant floor"
    assert stated.group(1) == _load(HACS)["homeassistant"]


def test_the_manifest_describes_this_integration() -> None:
    """Domain, ownership and the two URLs a bug report needs."""
    manifest = _load(MANIFEST)

    assert manifest["domain"] == DOMAIN == COMPONENT_DIR.name
    assert manifest["config_flow"] is True
    assert manifest["iot_class"] == "local_polling"
    assert manifest["codeowners"] == ["@tmeynell"]
    assert manifest["documentation"].startswith("https://github.com/tmeynell/")
    assert manifest["issue_tracker"].startswith("https://github.com/tmeynell/")


def test_the_readme_lists_every_entity_the_integration_builds() -> None:
    """An entity was added and the README's table did not notice.

    Named off `strings.json` rather than off the platform modules, because that
    file is already checked against them entity by entity in
    `test_translations`, and it is the wording the README has to match.
    """
    entity = _load(COMPONENT_DIR / "strings.json")["entity"]
    readme = README.read_text(encoding="utf-8")

    def _has_a_row_for(name: str) -> bool:
        """Match a whole table cell, not a substring of a longer one.

        Unanchored, "Screen" is satisfied by the "Screen brightness" row and
        the Screen switch could be dropped from the table unnoticed.
        """
        return re.search(rf"\|\s*{re.escape(name)}\s*\|", readme) is not None

    missing = [
        f"{platform}.{key}"
        for platform, keys in entity.items()
        for key, strings in keys.items()
        if not _has_a_row_for(strings["name"])
    ]

    assert not missing


def test_the_changelog_has_an_entry_for_the_current_version() -> None:
    """A version bump with no changelog entry is a version bump nobody can read back.

    Keep a Changelog (CONTRIBUTING.md) asks for `[Unreleased]` to become a dated
    `## [X.Y.Z]` section as part of the same PR that bumps the manifest version — this
    is what actually enforces that instead of trusting it gets remembered.
    """
    version = _load(MANIFEST)["version"]
    changelog = CHANGELOG.read_text(encoding="utf-8")
    pattern = rf"^## \[{re.escape(version)}\] - \d{{4}}-\d{{2}}-\d{{2}}"

    assert re.search(pattern, changelog, re.MULTILINE), (
        f"CHANGELOG.md has no dated '## [{version}]' section for the version in manifest.json"
    )
