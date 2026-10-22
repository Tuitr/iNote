"""Повторы, минимальный вариант S3: серия — одна запись с rrule (RFC 5545 без «RRULE:»).

Даты серии считаются в местном времени пользователя: «каждый понедельник в 9» остаётся 9:00
и после перевода часов. Начало серии — starts_at записи, а если его нет — remind_at.
"""

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from dateutil.rrule import rrule, rrulestr

from libs.core.errors import InvalidArgumentsError

_COUNT = re.compile(r"COUNT=(\d+)", re.IGNORECASE)
_UTC_UNTIL = re.compile(r"UNTIL=(\d{8}T\d{6})Z", re.IGNORECASE)


@dataclass(frozen=True)
class NextOccurrence:
    starts_at: datetime
    # Правило с остатком COUNT: серия теперь начинается с этого вхождения.
    rrule: str


def check_rrule(rule: str, series_start: datetime | None, tz: str) -> None:
    """Неверное правило или повтор без даты → validation_error, модель переспросит."""
    if series_start is None:
        raise InvalidArgumentsError(
            "Для повтора нужна дата: starts_at или remind_at", {"missing": ["starts_at"]}
        )
    _parse(rule, series_start, tz)


def next_occurrence(rule: str, current: datetime, tz: str, now: datetime) -> NextOccurrence | None:
    """Следующее вхождение позже текущего и позже «сейчас»; None — серия закончилась.

    Выполненные с опозданием вхождения, которые уже прошли, пропускаются и списываются с COUNT.
    """
    zone = ZoneInfo(tz)
    series = _parse(rule, current, tz)
    after = _local(max(current, now), zone)
    following = series.after(after)
    if following is None:
        return None
    passed = len(series.between(_local(current, zone), after, inc=True))
    return NextOccurrence(
        starts_at=following.replace(tzinfo=zone).astimezone(UTC),
        rrule=_COUNT.sub(lambda match: f"COUNT={int(match.group(1)) - passed}", rule),
    )


def _parse(rule: str, series_start: datetime, tz: str) -> rrule:
    zone = ZoneInfo(tz)
    try:
        # Модели часто пишут UNTIL в UTC («…Z»); серия считается в местном времени без пояса.
        local_rule = _UTC_UNTIL.sub(
            lambda match: f"UNTIL={_local_until(match.group(1), zone)}", rule
        )
        parsed = rrulestr(local_rule, dtstart=_local(series_start, zone))
    except (ValueError, TypeError) as error:
        raise _invalid() from error
    # Несколько правил подряд dateutil собирает в rruleset: серия у записи одна.
    if not isinstance(parsed, rrule):
        raise _invalid()
    return parsed


def _invalid() -> InvalidArgumentsError:
    return InvalidArgumentsError(
        "Неверное правило повтора, пример: FREQ=WEEKLY;BYDAY=MO", {"invalid": ["rrule"]}
    )


def _local(moment: datetime, zone: ZoneInfo) -> datetime:
    return moment.astimezone(zone).replace(tzinfo=None)


def _local_until(utc_value: str, zone: ZoneInfo) -> str:
    moment = datetime.strptime(utc_value, "%Y%m%dT%H%M%S").replace(tzinfo=UTC)
    return f"{_local(moment, zone):%Y%m%dT%H%M%S}"
