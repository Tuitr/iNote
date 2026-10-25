"""TranscribeAudio: лимиты до провайдера, кеш, no_speech, уверенность и нормализация."""

from typing import Final

import pytest

from asr.application.ports import Recognition
from asr.application.transcribe import (
    FULL_CONFIDENCE,
    TranscribeAudio,
    TranscribeLimits,
    audioDigest,
)
from asr.domain.failures import Failure, TranscribeError
from asr.domain.ogg import AudioInfo
from services.asr.tests.fakes import FixedProbe, MemoryCache, ScriptedConverter, ScriptedStt

AUDIO: Final = b"OggS-voice-bytes"
LANG: Final = "ru-RU"
LIMITS: Final = TranscribeLimits(maxBytes=1000, maxDurationMs=30_000, lowConfidenceBelow=0.6)


# Примечание: сценарий с фейками; части можно подменить
def make(
    stt: ScriptedStt | None = None,
    probe: FixedProbe | None = None,
    cache: MemoryCache | None = None,
) -> tuple[TranscribeAudio, ScriptedStt, MemoryCache]:
    stt = stt or ScriptedStt()
    cache = cache if cache is not None else MemoryCache()
    return TranscribeAudio(stt, probe or FixedProbe(), cache, LIMITS), stt, cache


# Примечание: успех: нормализованный текст, сырой — как у провайдера, длительность — из файла
async def test_recognizedTextIsNormalized() -> None:
    transcribe, stt, _ = make(ScriptedStt(result=Recognition("  что у  меня завтра ", 0.9)))

    transcript = await transcribe(AUDIO, LANG)

    assert transcript.text == "что у меня завтра"
    assert transcript.rawText == "что у  меня завтра"
    assert (transcript.confidence, transcript.lowConfidence) == (0.9, False)
    assert transcript.durationMs == 2000
    assert [(c.content, c.lang) for c in stt.clips] == [(AUDIO, LANG)]


# Примечание: сырой текст провайдера (raw) уходит в raw_text, текст для показа — в text
async def test_rawTextFromProvider() -> None:
    recognition = Recognition("Напомни в 10", None, raw="напомни в 10")
    transcribe, _, _ = make(ScriptedStt(result=recognition))

    transcript = await transcribe(AUDIO, LANG)

    assert (transcript.text, transcript.rawText) == ("Напомни в 10", "напомни в 10")


# Примечание: провайдер без уверенности (SpeechKit v1) — 1.0 и не «неуверенно»
async def test_unknownConfidenceIsFull() -> None:
    transcribe, _, _ = make(ScriptedStt(result=Recognition("текст", None)))

    transcript = await transcribe(AUDIO, LANG)

    assert (transcript.confidence, transcript.lowConfidence) == (FULL_CONFIDENCE, False)


@pytest.mark.parametrize(
    ("confidence", "expected", "low"),
    [(0.59, 0.59, True), (0.6, 0.6, False), (1.7, 1.0, False), (-0.2, 0.0, True)],
)
# Примечание: порог строго «ниже»; значения вне [0, 1] прижимаются к краям
async def test_lowConfidenceThreshold(confidence: float, expected: float, low: bool) -> None:
    transcribe, _, _ = make(ScriptedStt(result=Recognition("текст", confidence)))

    transcript = await transcribe(AUDIO, LANG)

    assert (transcript.confidence, transcript.lowConfidence) == (expected, low)


@pytest.mark.parametrize("raw", ["", "   ", "\n\t"])
# Примечание: пустой ответ провайдера — no_speech, в кеш не попадает
async def test_emptyRecognitionIsNoSpeech(raw: str) -> None:
    transcribe, _, cache = make(ScriptedStt(result=Recognition(raw, 0.9)))

    with pytest.raises(TranscribeError) as caught:
        await transcribe(AUDIO, LANG)

    assert caught.value.failure is Failure.NO_SPEECH
    assert cache.items == {}


# Примечание: пустой файл — unsupported_format, провайдер не вызван
async def test_emptyFileIsUnsupported() -> None:
    transcribe, stt, _ = make()

    with pytest.raises(TranscribeError) as caught:
        await transcribe(b"", LANG)

    assert caught.value.failure is Failure.UNSUPPORTED_FORMAT
    assert stt.clips == []


# Примечание: больше лимита байт — audio_too_long до разбора и провайдера (за это не платим)
async def test_tooManyBytes() -> None:
    probe = FixedProbe()
    transcribe, stt, _ = make(probe=probe)

    with pytest.raises(TranscribeError) as caught:
        await transcribe(b"x" * (LIMITS.maxBytes + 1), LANG)

    assert caught.value.failure is Failure.AUDIO_TOO_LONG
    assert caught.value.details == {"max_bytes": LIMITS.maxBytes}
    assert (probe.calls, stt.clips) == (0, [])


# Примечание: ровно лимит байт — ещё можно
async def test_exactlyMaxBytesIsAccepted() -> None:
    transcribe, stt, _ = make()

    await transcribe(b"x" * LIMITS.maxBytes, LANG)

    assert len(stt.clips) == 1


@pytest.mark.parametrize(("durationMs", "accepted"), [(30_000, True), (30_001, False)])
# Примечание: длительность — по файлу, до провайдера; ровно лимит — ещё можно
async def test_durationLimit(durationMs: int, accepted: bool) -> None:
    probe = FixedProbe(info=AudioInfo(durationMs=durationMs, channels=1))
    transcribe, stt, _ = make(probe=probe)

    if accepted:
        await transcribe(AUDIO, LANG)
    else:
        with pytest.raises(TranscribeError) as caught:
            await transcribe(AUDIO, LANG)
        assert caught.value.failure is Failure.AUDIO_TOO_LONG
        assert caught.value.details == {"max_duration_ms": 30_000}
    assert len(stt.clips) == int(accepted)


# Примечание: стерео — unsupported_format (SpeechKit берёт только моно), провайдер не вызван
async def test_stereoIsUnsupported() -> None:
    transcribe, stt, _ = make(probe=FixedProbe(info=AudioInfo(durationMs=1000, channels=2)))

    with pytest.raises(TranscribeError) as caught:
        await transcribe(AUDIO, LANG)

    assert caught.value.failure is Failure.UNSUPPORTED_FORMAT
    assert stt.clips == []


# Примечание: отказ разбора уходит как есть
async def test_probeFailurePropagates() -> None:
    error = TranscribeError(Failure.UNSUPPORTED_FORMAT, "нужен OGG/Opus")
    transcribe, stt, _ = make(probe=FixedProbe(error=error))

    with pytest.raises(TranscribeError) as caught:
        await transcribe(AUDIO, LANG)

    assert caught.value is error
    assert stt.clips == []


# Примечание: повтор того же голосового — из кеша, провайдер второй раз не вызывается
async def test_repeatComesFromCache() -> None:
    transcribe, stt, cache = make()

    first = await transcribe(AUDIO, LANG)
    second = await transcribe(AUDIO, LANG)

    assert first == second
    assert len(stt.clips) == 1
    assert list(cache.items) == [audioDigest(AUDIO, LANG)]


# Примечание: другой язык — другой ключ кеша
async def test_cacheKeyDependsOnLang() -> None:
    assert audioDigest(AUDIO, "ru-RU") != audioDigest(AUDIO, "en-US")
    assert audioDigest(AUDIO, "ru-RU") == audioDigest(AUDIO, "ru-RU")


# Примечание: не OGG (MP3 из MAX) — перекодируется; разбор и провайдер получают уже OGG
async def test_notOggIsConverted() -> None:
    converter = ScriptedConverter()
    stt = ScriptedStt()
    transcribe = TranscribeAudio(stt, FixedProbe(), MemoryCache(), LIMITS, converter)

    await transcribe(b"ID3-mp3-bytes", LANG)

    assert converter.inputs == [b"ID3-mp3-bytes"]
    assert [c.content for c in stt.clips] == [converter.output]


# Примечание: OGG уходит как есть, без перекодирования
async def test_oggIsNotConverted() -> None:
    converter = ScriptedConverter()
    stt = ScriptedStt()
    transcribe = TranscribeAudio(stt, FixedProbe(), MemoryCache(), LIMITS, converter)

    await transcribe(AUDIO, LANG)

    assert converter.inputs == []
    assert [c.content for c in stt.clips] == [AUDIO]


# Примечание: ошибка провайдера уходит как есть и не кешируется
async def test_providerFailurePropagates() -> None:
    error = TranscribeError(Failure.UPSTREAM_TIMEOUT, "не ответил")
    transcribe, _, cache = make(ScriptedStt(error=error))

    with pytest.raises(TranscribeError) as caught:
        await transcribe(AUDIO, LANG)

    assert caught.value is error
    assert cache.items == {}
