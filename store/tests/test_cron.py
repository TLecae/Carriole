from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from etl_store.cron import Cron, CronError

PARIS = ZoneInfo("Europe/Paris")


def at(text: str) -> float:
    return datetime.fromisoformat(text).replace(tzinfo=PARIS).timestamp()


def show(instant: float) -> str:
    return datetime.fromtimestamp(instant, PARIS).strftime("%Y-%m-%d %H:%M")


def nxt(expression: str, after: str) -> str:
    return show(Cron.parse(expression).next_after(at(after), PARIS))


def test_simple_fields_and_strictly_after():
    assert nxt("30 6 * * *", "2026-03-10 06:00") == "2026-03-10 06:30"
    assert nxt("30 6 * * *", "2026-03-10 06:30") == "2026-03-11 06:30"  # strictement postérieur
    assert nxt("*/15 * * * *", "2026-03-10 10:07") == "2026-03-10 10:15"
    assert nxt("0 0 1 * *", "2026-03-10 12:00") == "2026-04-01 00:00"
    assert nxt("0 9 25 12 *", "2026-03-10 12:00") == "2026-12-25 09:00"


def test_lists_ranges_steps_and_names():
    assert nxt("0 8-18/5 * * *", "2026-03-10 09:00") == "2026-03-10 13:00"  # 8, 13, 18
    assert nxt("0 8,12,20 * * *", "2026-03-10 12:30") == "2026-03-10 20:00"
    assert nxt("0 6 * * mon-fri", "2026-03-13 07:00") == "2026-03-16 06:00"  # vendredi -> lundi
    assert nxt("0 0 1 JAN,JUL *", "2026-03-10 00:00") == "2026-07-01 00:00"
    assert nxt("5/20 * * * *", "2026-03-10 10:06") == "2026-03-10 10:25"  # 5, 25, 45


def test_sunday_is_0_or_7_and_day_rules_are_or_when_both_restricted():
    assert nxt("0 0 * * 0", "2026-03-10 00:00") == nxt("0 0 * * 7", "2026-03-10 00:00") == "2026-03-15 00:00"
    # le 13 du mois OU un vendredi : le vendredi 13 mars 2026 compte, mais aussi tout autre vendredi
    assert nxt("0 0 13 * 5", "2026-03-10 00:00") == "2026-03-13 00:00"
    assert nxt("0 0 13 * 5", "2026-03-14 00:00") == "2026-03-20 00:00"


def test_macros():
    assert nxt("@daily", "2026-03-10 05:00") == "2026-03-11 00:00"
    assert nxt("@hourly", "2026-03-10 05:10") == "2026-03-10 06:00"
    assert nxt("@weekly", "2026-03-10 05:10") == "2026-03-15 00:00"
    assert nxt("@monthly", "2026-03-10 05:10") == "2026-04-01 00:00"
    assert nxt("@yearly", "2026-03-10 05:10") == "2027-01-01 00:00"


def test_daylight_saving_time():
    # 29 mars 2026 : à 2 h il est 3 h (heure inexistante : 2 h 30 est sautée)
    assert nxt("30 2 * * *", "2026-03-28 03:00") == "2026-03-30 02:30"
    # 25 octobre 2026 : 2 h 30 existe deux fois, on ne la prend qu'une fois
    first = Cron.parse("30 2 * * *").next_after(at("2026-10-24 12:00"), PARIS)
    second = Cron.parse("30 2 * * *").next_after(first, PARIS)
    assert show(first) == "2026-10-25 02:30" and show(second) == "2026-10-26 02:30"


def test_upcoming_lists_following_occurrences():
    start = at("2026-03-10 00:00")
    assert [show(t) for t in Cron.parse("0 12 * * *").upcoming(start, 3)] == [
        "2026-03-10 12:00",
        "2026-03-11 12:00",
        "2026-03-12 12:00",
    ]


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "* * * *",
        "* * * * * *",
        "60 * * * *",
        "* 24 * * *",
        "* * 0 * *",
        "* * * 13 *",
        "* * * * 8",
        "a * * * *",
        "*/0 * * * *",
        "5-1 * * * *",
    ],
)
def test_invalid_expressions(bad):
    with pytest.raises(CronError):
        Cron.parse(bad)


def test_never_satisfied_expression_is_reported():
    with pytest.raises(CronError, match="aucune échéance"):
        Cron.parse("0 0 31 2 *").next_after(at("2026-03-10 00:00"), PARIS)  # 31 février


def test_timezone_comes_from_environment(monkeypatch):
    monkeypatch.setenv("ETL_TIMEZONE", "UTC")
    utc_instant = Cron.parse("0 12 * * *").next_after(datetime(2026, 3, 10, 0, 0, tzinfo=ZoneInfo("UTC")).timestamp())
    assert datetime.fromtimestamp(utc_instant, ZoneInfo("UTC")).hour == 12
