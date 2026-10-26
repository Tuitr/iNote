"""common.markdown: экранирование названий и обрезка, которая не рвёт пару «\\*» (S4)."""

import pytest

from common.markdown import cutText, escapeMarkdown


# Примечание: короткий текст не трогаем
def test_shortUntouched() -> None:
    assert cutText("привет", 10) == "привет"


# Примечание: граница между «\» и «*» — «\» уходит в хвост, в конце нет висящего «\»
@pytest.mark.parametrize("prefix", ["", "x"])
def test_cutKeepsEscapePairs(prefix: str) -> None:
    escaped = escapeMarkdown(prefix + "*" * 50)
    for limit in range(5, 40):
        cut = cutText(escaped, limit)
        assert len(cut) <= limit
        head = cut.removesuffix("…")
        assert (len(head) - len(head.rstrip("\\"))) % 2 == 0, cut


# Примечание: своё многоточие учитывается в лимите
def test_customEllipsis() -> None:
    assert cutText("abcdefgh", 6, "...") == "abc..."
