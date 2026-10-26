"""Тестовые голосовые: настоящие OGG от ffmpeg (fixtures/) и сборка OGG-страниц для краёв разбора.

Фикстуры получены так (libopus, моно и стерео, 48 кГц; Vorbis — для «OGG, но не Opus»):

    ffmpeg -f lavfi -i "sine=frequency=440:duration=2.5:sample_rate=48000" -ac 1 \
        -c:a libopus -b:a 16k sine_2500ms_mono.ogg
    ffmpeg -f lavfi -i "sine=frequency=440:duration=1:sample_rate=48000" -ac 2 \
        -c:a libopus -b:a 16k sine_1000ms_stereo.ogg
    ffmpeg -f lavfi -i "sine=frequency=440:duration=1:sample_rate=44100" -ac 1 \
        -c:a libvorbis vorbis_1000ms.ogg
"""

import struct
from pathlib import Path
from typing import Final

FIXTURES: Final = Path(__file__).resolve().parent / "fixtures"
MONO_2500MS: Final = FIXTURES / "sine_2500ms_mono.ogg"
STEREO_1000MS: Final = FIXTURES / "sine_1000ms_stereo.ogg"
VORBIS_1000MS: Final = FIXTURES / "vorbis_1000ms.ogg"
SAMPLE_RATE: Final = 48_000
PRE_SKIP: Final = 312
NO_GRANULE: Final = -1
_SEGMENT: Final = 255


# Примечание: байты фикстуры
def fixture(path: Path) -> bytes:
    return path.read_bytes()


# Примечание: одна страница OGG (crc не считаем — разбор его не проверяет, провайдер получит файл)
def oggPage(granule: int, payload: bytes, sequence: int = 0) -> bytes:
    lacing = [_SEGMENT] * (len(payload) // _SEGMENT) + [len(payload) % _SEGMENT]
    header = struct.pack("<4sBBqIIIB", b"OggS", 0, 0, granule, 1, sequence, 0, len(lacing))
    return header + bytes(lacing) + payload


# Примечание: заголовок OpusHead (RFC 7845, 5.1)
def opusHead(channels: int = 1, preSkip: int = PRE_SKIP) -> bytes:
    return struct.pack("<8sBBHIhB", b"OpusHead", 1, channels, preSkip, SAMPLE_RATE, 0, 0)


# Примечание: синтетический OGG/Opus длительностью durationMs: OpusHead, OpusTags и страницы звука
def oggOpus(durationMs: int, *, channels: int = 1, audioPages: int = 3) -> bytes:
    total = PRE_SKIP + durationMs * SAMPLE_RATE // 1000
    pages = [oggPage(0, opusHead(channels)), oggPage(0, b"OpusTags" + b"\0" * 8, 1)]
    for index in range(1, audioPages + 1):
        granule = total * index // audioPages
        pages.append(oggPage(granule, b"\x01" * 300, index + 1))
    return b"".join(pages)
