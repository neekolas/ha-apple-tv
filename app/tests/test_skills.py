"""Keep the agent skills installable on their own and the TV apps on one status kit."""

import re
from pathlib import Path

import pytest

SKILLS = Path(__file__).resolve().parents[2] / "skills"
NAMES = sorted(p.name for p in SKILLS.iterdir() if (p / "SKILL.md").is_file())
KIT = re.compile(r"<!-- tv-kit:start.*?<!-- tv-kit:end -->", re.S)


def frontmatter(path):
    text = path.read_text()
    assert text.startswith("---\n"), path
    block = text.split("---\n", 2)[1]
    return dict(line.split(": ", 1) for line in block.strip().splitlines())


def test_four_skills():
    assert NAMES == ["tv-use-connect", "tv-use-realtime", "tv-use-setup", "tv-use-web-apps"]


@pytest.mark.parametrize("name", NAMES)
def test_frontmatter_matches_agent_skills_rules(name):
    meta = frontmatter(SKILLS / name / "SKILL.md")
    assert meta["name"] == name
    assert re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", name) and len(name) <= 64
    assert 0 < len(meta["description"]) <= 1024


@pytest.mark.parametrize("name", NAMES)
def test_relative_links_stay_inside_the_skill(name):
    root = SKILLS / name
    for doc in root.rglob("*.md"):
        for target in re.findall(r"\]\(([^)#\s]+)(?:#[^)]*)?\)", doc.read_text()):
            if re.match(r"[a-z]+:", target):
                continue
            resolved = (doc.parent / target).resolve()
            assert resolved.is_relative_to(root.resolve()), f"{doc}: {target} leaves the skill"
            assert resolved.exists(), f"{doc}: {target} is missing"


def test_every_tv_app_carries_the_same_kit():
    assets = SKILLS / "tv-use-web-apps" / "assets"
    kit = KIT.search((assets / "tv-kit.html").read_text()).group(0)
    pages = [assets / "pending-page.html", *sorted((assets / "apps").glob("*.html"))]
    assert len(pages) >= 2
    for page in pages:
        found = KIT.search(page.read_text())
        assert found and found.group(0) == kit, f"{page.name} has a different tv kit"


def test_tv_apps_load_nothing_from_the_network():
    for page in (SKILLS / "tv-use-web-apps" / "assets").rglob("*.html"):
        text = page.read_text()
        assert not re.search(r"""(src|href)\s*=\s*["']?(https?:)?//""", text), page.name
        assert "@import" not in text and "url(http" not in text, page.name
