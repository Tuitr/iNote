"""Нормализация распознанного текста: пробелы, заглавная буква (для SpeechKit); слова не трогаем."""

import pytest

from asr.domain.failures import HTTP_STATUS, Failure, TranscribeError
from asr.domain.transcript import capitalizeFirst, normalizeText


@pytest.mark.parametrize(
    ("raw", "text"),
    [
        ("напомни завтра в 10 позвонить ивану", "напомни завтра в 10 позвонить ивану"),
        ("  что   у меня\nзавтра  ", "что у меня завтра"),
        ("10 минут", "10 минут"),
        ("", ""),
        ("   \t", ""),
    ],
)
# Примечание: пробелы схлопнуты и убраны по краям, слова и регистр — как у провайдера
def test_normalizeText(raw: str, text: str) -> None:
    assert normalizeText(raw) == text


@pytest.mark.parametrize(
    ("raw", "text"),
    [
        ("напомни завтра", "Напомни завтра"),
        ("Уже", "Уже"),
        ("ё", "Ё"),
        ("10 минут", "10 минут"),
        ("", ""),
    ],
)
# Примечание: заглавная — только первая буква (SpeechKit отдаёт строчными)
def test_capitalizeFirst(raw: str, text: str) -> None:
    assert capitalizeFirst(raw) == text


# Примечание: статусы J1 у каждой причины отказа
def test_httpStatusPerFailure() -> None:
    assert HTTP_STATUS == {
        Failure.NO_SPEECH: 422,
        Failure.AUDIO_TOO_LONG: 413,
        Failure.UNSUPPORTED_FORMAT: 400,
        Failure.UPSTREAM_UNAVAILABLE: 503,
        Failure.UPSTREAM_TIMEOUT: 504,
    }
    assert TranscribeError(Failure.NO_SPEECH, "x").httpStatus == 422
