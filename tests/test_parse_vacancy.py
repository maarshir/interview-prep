"""Разбор вакансии. Все вакансии в тестах синтетические, сеть не трогаем."""

import io
import json
import sys
import urllib.error
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(__file__).resolve().parent / "data"
sys.path.insert(0, str(ROOT / "skills" / "interview-prep" / "scripts"))

import parse_vacancy as pv  # noqa: E402

HH_JSON = DATA / "vacancy_hh_synthetic.json"
TEXT = DATA / "vacancy_text_synthetic.txt"


def run(capsys, *args):
    code = pv.main([str(a) for a in args])
    out = capsys.readouterr()
    return code, out.out, out.err


def test_hh_json_fields():
    v = pv.parse_input(HH_JSON.read_text(encoding="utf-8"))
    assert v["title"] == "Стажёр Python-разработчик (ИИ-сервисы)"
    assert v["company"] == "ООО «Пример Тест»"
    assert v["city"] == "Москва"
    assert v["experience"] == "Нет опыта"
    assert v["salary"] == "от 60 000 до 90 000 ₽, на руки"
    assert v["key_skills"] == ["Python", "SQL", "Git"]


def test_hh_json_sections():
    v = pv.parse_input(HH_JSON.read_text(encoding="utf-8"))
    assert v["requirements"] == ["Python на уровне учебных проектов", "базовый SQL", "умение работать с Git."]
    assert v["nice_to_have"] == ["опыт с API языковых моделей."]
    assert v["responsibilities"][0] == "писать и тестировать обработчики на FastAPI"
    assert v["conditions"] == ["гибкий график, 30 часов в неделю", "наставника & разбор кода."]
    # «О компании» не попадает ни в один раздел
    assert not any("12 человек" in item for k in pv.SECTIONS for item in v[k] or [])


def test_text_labels_and_variants():
    v = pv.parse_input(TEXT.read_text(encoding="utf-8"))
    assert v["title"] == "Младший разработчик чат-ботов"
    assert v["city"] == "Казань"
    assert v["salary"] == "от 80 000 ₽"
    assert v["key_skills"] == ["Python", "aiogram", "PostgreSQL"]
    # «Чем предстоит заниматься», «Что мы ждём:», «Мы даём:» без слов «Обязанности» и т. п.
    assert v["responsibilities"] == ["разрабатывать ботов для Телеграма", "подключать оплату и базу данных"]
    assert v["requirements"] == ["Python и асинхронность", "опыт с aiogram"]
    assert "ДМС" in v["conditions"]


def test_sentence_starting_like_heading_is_text():
    # «У нас дружная команда» без двоеточия остаётся пунктом, а не заголовком
    v = pv.parse_input(TEXT.read_text(encoding="utf-8"))
    assert v["conditions"][0] == "У нас дружная команда"


@pytest.mark.parametrize("line,kind", [
    ("Требования:", "requirements"),
    ("**Требования к кандидату**", "requirements"),
    ("Что мы ожидаем от тебя:", "requirements"),
    ("Будет преимуществом:", "nice_to_have"),
    ("Ваши задачи", "responsibilities"),
    ("Условия работы:", "conditions"),
    ("О компании:", "other"),
    ("- требования к коду и тестам", None),
    ("Мы предлагаем работу над интересными задачами в команде из десяти человек.", None),
])
def test_classify_heading(line, kind):
    assert pv.classify_heading(line) == kind


def test_missing_sections_marked():
    md = pv.to_markdown(pv.parse_text("Курьер\nГород: Тверь\nРабота на свежем воздухе."))
    assert md.count(pv.NOT_FOUND) == 3
    assert "## Будет плюсом" not in md
    assert "- Компания: не указана" in md


@pytest.mark.parametrize("salary,expected", [
    (None, None),
    ({"from": None, "to": None, "currency": "RUR"}, None),
    ({"from": None, "to": 2000, "currency": "USD", "gross": True}, "до 2 000 $, до вычета налогов"),
    ({"from": 100000, "to": None, "currency": "KZT"}, "от 100 000 ₸"),
])
def test_salary(salary, expected):
    assert pv.format_salary(salary) == expected


def test_salary_range_field():
    data = {"name": "X", "salary_range": {"from": 50000, "to": 70000, "currency": "RUR", "gross": False}}
    assert pv.parse_hh_json(data)["salary"] == "от 50 000 до 70 000 ₽, на руки"


def test_html_text_input():
    v = pv.parse_input("Аналитик\n<p><b>Обязанности:</b></p><ul><li>отчёты</li></ul>")
    assert v["responsibilities"] == ["отчёты"]


def test_cli_markdown_and_json(capsys):
    code, out, _ = run(capsys, HH_JSON)
    assert code == 0 and out.startswith("# Стажёр Python-разработчик")
    code, out, _ = run(capsys, TEXT, "--json")
    assert code == 0 and json.loads(out)["city"] == "Казань"


def test_cli_errors(capsys, tmp_path):
    code, _, err = run(capsys, tmp_path / "nope.txt")
    assert code == 2 and "не удалось открыть" in err
    bad = tmp_path / "bad.json"
    bad.write_text('{"id": 1}', encoding="utf-8")
    code, _, err = run(capsys, bad)
    assert code == 2 and "name" in err


@pytest.mark.parametrize("ref,vid", [
    ("123456", "123456"),
    ("https://hh.ru/vacancy/123456", "123456"),
    ("https://spb.hh.ru/vacancy/123456?from=search", "123456"),
    ("https://hh.kz/vacancy/42", "42"),
])
def test_vacancy_id(ref, vid):
    assert pv.vacancy_id(ref) == vid


def test_vacancy_id_rejects_other_sites():
    with pytest.raises(pv.VacancyError):
        pv.vacancy_id("https://hh.ru.evil.example/vacancy/1")
    with pytest.raises(pv.VacancyError):
        pv.vacancy_id("https://example.com/vacancy/1")


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_fetch_uses_one_get_with_header(monkeypatch, capsys):
    calls = []

    def fake_urlopen(req, timeout):
        calls.append(req)
        return FakeResponse(HH_JSON.read_bytes())

    monkeypatch.setattr(pv.urllib.request, "urlopen", fake_urlopen)
    code, out, _ = run(capsys, "--fetch", "https://hh.ru/vacancy/100000001")
    assert code == 0 and "## Требования" in out
    assert len(calls) == 1
    req = calls[0]
    assert req.full_url == "https://api.hh.ru/vacancies/100000001"
    assert req.get_method() == "GET"
    assert req.get_header("Hh-user-agent").startswith("interview-prep/")
    assert "Authorization" not in req.headers


def test_fetch_http_error(monkeypatch, capsys):
    def fake_urlopen(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 403, "Forbidden", {}, None)

    monkeypatch.setattr(pv.urllib.request, "urlopen", fake_urlopen)
    code, _, err = run(capsys, "--fetch", "123")
    assert code == 2 and "403" in err and "файл" in err
