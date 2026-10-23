"""Экранирование пользовательского текста перед подстановкой в markdown MAX (названия записей).

Общее для агента (ответы) и бота (уведомления J4).
"""

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


# Примечание: обрезка до limit символов с многоточием, не разрывая экранирование: если граница
# пришлась между «\» и символом, «\» уходит вместе с хвостом (иначе MAX покажет «\…»)
def cutText(text: str, limit: int, ellipsis: str = "…") -> str:
    if len(text) <= limit:
        return text
    head = text[: limit - len(ellipsis)]
    trailing = len(head) - len(head.rstrip("\\"))
    if trailing % 2 == 1:
        head = head[:-1]
    return head + ellipsis
