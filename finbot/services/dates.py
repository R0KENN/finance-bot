"""Даты и периоды. Ключи периодов лежат в callback_data, поэтому короткие:
d — сегодня, w — неделя, m — этот месяц, y — год, a — всё время, YYYY-MM — месяц."""
import calendar
import re
from datetime import date, datetime, timedelta
from datetime import tzinfo

MONTHS_NOM = ["Январь", "Февраль", "Март", "Апрель", "Май", "Июнь", "Июль",
              "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"]
MONTHS_GEN = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля",
              "августа", "сентября", "октября", "ноября", "декабря"]
MONTHS_SHORT = ["янв", "фев", "мар", "апр", "мая", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"]
WEEKDAYS = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
WEEKDAYS_SHORT = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]

ALL_TIME_START = date(2000, 1, 1)
_MONTH_KEY = re.compile(r"^(\d{4})-(\d{2})$")


def today_in(tz: tzinfo) -> date:
    return datetime.now(tz).date()


def iso(d: date) -> str:
    return d.isoformat()


def month_key(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def month_bounds(year: int, month: int) -> tuple[date, date]:
    return date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])


def add_months(d: date, delta: int) -> date:
    """Сдвиг на месяцы с обрезкой дня (31 янв + 1 мес = 28/29 фев)."""
    index = d.year * 12 + (d.month - 1) + delta
    year, month = divmod(index, 12)
    month += 1
    return date(year, month, min(d.day, calendar.monthrange(year, month)[1]))


def shift_month_key(key: str, delta: int, today: date) -> str:
    """Соседний месяц для ключа 'm' или 'YYYY-MM'."""
    match = _MONTH_KEY.match(key)
    base = date(int(match.group(1)), int(match.group(2)), 1) if match else today.replace(day=1)
    return month_key(add_months(base, delta))


def is_month_period(key: str) -> bool:
    return key == "m" or bool(_MONTH_KEY.match(key))


def resolve(key: str, today: date) -> tuple[date, date, str]:
    """Ключ периода -> (с, по, подпись)."""
    if key == "d":
        return today, today, f"Сегодня, {today.day} {MONTHS_GEN[today.month - 1]}"
    if key == "w":
        start = today - timedelta(days=today.weekday())
        end = start + timedelta(days=6)
        return start, end, f"Неделя {fmt_short(start)} – {fmt_short(end)}"
    if key == "y":
        return date(today.year, 1, 1), date(today.year, 12, 31), f"{today.year} год"
    if key == "a":
        return ALL_TIME_START, today, "Всё время"
    match = _MONTH_KEY.match(key)
    if match:
        year, month = int(match.group(1)), int(match.group(2))
        if 1 <= month <= 12:
            start, end = month_bounds(year, month)
            return start, end, f"{MONTHS_NOM[month - 1]} {year}"
    start, end = month_bounds(today.year, today.month)
    return start, end, f"{MONTHS_NOM[today.month - 1]} {today.year}"


def fmt_short(d: date) -> str:
    return f"{d.day} {MONTHS_SHORT[d.month - 1]}"


def fmt_day(d: date, today: date) -> str:
    """Сегодня / Вчера / 15 сен (с годом, если не текущий)."""
    if d == today:
        return "Сегодня"
    if d == today - timedelta(days=1):
        return "Вчера"
    text = fmt_short(d)
    return text if d.year == today.year else f"{text} {d.year}"


def parse_user_date(text: str, today: date, allow_future: bool = False) -> date | None:
    """'вчера', '15.09', '15.09.26', '15.09.2026', '2026-09-15'. Будущее по умолчанию запрещено."""
    raw = (text or "").strip().lower()
    words = {"сегодня": 0, "вчера": 1, "позавчера": 2}
    if raw in words:
        return today - timedelta(days=words[raw])
    found = None
    match = re.fullmatch(r"(\d{1,2})[./-](\d{1,2})(?:[./-](\d{2}|\d{4}))?", raw)
    if match:
        day, month = int(match.group(1)), int(match.group(2))
        year_raw = match.group(3)
        year = today.year if not year_raw else int(year_raw) + (2000 if len(year_raw) == 2 else 0)
        found = _safe_date(year, month, day)
        # '30.12' в январе — это прошлый год, а не будущее (но для сроков наоборот: ближайшая дата)
        if found and not year_raw and found > today and not allow_future:
            found = _safe_date(year - 1, month, day)
    else:
        match = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", raw)
        if match:
            found = _safe_date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    if found is None or (found > today and not allow_future) or found.year < 2000:
        return None
    return found


def _safe_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def next_occurrence(freq: str, param: int, after: date) -> date:
    """Следующая дата регулярной операции строго после `after`."""
    if freq == "daily":
        return after + timedelta(days=1)
    if freq == "weekly":
        ahead = (param - after.weekday()) % 7
        return after + timedelta(days=ahead or 7)
    # monthly: день месяца, 31-е в коротком месяце уходит на последний день
    this_month = _clamp_day(after.year, after.month, param)
    if this_month > after:
        return this_month
    nxt = add_months(after.replace(day=1), 1)
    return _clamp_day(nxt.year, nxt.month, param)


def first_occurrence(freq: str, param: int, today: date) -> date:
    """Первая дата, включая сегодняшнюю."""
    return next_occurrence(freq, param, today - timedelta(days=1))


def _clamp_day(year: int, month: int, day: int) -> date:
    return date(year, month, min(day, calendar.monthrange(year, month)[1]))
