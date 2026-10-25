"""Навык соответствует спецификации agentskills.io/specification.

Проверяем то, из-за чего навык не загрузится или загрузится не тогда:
поля и их длину, имя папки, размер SKILL.md и ссылки на файлы внутри навыка.
"""

import json
import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
SKILLS = sorted(p.parent for p in (ROOT / "skills").glob("*/SKILL.md"))
ALLOWED = {"name", "description", "license", "compatibility", "metadata", "allowed-tools"}
NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


def split(skill: Path) -> tuple[dict, str]:
    text = (skill / "SKILL.md").read_text(encoding="utf-8")
    assert text.startswith("---\n"), "SKILL.md должен начинаться с YAML между ---"
    _, head, body = text.split("---\n", 2)
    meta = yaml.safe_load(head)
    assert isinstance(meta, dict), "заголовок SKILL.md не читается как словарь"
    return meta, body


def test_there_are_skills():
    assert SKILLS


@pytest.mark.parametrize("skill", SKILLS, ids=lambda p: p.name)
def test_frontmatter(skill):
    meta, _ = split(skill)
    assert set(meta) <= ALLOWED, f"лишние поля: {set(meta) - ALLOWED}"
    name = meta["name"]
    assert 1 <= len(name) <= 64 and NAME_RE.match(name), name
    assert name == skill.name, "имя должно совпадать с папкой"
    desc = meta["description"]
    assert isinstance(desc, str) and 1 <= len(desc) <= 1024
    if "compatibility" in meta:
        assert 1 <= len(meta["compatibility"]) <= 500
    if "metadata" in meta:
        assert all(isinstance(k, str) and isinstance(v, str) for k, v in meta["metadata"].items())


@pytest.mark.parametrize("skill", SKILLS, ids=lambda p: p.name)
def test_body_is_short(skill):
    _, body = split(skill)
    assert len(body.splitlines()) < 500
    assert len(body) < 20_000  # около 5000 токенов, как советует спецификация


@pytest.mark.parametrize("skill", SKILLS, ids=lambda p: p.name)
def test_referenced_files_exist(skill):
    for md in skill.rglob("*.md"):
        text = md.read_text(encoding="utf-8")
        links = re.findall(r"\]\(([^)#]+)\)", text)
        paths = re.findall(r"`((?:scripts|assets|references)/[^`\s]+)`", text)
        for link in links:
            if link.startswith(("http://", "https://", "mailto:")):
                continue
            assert (md.parent / link).exists(), f"{md.name}: нет файла {link}"
        for path in paths:
            assert (skill / path).exists(), f"{md.name}: нет файла {path}"


def test_questions_have_replacements():
    """У каждого банального вопроса в таблице есть замена."""
    text = (ROOT / "skills" / "interview-prep" / "references" / "questions.md").read_text(encoding="utf-8")
    rows = [r for r in text.splitlines() if r.startswith("| ") and not r.startswith("| Не спрашивать")]
    assert len(rows) >= 8
    for row in rows:
        cells = [c.strip() for c in row.strip("|").split("|")]
        assert len(cells) == 2 and all(cells), row


def test_marketplace_lists_every_skill():
    data = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
    listed = {Path(s).name for plugin in data["plugins"] for s in plugin["skills"]}
    assert listed == {s.name for s in SKILLS}
