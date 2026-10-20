"""Expressions cron à cinq champs : minute heure jour-du-mois mois jour-de-semaine.

Supporte `*`, `a`, `a-b`, `*/n`, `a-b/n`, les listes `a,b,c`, les noms de mois (JAN…) et de jours (MON…), et les
raccourcis @hourly @daily @weekly @monthly @yearly. Comme le cron classique, quand le jour du mois **et** le jour de
la semaine sont restreints, il suffit que l'un des deux corresponde. Les heures sont celles du fuseau
`ETL_TIMEZONE` (Europe/Paris par défaut) ; une heure inexistante (passage à l'heure d'été) est sautée.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

MACROS = {
    "@hourly": "0 * * * *",
    "@daily": "0 0 * * *",
    "@midnight": "0 0 * * *",
    "@weekly": "0 0 * * 0",
    "@monthly": "0 0 1 * *",
    "@yearly": "0 0 1 1 *",
    "@annually": "0 0 1 1 *",
}
_MONTH_NAMES = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]
MONTHS = {n: i + 1 for i, n in enumerate(_MONTH_NAMES)}
DAYS = {n: i for i, n in enumerate(["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"])}


class CronError(ValueError):
    pass


def _field(text: str, low: int, high: int, names: dict[str, int] | None = None) -> set[int]:
    values: set[int] = set()
    for part in text.split(","):
        step = 1
        if "/" in part:
            part, step_text = part.split("/", 1)
            if not step_text.isdigit() or int(step_text) < 1:
                raise CronError(f"pas invalide : « /{step_text} »")
            step = int(step_text)

        def number(token: str) -> int:
            token = token.upper()
            if names and token in names:
                return names[token]
            if not token.isdigit():
                raise CronError(f"valeur invalide : « {token} »")
            return int(token)

        if part == "*":
            start, end = low, high
        elif "-" in part:
            a, b = part.split("-", 1)
            start, end = number(a), number(b)
        else:
            start = number(part)
            end = high if step > 1 else start  # « 5/15 » = de 5 jusqu'à la fin, tous les 15
        if not (low <= start <= high and low <= end <= high) or start > end:
            raise CronError(f"hors limites ({low}-{high}) : « {part} »")
        values.update(range(start, end + 1, step))
    return values


@dataclass(frozen=True)
class Cron:
    expression: str
    minutes: frozenset[int]
    hours: frozenset[int]
    days: frozenset[int]
    months: frozenset[int]
    weekdays: frozenset[int]  # 0 = dimanche
    any_day: bool
    any_weekday: bool

    @classmethod
    def parse(cls, expression: str) -> Cron:
        text = MACROS.get(expression.strip().lower(), expression.strip())
        fields = text.split()
        if len(fields) != 5:
            raise CronError("cinq champs attendus : minute heure jour mois jour-de-semaine (ex. 30 6 * * 1-5)")
        weekdays = {d % 7 for d in _field(fields[4], 0, 7, DAYS)}
        return cls(
            expression=expression.strip(),
            minutes=frozenset(_field(fields[0], 0, 59)),
            hours=frozenset(_field(fields[1], 0, 23)),
            days=frozenset(_field(fields[2], 1, 31)),
            months=frozenset(_field(fields[3], 1, 12, MONTHS)),
            weekdays=frozenset(weekdays),
            any_day=fields[2] == "*",
            any_weekday=fields[4] == "*",
        )

    def _day_matches(self, moment: datetime) -> bool:
        in_month = moment.day in self.days
        in_week = (moment.isoweekday() % 7) in self.weekdays
        if self.any_day and self.any_weekday:
            return True
        if self.any_day:
            return in_week
        if self.any_weekday:
            return in_month
        return in_month or in_week

    def next_after(self, after: float, tz: ZoneInfo | None = None) -> float:
        """Prochaine échéance strictement postérieure à l'instant `after` (secondes depuis l'epoch)."""
        zone = tz or timezone()
        local = datetime.fromtimestamp(after, zone).replace(tzinfo=None, second=0, microsecond=0) + timedelta(minutes=1)
        for _ in range(200_000):  # bornes larges : 5 ans en sauts de jour/heure/minute
            if local.month not in self.months:
                local = (local.replace(day=1, hour=0, minute=0) + timedelta(days=32)).replace(day=1)
                continue
            if not self._day_matches(local):
                local = local.replace(hour=0, minute=0) + timedelta(days=1)
                continue
            if local.hour not in self.hours:
                local = local.replace(minute=0) + timedelta(hours=1)
                continue
            if local.minute not in self.minutes:
                local += timedelta(minutes=1)
                continue
            aware = local.replace(tzinfo=zone)
            instant = aware.timestamp()
            if datetime.fromtimestamp(instant, zone).replace(tzinfo=None) == local:  # l'heure existe
                return instant
            local += timedelta(minutes=1)  # heure sautée par le passage à l'heure d'été
        raise CronError("aucune échéance dans les prochaines années : expression jamais satisfaite ?")

    def upcoming(self, after: float, count: int = 5) -> list[float]:
        out: list[float] = []
        for _ in range(count):
            after = self.next_after(after)
            out.append(after)
        return out


def timezone() -> ZoneInfo:
    return ZoneInfo(os.environ.get("ETL_TIMEZONE", "Europe/Paris"))
