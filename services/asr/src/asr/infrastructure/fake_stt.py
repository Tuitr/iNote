"""Фейковый режим ASR_PROVIDER=fake: без ключей и денег — для mock-стенда, e2e и CI (план A).

Голосовое фейкового MAX — «OggS» + текст в UTF-8; фейк «распознаёт» этот текст. Спецслучаи —
те же, что у прежней заглушки C (`deploy/stub/asr`), поэтому сценарии e2e не меняются:

| После «OggS»        | Итог                                   |
|---------------------|----------------------------------------|
| пусто               | `no_speech` 422                        |
| `~low~ текст`       | текст, confidence 0.42, low_confidence |
| `~long~ …`          | `audio_too_long` 413                   |
| `~down~ …`          | `upstream_unavailable` 503             |
| не начинается с OggS| `unsupported_format` 400               |

Заголовок `X-Fake-Text` (UTF-8, URL-кодированный) подменяет текст — см. presentation/routes.py.
"""

from typing import Final

from asr.application.ports import AudioClip, Recognition
from asr.domain.failures import Failure, TranscribeError
from asr.domain.ogg import OGG_MAGIC, AudioInfo, isOgg

PROVIDER_NAME: Final = "fake"
LOW_PREFIX: Final = "~low~"
LONG_PREFIX: Final = "~long~"
DOWN_PREFIX: Final = "~down~"
CONFIDENCE: Final = 0.95
LOW_CONFIDENCE: Final = 0.42
# ~60 мс на символ — правдоподобная длительность фразы
MS_PER_CHAR: Final = 60
# «бесконечно длинное» голосовое для ~long~
TOO_LONG_MS: Final = 10**9


# Примечание: текст фейкового голосового — всё после сигнатуры
def fakeText(content: bytes) -> str:
    return content[len(OGG_MAGIC) :].decode("utf-8", errors="replace").strip()


# Примечание: фейковое голосовое из текста (для X-Fake-Text и тестов)
def fakeAudio(text: str) -> bytes:
    return OGG_MAGIC + text.encode()


# Примечание: «разбор» фейкового голосового: формат по сигнатуре, длительность — по длине текста
class FakeAudioProbe:
    # Примечание: ~long~ — заведомо длиннее любого лимита
    def probe(self, content: bytes) -> AudioInfo:
        if not isOgg(content):
            raise TranscribeError(Failure.UNSUPPORTED_FORMAT, "нужен OGG/Opus")
        text = fakeText(content)
        if text.startswith(LONG_PREFIX):
            return AudioInfo(durationMs=TOO_LONG_MS, channels=1)
        return AudioInfo(durationMs=len(text) * MS_PER_CHAR, channels=1)


# Примечание: фейковый провайдер: возвращает текст голосового, спецслучаи — по префиксу
class FakeSpeechToText:
    # Примечание: имя для логов
    @property
    def name(self) -> str:
        return PROVIDER_NAME

    # Примечание: ~down~ — провайдер недоступен; ~low~ — неуверенно; пусто — пустой ответ
    async def recognize(self, clip: AudioClip) -> Recognition:
        text = fakeText(clip.content)
        if text.startswith(DOWN_PREFIX):
            raise TranscribeError(Failure.UPSTREAM_UNAVAILABLE, "провайдер недоступен (фейк)")
        if text.startswith(LOW_PREFIX):
            return Recognition(
                text=text.removeprefix(LOW_PREFIX).strip(), confidence=LOW_CONFIDENCE
            )
        return Recognition(text=text, confidence=CONFIDENCE)
