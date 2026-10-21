import uuid
from datetime import date
from typing import Final

import pytest
from libs.contracts.tests.examples import loadExample

from contracts.start_param import StartParam, StartParamKind, isValidStartParam, parseStartParam

ITEM_ID: Final = uuid.UUID("7f3a9c2e-5b1d-4c8e-9a21-0d4e5f6a8b90")


# Примечание: все допустимые примеры из contracts/examples разбираются и собираются обратно
def test_validExamplesRoundTrip() -> None:
    values = loadExample("start_param/valid.json")
    assert isinstance(values, list)
    for raw in values:
        assert str(parseStartParam(raw)) == raw


# Примечание: недопустимые примеры отклоняются
def test_invalidExamplesRejected() -> None:
    values = loadExample("start_param/invalid.json")
    assert isinstance(values, list)
    for raw in values:
        assert not isValidStartParam(raw), raw


# Примечание: разбор даёт вид экрана и данные
def test_parsedFields() -> None:
    assert parseStartParam("today").kind is StartParamKind.TODAY
    assert parseStartParam("day_2026-10-21").day == date(2026, 10, 21)
    assert parseStartParam(f"item_{ITEM_ID}").itemId == ITEM_ID


# Примечание: построители дают каноничные строки; неделя — только с понедельника
def test_builders() -> None:
    assert str(StartParam.today()) == "today"
    assert str(StartParam.dialog()) == "dialog"
    assert str(StartParam.forDay(date(2026, 12, 31))) == "day_2026-12-31"
    assert str(StartParam.forWeek(date(2026, 10, 19))) == "week_2026-10-19"
    assert str(StartParam.forItem(ITEM_ID)) == f"item_{ITEM_ID}"
    with pytest.raises(ValueError, match="понедельника"):
        StartParam.forWeek(date(2026, 10, 21))


# Примечание: длина ровно 512 по алфавиту проходит проверку алфавита, но не формата
def test_lengthAndAlphabet() -> None:
    with pytest.raises(ValueError, match="1–512"):
        parseStartParam("a" * 513)
    with pytest.raises(ValueError, match="неизвестный формат"):
        parseStartParam("a" * 512)


# Примечание: несогласованный объект не собирается
def test_inconsistentStartParamRejected() -> None:
    with pytest.raises(ValueError, match="обязательна"):
        StartParam(StartParamKind.DAY)
    with pytest.raises(ValueError, match="не нужна"):
        StartParam(StartParamKind.TODAY, day=date(2026, 10, 21))
    with pytest.raises(ValueError, match="только для item"):
        StartParam(StartParamKind.ITEM)
