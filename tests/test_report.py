"""Шаблон разбора согласован с критериями, SKILL.md ссылается на оба файла."""

import re
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent / "skills" / "interview-prep"
CRITERIA = SKILL / "references" / "criteria.md"
TEMPLATE = SKILL / "assets" / "report-template.md"


def criteria() -> list[str]:
    text = CRITERIA.read_text(encoding="utf-8")
    return re.findall(r"^## \d+\. (.+)$", text, flags=re.M)


def test_criteria_count():
    assert 5 <= len(criteria()) <= 6


def test_each_criterion_has_levels_and_example():
    text = CRITERIA.read_text(encoding="utf-8")
    blocks = re.split(r"^## \d+\. ", text, flags=re.M)[1:]
    assert len(blocks) == len(criteria())
    for block in blocks:
        name = block.splitlines()[0]
        for level in ("- Сильно:", "- Есть:", "- Нет:", "Пример замечания:"):
            assert level in block, f"{name}: нет «{level}»"


def test_template_has_every_criterion():
    sections = re.findall(r"^### (.+)$", TEMPLATE.read_text(encoding="utf-8"), flags=re.M)
    assert sections == criteria()


def test_template_sections():
    text = TEMPLATE.read_text(encoding="utf-8")
    for heading in ("## Заданные вопросы", "## Сильные места", "## Что подтянуть",
                    "## Повторить перед настоящим собеседованием"):
        assert heading in text
    assert "Вакансия:" in text and "тип интервьюера" in text
    assert "Цитата:" in text and "Упражнение:" in text


def test_skill_links_to_criteria_and_template():
    text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    links = re.findall(r"\]\(([^)#]+)\)", text)
    assert "references/criteria.md" in links
    assert "assets/report-template.md" in links
    for link in links:
        if not link.startswith(("http://", "https://")):
            assert (SKILL / link).exists(), link
