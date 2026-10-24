"""Разбор OGG/Opus по байтам: формат, число каналов и длительность — без ffmpeg и без провайдера.

Голосовые MAX — OGG-контейнер с Opus. Формат определяем по сигнатуре `OggS`, а не по расширению
или mime (CLAUDE.md, подводные камни ASR). Длительность — гранула последней страницы минус pre-skip
из заголовка OpusHead, в отсчётах 48 кГц (RFC 7845): так лимит провайдера проверяется до платного
запроса.
"""

import struct
from dataclasses import dataclass
from typing import Final

from asr.domain.failures import Failure, TranscribeError

OGG_MAGIC: Final = b"OggS"
OPUS_HEAD: Final = b"OpusHead"
# Гранула Opus всегда в отсчётах 48 кГц, независимо от исходной частоты (RFC 7845, 4)
OPUS_GRANULE_RATE: Final = 48_000
# Заголовок страницы до таблицы сегментов: OggS, версия, флаги, гранула, serial, номер, crc, число
_PAGE_HEADER: Final = struct.Struct("<4sBBqIIIB")
# OpusHead: сигнатура, версия, каналы, pre-skip, частота исходника
_OPUS_HEAD: Final = struct.Struct("<8sBBHI")
# гранула -1: на странице не заканчивается ни один пакет
_NO_GRANULE: Final = -1


# Примечание: что узнали из файла: длительность и число каналов
@dataclass(frozen=True)
class AudioInfo:
    durationMs: int
    channels: int


# Примечание: страница OGG: гранула и её данные (первый пакет страницы — с начала payload)
@dataclass(frozen=True)
class _Page:
    granule: int
    payload: bytes


# Примечание: файл похож на OGG — по первым байтам
def isOgg(content: bytes) -> bool:
    return content.startswith(OGG_MAGIC)


# Примечание: длительность и каналы OGG/Opus; не OGG, не Opus или битый файл — unsupported_format
def probeOggOpus(content: bytes) -> AudioInfo:
    if not isOgg(content):
        raise _unsupported("не OGG")
    pages = list(_pages(content))
    head = pages[0].payload
    if len(head) < _OPUS_HEAD.size or not head.startswith(OPUS_HEAD):
        raise _unsupported("OGG без Opus")
    _, _, channels, preSkip, _ = _OPUS_HEAD.unpack_from(head)
    granules = [page.granule for page in pages if page.granule != _NO_GRANULE]
    samples = max(0, max(granules, default=0) - preSkip)
    return AudioInfo(durationMs=samples * 1000 // OPUS_GRANULE_RATE, channels=channels)


# Примечание: страницы подряд от начала файла; обрезанная страница или мусор между ними — отказ
def _pages(content: bytes) -> list[_Page]:
    pages: list[_Page] = []
    offset = 0
    while offset < len(content):
        if len(content) - offset < _PAGE_HEADER.size:
            raise _unsupported("обрезанный заголовок страницы")
        magic, _, _, granule, _, _, _, segments = _PAGE_HEADER.unpack_from(content, offset)
        if magic != OGG_MAGIC:
            raise _unsupported("нет сигнатуры страницы")
        tableStart = offset + _PAGE_HEADER.size
        table = content[tableStart : tableStart + segments]
        if len(table) != segments:
            raise _unsupported("обрезанная таблица сегментов")
        bodyStart = tableStart + segments
        bodyEnd = bodyStart + sum(table)
        if bodyEnd > len(content):
            raise _unsupported("обрезанная страница")
        pages.append(_Page(granule=granule, payload=content[bodyStart:bodyEnd]))
        offset = bodyEnd
    return pages


# Примечание: отказ «не тот формат» с причиной для лога
def _unsupported(reason: str) -> TranscribeError:
    return TranscribeError(Failure.UNSUPPORTED_FORMAT, "нужен OGG/Opus", {"reason": reason})
