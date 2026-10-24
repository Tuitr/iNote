"""Разбор OGG/Opus по байтам: длительность и каналы — на настоящих файлах ffmpeg и на краях."""

import pytest

from asr.domain.failures import Failure, TranscribeError
from asr.domain.ogg import isOgg, probeOggOpus
from services.asr.tests.oggdata import (
    MONO_2500MS,
    NO_GRANULE,
    STEREO_1000MS,
    VORBIS_1000MS,
    fixture,
    oggOpus,
    oggPage,
    opusHead,
)


# Примечание: файл libopus: длительность без pre-skip, моно
def test_realMonoOpusFile() -> None:
    info = probeOggOpus(fixture(MONO_2500MS))

    assert (info.durationMs, info.channels) == (2500, 1)


# Примечание: стерео — каналы видно (SpeechKit принимает только моно, решает сценарий)
def test_realStereoOpusFile() -> None:
    info = probeOggOpus(fixture(STEREO_1000MS))

    assert (info.durationMs, info.channels) == (1000, 2)


# Примечание: OGG с Vorbis — не наш формат
def test_vorbisIsUnsupported() -> None:
    with pytest.raises(TranscribeError) as caught:
        probeOggOpus(fixture(VORBIS_1000MS))

    assert caught.value.failure is Failure.UNSUPPORTED_FORMAT


# Примечание: длительность — по последней грануле, страницы без гранулы (-1) не считаются
def test_durationFromLastGranule() -> None:
    content = oggOpus(12_345) + oggPage(NO_GRANULE, b"\x01" * 10, 9)

    assert probeOggOpus(content).durationMs == 12_345


# Примечание: пакет длиннее 255 байт — несколько сегментов, разбор не сбивается
def test_longPayloadSpansSegments() -> None:
    content = oggPage(0, opusHead()) + oggPage(48_000 + 312, b"\x02" * 1000, 1)

    assert probeOggOpus(content).durationMs == 1000


# Примечание: гранула меньше pre-skip — длительность 0, а не отрицательная
def test_granuleBelowPreSkipIsZero() -> None:
    assert probeOggOpus(oggPage(0, opusHead()) + oggPage(100, b"\x01", 1)).durationMs == 0


@pytest.mark.parametrize(
    ("content", "reason"),
    [
        (b"RIFF....WAVEfmt ", "не OGG"),
        (b"OggS\x00", "обрезанный заголовок страницы"),
        (oggPage(0, opusHead())[:27], "обрезанная таблица сегментов"),
        (oggPage(0, opusHead())[:-5], "обрезанная страница"),
        (oggPage(0, opusHead()) + b"garbage-garbage-garbage-garbage", "нет сигнатуры страницы"),
        (oggPage(0, b"OpusTags"), "OGG без Opus"),
        (oggPage(0, b"Opus"), "OGG без Opus"),
    ],
    ids=["wav", "short", "table", "body", "junk", "no-head", "tiny-head"],
)
# Примечание: битый или чужой файл — unsupported_format с причиной для лога
def test_brokenFilesAreUnsupported(content: bytes, reason: str) -> None:
    with pytest.raises(TranscribeError) as caught:
        probeOggOpus(content)

    assert caught.value.failure is Failure.UNSUPPORTED_FORMAT
    assert caught.value.details == {"reason": reason}


# Примечание: формат — по сигнатуре в начале, а не по расширению
def test_isOggBySignature() -> None:
    assert isOgg(b"OggS\x00rest")
    assert not isOgg(b"xOggS")
    assert not isOgg(b"")
