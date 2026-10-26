"""Распознанная речь и нормализация текста перед отдачей боту.

`text` сразу готов к показу и к LLM (план A): без лишних пробелов по краям и внутри. Заглавную
букву ставит адаптер провайдера, который отдаёт текст строчными (SpeechKit); числа цифрами делает
сам SpeechKit (`rawResults=false`). Фейк отдаёт текст как есть — сценарии e2e его сверяют.
`raw_text` — ответ провайдера как есть.
"""

import re
from dataclasses import dataclass
from typing import Final

_SPACES_RE: Final = re.compile(r"\s+")


# Примечание: итог распознавания — поля TranscribeResponse (J1)
@dataclass(frozen=True)
class Transcript:
    text: str
    rawText: str
    confidence: float
    lowConfidence: bool
    durationMs: int


# Примечание: пробелы схлопнуть и убрать по краям; пусто → пусто
def normalizeText(raw: str) -> str:
    return _SPACES_RE.sub(" ", raw).strip()


# Примечание: первая буква — заглавной (у SpeechKit весь текст строчными)
def capitalizeFirst(text: str) -> str:
    return text[:1].upper() + text[1:]
