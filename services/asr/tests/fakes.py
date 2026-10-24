"""Фейки портов ASR для unit-тестов сценария: провайдер по сценарию, кеш в памяти, сломанный кеш."""

from dataclasses import dataclass, field

from asr.application.ports import AudioClip, Recognition
from asr.domain.failures import TranscribeError
from asr.domain.ogg import AudioInfo
from asr.domain.transcript import Transcript


# Примечание: провайдер по сценарию: отдаёт result или бросает error; clips — что ему прислали
@dataclass
class ScriptedStt:
    result: Recognition = field(default_factory=lambda: Recognition("что у меня завтра", 0.9))
    error: TranscribeError | None = None
    clips: list[AudioClip] = field(default_factory=list)

    # Примечание: имя для логов
    @property
    def name(self) -> str:
        return "scripted"

    # Примечание: запомнить фрагмент и ответить по сценарию
    async def recognize(self, clip: AudioClip) -> Recognition:
        self.clips.append(clip)
        if self.error is not None:
            raise self.error
        return self.result


# Примечание: разбор аудио по сценарию: всегда одна и та же длительность и каналы
@dataclass
class FixedProbe:
    info: AudioInfo = field(default_factory=lambda: AudioInfo(durationMs=2000, channels=1))
    error: TranscribeError | None = None
    calls: int = 0

    # Примечание: info или error; calls — сколько раз разбирали
    def probe(self, content: bytes) -> AudioInfo:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.info


# Примечание: кеш в памяти: digest → Transcript
@dataclass
class MemoryCache:
    items: dict[str, Transcript] = field(default_factory=dict)

    # Примечание: сохранённое или None
    async def get(self, digest: str) -> Transcript | None:
        return self.items.get(digest)

    # Примечание: положить итог в память
    async def put(self, digest: str, transcript: Transcript) -> None:
        self.items[digest] = transcript
