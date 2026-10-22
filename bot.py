"""Бот-уведомлялка о датах спектакля «Записки юного врача».

Заходит на страницу спектакля на сайте Белорусского государственного театра
кукол, ищет там даты показов и, если появилась новая дата, пишет в Telegram.
Уже виденные даты запоминает в файле state.json.
"""

import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

PAGE_URL = "https://puppet-minsk.by/spektakli/spektakli-dlya-vzroslykh/item/217-zapiski-yunogo-vracha"
TICKETPRO_URL = "https://www.ticketpro.by/bilety-v-teatr/kukolnyj/"
STATE_FILE = "state.json"
FAILS_BEFORE_ALERT = 12  # примерно час подряд ошибок при проверке раз в 5 минут

MONTHS = [
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
]
WEEKDAYS = "понедельник|вторник|среда|четверг|пятница|суббота|воскресенье"
# Ищем фрагменты вида «28 Октября, Среда, 19:00»
DATE_RE = re.compile(
    r"(\d{1,2})\s+(" + "|".join(MONTHS) + r")\s*,?\s*(" + WEEKDAYS + r")\s*,?\s*(\d{1,2}:\d{2})",
    re.IGNORECASE,
)


def fetch_page():
    """Скачивает страницу спектакля (3 попытки)."""
    last_error = None
    for _ in range(3):
        try:
            request = urllib.request.Request(
                PAGE_URL,
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                                  "Chrome/124.0 Safari/537.36",
                    "Accept-Language": "ru-RU,ru;q=0.9",
                },
            )
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.read().decode("utf-8", errors="replace")
        except Exception as error:  # noqa: BLE001
            last_error = error
            time.sleep(5)
    raise RuntimeError(last_error)


def parse_dates(raw_html):
    """Возвращает {ключ: красивая_строка} для всех дат на странице."""
    text = re.sub(r"(?is)<(script|style).*?</\1>", " ", raw_html)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text)

    found = {}
    for day, month, weekday, hhmm in DATE_RE.findall(text):
        month, weekday = month.lower(), weekday.lower()
        key = f"{int(day)} {month} {hhmm}"
        found[key] = f"{int(day)} {month}, {weekday}, {hhmm}"
    return found


def sort_keys(keys):
    def order(key):
        day, month, hhmm = key.split()
        return (MONTHS.index(month), int(day), hhmm)
    return sorted(keys, key=order)


def send_telegram(text):
    token = os.environ["TELEGRAM_TOKEN"].strip()
    chat_ids = [c.strip() for c in os.environ["TELEGRAM_CHAT_ID"].split(",") if c.strip()]
    for chat_id in chat_ids:
        data = urllib.parse.urlencode({"chat_id": chat_id, "text": text}).encode()
        request = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage", data=data
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                response.read()
        except urllib.error.HTTPError as error:
            print("Telegram ответил ошибкой:", error.code, error.read().decode("utf-8", "replace"))
            raise


def load_state():
    if not os.path.exists(STATE_FILE):
        return None
    with open(STATE_FILE, encoding="utf-8") as file:
        return json.load(file)


def save_state(state):
    state["seen"] = sort_keys(state["seen"])
    with open(STATE_FILE, "w", encoding="utf-8") as file:
        json.dump(state, file, ensure_ascii=False, indent=2)


def main():
    state = load_state()
    first_run = state is None
    if first_run:
        state = {"seen": [], "fail_count": 0, "warned_empty": False}

    try:
        raw_html = fetch_page()
    except Exception as error:  # noqa: BLE001
        print(f"Не удалось открыть сайт театра: {error}")
        if first_run:
            sys.exit(1)  # при первом запуске пусть будет видно красным крестиком
        state["fail_count"] += 1
        if state["fail_count"] == FAILS_BEFORE_ALERT:
            send_telegram("⚠️ Бот уже около часа не может открыть сайт театра. "
                          "Проверьте сайт вручную: " + PAGE_URL)
        save_state(state)
        return

    state["fail_count"] = 0
    dates = parse_dates(raw_html)
    seen = set(state["seen"])
    print(f"Дат на странице: {len(dates)}; уже известных: {len(seen)}")

    if first_run:
        if dates:
            lines = "\n".join("• " + dates[k] for k in sort_keys(dates))
            send_telegram("✅ Бот запущен и работает!\n\n"
                          "Сейчас на сайте у «Записок юного врача» такие даты:\n"
                          f"{lines}\n\nО новых датах напишу сразу, как только они появятся.\n"
                          f"Страница спектакля: {PAGE_URL}")
        else:
            send_telegram("✅ Бот запущен, но на странице спектакля я пока не вижу ни одной даты. "
                          "Если они там есть, а бот их не видит — дайте знать, поправим. "
                          "Если дат правда нет — просто подождём, напишу, когда появятся.")
        state["seen"] = list(dates)
        save_state(state)
        return

    if not dates:
        if seen and not state["warned_empty"]:
            send_telegram("⚠️ На странице спектакля бот не нашёл ни одной даты. "
                          "Возможно, все показы прошли, а возможно, сайт изменился. "
                          "Проверьте вручную: " + PAGE_URL)
            state["warned_empty"] = True
        save_state(state)
        return

    state["warned_empty"] = False
    new_keys = sort_keys(k for k in dates if k not in seen)
    if new_keys:
        title = "🎭 Новая дата" if len(new_keys) == 1 else "🎭 Новые даты"
        lines = "\n".join("• " + dates[k] for k in new_keys)
        send_telegram(f"{title} спектакля «Записки юного врача»!\n\n{lines}\n\n"
                      f"Купить билеты: {PAGE_URL}\n"
                      f"Партнёр: {TICKETPRO_URL}\n\n"
                      "Билеты разлетаются за минуты — поторопитесь!")
        state["seen"] = list(seen | set(new_keys))
    save_state(state)


if __name__ == "__main__":
    main()
