#!/usr/bin/env python3
"""Разбирает вакансию на части: требования, обязанности, условия и остальное.

Вход: файл с текстом вакансии, JSON-ответ hh.ru (/vacancies/{id}) или,
с флагом --fetch, ссылка на вакансию hh.ru. Выход: Markdown или --json.

Примеры:
    python parse_vacancy.py vacancy.txt
    python parse_vacancy.py vacancy.json --json
    python parse_vacancy.py --fetch https://hh.ru/vacancy/123456

Только стандартная библиотека Python 3.10+.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
import urllib.error
import urllib.request
from html.parser import HTMLParser

API_URL = "https://api.hh.ru/vacancies/{id}"
USER_AGENT = "interview-prep/0.1 (+https://github.com/maarshir/interview-prep)"
NOT_FOUND = "Раздел не найден в тексте вакансии."

# Заголовки разделов и их частые варианты. Сравниваем с началом строки
# после приведения к нижнему регистру и замены «ё» на «е».
SECTIONS = {
    "requirements": (
        "требования", "мы ждем", "ждем от вас", "ждем от тебя", "что мы ждем",
        "мы ожидаем", "что мы ожидаем", "наши ожидания", "ожидания", "от вас",
        "от тебя", "кого мы ищем", "кого ищем", "необходимые навыки",
        "что нужно знать", "что нужно уметь", "что важно для нас",
        "тебе подойдет", "вам подойдет", "ты нам подходишь", "вы нам подходите",
    ),
    "nice_to_have": (
        "будет плюсом", "будет преимуществом", "преимуществом будет",
        "плюсом будет", "желательно", "дополнительно", "приветствуется",
    ),
    "responsibilities": (
        "обязанности", "задачи", "ваши задачи", "твои задачи", "чем предстоит заниматься",
        "чем заниматься", "что предстоит делать", "что нужно делать", "что делать",
        "чем ты будешь заниматься", "чем вы будете заниматься", "функционал",
        "предстоит", "основные задачи",
    ),
    "conditions": (
        "условия", "мы предлагаем", "что мы предлагаем", "предлагаем",
        "что мы даем", "мы даем", "что получишь", "что вы получите", "бонусы",
        "плюшки", "у нас", "почему у нас", "что у нас есть",
    ),
}
# Без двоеточия на конце строку с продолжением («Требования к кандидату»)
# считаем заголовком только для этих слов: «У нас дружная команда» это текст.
UNAMBIGUOUS = {"требования", "обязанности", "условия", "будет плюсом", "мы предлагаем", "мы ожидаем"}
SECTION_TITLES = {
    "requirements": "Требования",
    "nice_to_have": "Будет плюсом",
    "responsibilities": "Обязанности",
    "conditions": "Условия",
}
# «Будет плюсом» в вакансиях часто нет, поэтому отсутствие этого раздела не помечаем
REQUIRED_SECTIONS = ("requirements", "responsibilities", "conditions")

LABELS = {
    "company": ("компания", "работодатель"),
    "city": ("город", "локация", "место работы"),
    "experience": ("опыт", "опыт работы", "требуемый опыт"),
    "salary": ("зарплата", "заработная плата", "доход", "оплата"),
}
SKILL_HEADINGS = ("ключевые навыки", "навыки", "стек", "стек технологий", "технологии")

CURRENCY = {"RUR": "₽", "RUB": "₽", "USD": "$", "EUR": "€", "KZT": "₸", "BYR": "Br", "BYN": "Br", "UZS": "сум"}


class VacancyError(Exception):
    """Вакансию не удалось прочитать или скачать."""


# --- HTML в текст ---

class _HtmlToText(HTMLParser):
    BLOCK = {"p", "div", "br", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "section"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "li":
            self.parts.append("\n- ")
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.BLOCK or tag == "li":
            self.parts.append("\n")

    def handle_data(self, data):
        self.parts.append(data)


def html_to_text(markup: str) -> str:
    parser = _HtmlToText()
    parser.feed(markup)
    parser.close()
    lines = (re.sub(r"[ \t ]+", " ", line).strip() for line in "".join(parser.parts).splitlines())
    return "\n".join(line for line in lines if line and line != "-")


# --- Разделы ---

def _norm(line: str) -> str:
    line = line.lower().replace("ё", "е").replace("*", "").replace("#", "")
    return re.sub(r"\s+", " ", line).strip(" :.-–—!?")


def _is_bullet(line: str) -> bool:
    return bool(re.match(r"^\s*([-•·*]\s|\d+[.)]\s)", line))


def classify_heading(line: str) -> str | None:
    """Вид раздела для строки-заголовка, «other» для чужого заголовка, None для текста."""
    stripped = line.strip().strip("*").strip()
    if not stripped or _is_bullet(line) or len(stripped) > 80:
        return None
    norm = _norm(stripped)
    has_colon = stripped.endswith(":")
    short = len(stripped) <= 40 and not stripped.endswith((".", ",", ";"))
    if not (has_colon or short):
        return None
    for kind, keys in SECTIONS.items():
        for key in keys:
            if norm == key or (norm.startswith(key + " ") and (has_colon or key in UNAMBIGUOUS)):
                return kind
    if has_colon:
        return "other"
    return None


def _clean_item(line: str) -> str:
    return re.sub(r"^\s*([-•·*]|\d+[.)])\s+", "", line).strip().rstrip(";,").strip()


def split_sections(text: str) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    current: str | None = None
    for raw in text.splitlines():
        if not raw.strip():
            continue
        kind = classify_heading(raw)
        if kind:
            current = kind if kind in SECTIONS else None
            if current:
                found.setdefault(current, [])
            continue
        if current:
            item = _clean_item(raw)
            if item:
                found[current].append(item)
    return found


# --- Поля ---

def format_salary(salary: dict | None) -> str | None:
    if not salary or (salary.get("from") is None and salary.get("to") is None):
        return None
    cur = CURRENCY.get(str(salary.get("currency") or ""), salary.get("currency") or "")
    num = lambda v: f"{int(v):,}".replace(",", " ")  # noqa: E731
    parts = []
    if salary.get("from") is not None:
        parts.append(f"от {num(salary['from'])}")
    if salary.get("to") is not None:
        parts.append(f"до {num(salary['to'])}")
    text = " ".join(parts) + (f" {cur}" if cur else "")
    if salary.get("gross") is True:
        text += ", до вычета налогов"
    elif salary.get("gross") is False:
        text += ", на руки"
    return text


def _empty() -> dict:
    return {
        "title": None, "company": None, "city": None, "experience": None, "salary": None,
        "key_skills": [], "requirements": None, "nice_to_have": None,
        "responsibilities": None, "conditions": None,
    }


def _fill_sections(result: dict, text: str) -> None:
    for kind, items in split_sections(text).items():
        if items:
            result[kind] = items


def parse_hh_json(data: dict) -> dict:
    if not isinstance(data, dict) or "name" not in data:
        raise VacancyError("JSON не похож на вакансию hh.ru: нет поля name")
    result = _empty()
    result["title"] = data.get("name")
    result["company"] = (data.get("employer") or {}).get("name")
    result["city"] = (data.get("area") or {}).get("name")
    result["experience"] = (data.get("experience") or {}).get("name")
    result["salary"] = format_salary(data.get("salary") or data.get("salary_range"))
    result["key_skills"] = [s["name"] for s in data.get("key_skills") or [] if s.get("name")]
    _fill_sections(result, html_to_text(data.get("description") or ""))
    return result


def parse_text(text: str) -> dict:
    if "<" in text and re.search(r"</?(p|li|ul|br|strong|div)\b", text, re.I):
        text = html_to_text(text)
    else:
        text = html.unescape(text)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        raise VacancyError("пустой текст вакансии")
    result = _empty()
    result["title"] = lines[0].strip("#* ").strip()
    for line in lines[1:15]:
        match = re.match(r"^([^:]{2,30}):\s*(.+)$", line)
        if not match:
            continue
        label, value = _norm(match.group(1)), match.group(2).strip()
        for field, keys in LABELS.items():
            if label in keys and result[field] is None:
                result[field] = value
        if label in SKILL_HEADINGS:
            result["key_skills"] = [s.strip() for s in re.split(r"[,;]", value) if s.strip()]
    _fill_sections(result, "\n".join(lines[1:]))
    return result


def parse_input(raw: str) -> dict:
    stripped = raw.lstrip("﻿").strip()
    if stripped.startswith("{"):
        try:
            data = json.loads(stripped)
        except json.JSONDecodeError as err:
            raise VacancyError(f"файл похож на JSON, но не читается: {err}") from err
        return parse_hh_json(data)
    return parse_text(stripped)


# --- Скачивание ---

def vacancy_id(ref: str) -> str:
    """Номер вакансии из ссылки hh.ru (любой региональный поддомен) или из числа."""
    ref = ref.strip()
    if ref.isdigit():
        return ref
    match = re.match(r"^https?://([a-z0-9-]+\.)*hh\.(ru|kz|uz)/vacancy/(\d+)", ref, re.I)
    if not match:
        raise VacancyError(f"не похоже на ссылку на вакансию hh.ru: {ref}")
    return match.group(3)


def fetch(ref: str, timeout: float = 15) -> dict:
    """Один GET к открытому API hh.ru, без ключей."""
    url = API_URL.format(id=vacancy_id(ref))
    agent = os.environ.get("HH_USER_AGENT", USER_AGENT)
    req = urllib.request.Request(url, headers={"HH-User-Agent": agent, "User-Agent": agent})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as err:
        hint = {404: "вакансия не найдена или в архиве", 403: "hh.ru не пустил запрос без ключа",
                429: "слишком много запросов, попробуйте позже"}.get(err.code, "ошибка на стороне hh.ru")
        raise VacancyError(f"hh.ru ответил {err.code}: {hint}. Сохраните текст вакансии в файл и передайте его.") from err
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as err:
        raise VacancyError(f"не удалось получить вакансию с hh.ru: {err}. Сохраните текст вакансии в файл.") from err


# --- Вывод ---

def to_markdown(v: dict) -> str:
    out = [f"# {v['title'] or 'Вакансия без названия'}", ""]
    for label, key, empty in (
        ("Компания", "company", "не указана"), ("Город", "city", "не указан"),
        ("Опыт", "experience", "не указан"), ("Зарплата", "salary", "не указана"),
    ):
        out.append(f"- {label}: {v[key] or empty}")
    out += ["", "## Ключевые навыки", ""]
    out.append(", ".join(v["key_skills"]) if v["key_skills"] else "Не указаны.")
    for kind in ("requirements", "nice_to_have", "responsibilities", "conditions"):
        items = v[kind]
        if not items and kind not in REQUIRED_SECTIONS:
            continue
        out += ["", f"## {SECTION_TITLES[kind]}", ""]
        out += [f"- {item}" for item in items] if items else [f"_{NOT_FOUND}_"]
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Разбор вакансии: требования, обязанности, условия.")
    ap.add_argument("source", help="файл с текстом или JSON вакансии, «-» для stdin, с --fetch ссылка hh.ru")
    ap.add_argument("--fetch", action="store_true", help="скачать вакансию через открытый API hh.ru")
    ap.add_argument("--json", action="store_true", help="вывести JSON вместо Markdown")
    args = ap.parse_args(argv)
    try:
        if args.fetch:
            vacancy = parse_hh_json(fetch(args.source))
        else:
            if args.source == "-":
                raw = sys.stdin.read()
            else:
                try:
                    with open(args.source, encoding="utf-8") as fh:
                        raw = fh.read()
                except OSError as err:
                    raise VacancyError(f"не удалось открыть файл: {err}") from err
            vacancy = parse_input(raw)
    except VacancyError as err:
        print(f"Ошибка: {err}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(vacancy, ensure_ascii=False, indent=2))
    else:
        sys.stdout.write(to_markdown(vacancy))
    return 0


if __name__ == "__main__":
    sys.exit(main())
