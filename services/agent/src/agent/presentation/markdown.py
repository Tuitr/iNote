"""Экранирование пользовательского текста перед подстановкой в markdown-ответ (названия записей)."""

import re
from typing import Final

# Разметка MAX: **жирный**, *курсив*, ~~зачёркнутый~~, `код`, [ссылка](url), ++подчёркнутый++
_INLINE_RE: Final = re.compile(r"([\\`*_~\[\]()|+])")
# В начале строки: заголовок, цитата, пункт списка; «1.» — экранируем точку
_BLOCK_START_RE: Final = re.compile(r"^([#>\-])")
_ORDERED_START_RE: Final = re.compile(r"^(\d+)([.)])")
_SPACES_RE: Final = re.compile(r"\s+")


# Примечание: название одной строкой с экранированной разметкой — пользователь не сломает ответ
def escapeMarkdown(text: str) -> str:
    flat = _SPACES_RE.sub(" ", text).strip()
    escaped = _INLINE_RE.sub(r"\\\1", flat)
    escaped = _BLOCK_START_RE.sub(r"\\\1", escaped)
    return _ORDERED_START_RE.sub(r"\1\\\2", escaped)
