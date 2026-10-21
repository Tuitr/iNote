"""Фейки портов бота для тестов. MaxGateway — настоящий FakeMaxGateway из infrastructure."""

import asyncio
import json
from pathlib import Path
from typing import Final

from bot.application.ports import RateDecision, StoredAction
from bot.domain.updates import IncomingUpdate, MaxUserId, UpdateId, VoiceRef
from bot.domain.voice import AudioFile, Transcript, VoiceFailureError
from bot.infrastructure.max_mapper import mapRawUpdate
from contracts.reply import ActionRequest, MessageRequest, Reply

FIXTURES_DIR: Final = Path(__file__).parent / "fixtures" / "max_updates"
REPLY_EXAMPLES: Final = Path(__file__).resolve().parents[3] / "contracts" / "examples" / "reply"
FIXTURE_NAMES: Final = ("text", "command", "voice", "callback", "bot_started")


# Примечание: сырой апдейт MAX из fixtures/max_updates/<name>.json
def loadMaxUpdate(name: str) -> dict[str, object]:
    raw: dict[str, object] = json.loads((FIXTURES_DIR / f"{name}.json").read_text())
    return raw


# Примечание: фикстура, уже переведённая в IncomingUpdate
def loadIncoming(name: str) -> IncomingUpdate:
    update = mapRawUpdate(loadMaxUpdate(name))
    assert update is not None
    return update


# Примечание: дедуп в памяти (вместо Redis SET NX)
class InMemoryDeduplicator:
    # Примечание: множество уже виденных id
    def __init__(self) -> None:
        self.seen: set[UpdateId] = set()

    # Примечание: True при первом появлении id
    async def isFirstSeen(self, updateId: UpdateId) -> bool:
        if updateId in self.seen:
            return False
        self.seen.add(updateId)
        return True


# Примечание: лимитер с настоящим подсчётом, но без окна по времени
class CountingRateLimiter:
    # Примечание: limit — сколько апдейтов на пользователя пропускаем
    def __init__(self, limit: int = 20) -> None:
        self.limit = limit
        self.counts: dict[MaxUserId, int] = {}

    # Примечание: та же логика, что у Redis: notify только на первом превышении
    async def check(self, maxUserId: MaxUserId) -> RateDecision:
        count = self.counts[maxUserId] = self.counts.get(maxUserId, 0) + 1
        if count <= self.limit:
            return RateDecision(allowed=True)
        return RateDecision(allowed=False, notify=count == self.limit + 1)


# Примечание: агент по сценарию: заданный Reply, журнал запросов, умеет «думать» и падать
class FakeAgent:
    # Примечание: reply — что вернуть; delayS — сколько «думать»; error — чем упасть
    def __init__(
        self, reply: Reply | None = None, *, delayS: float = 0.0, error: Exception | None = None
    ) -> None:
        self.reply = reply or exampleReply("confirm.json")
        self.delayS = delayS
        self.error = error
        self.messages: list[MessageRequest] = []
        self.actions: list[ActionRequest] = []

    # Примечание: ход агента по тексту
    async def sendMessage(self, request: MessageRequest) -> Reply:
        self.messages.append(request)
        return await self._answer()

    # Примечание: действие без LLM
    async def sendAction(self, request: ActionRequest) -> Reply:
        self.actions.append(request)
        return await self._answer()

    # Примечание: задержка, затем ошибка или ответ
    async def _answer(self) -> Reply:
        if self.delayS:
            await asyncio.sleep(self.delayS)
        if self.error is not None:
            raise self.error
        return self.reply


# Примечание: хранилище кнопок в памяти с тем же форматом ключа, что у Redis
class InMemoryCallbackStore:
    # Примечание: saved — что лежит за ключами
    def __init__(self) -> None:
        self.saved: dict[str, StoredAction] = {}

    # Примечание: ключ — порядковый, 16 символов, как token_urlsafe(12)
    async def save(self, action: StoredAction) -> str:
        key = f"key{len(self.saved):013d}"
        self.saved[key] = action
        return key

    # Примечание: None — ключа нет
    async def load(self, key: str) -> StoredAction | None:
        return self.saved.get(key)


# Примечание: Reply из contracts/examples/reply/<name>
def exampleReply(name: str) -> Reply:
    return Reply.fromJson((REPLY_EXAMPLES / name).read_bytes())


# Примечание: скачивание голосового: байты по ссылке или ошибка по сценарию
class FakeDownloader:
    # Примечание: error — чем упасть (например, VoiceFailureError TOO_LONG)
    def __init__(self, error: VoiceFailureError | None = None) -> None:
        self.error = error
        self.urls: list[str] = []

    # Примечание: «скачать» — вернуть OggS + url (чтобы ASR-фейк видел, какое это голосовое)
    async def download(self, voice: VoiceRef) -> AudioFile:
        self.urls.append(voice.url)
        if self.error is not None:
            raise self.error
        return AudioFile(content=b"OggS" + voice.url.encode(), mime="audio/ogg")


# Примечание: ASR по сценарию: фиксированный Transcript, задержка или ошибка
class FakeAsr:
    # Примечание: text — что «распознано»; low — неуверенно; delayS — сколько думать
    def __init__(
        self,
        text: str = "Напомни завтра в 10 позвонить Ивану",
        *,
        low: bool = False,
        delayS: float = 0.0,
        error: VoiceFailureError | None = None,
    ) -> None:
        self.text = text
        self.low = low
        self.delayS = delayS
        self.error = error
        self.calls: list[AudioFile] = []

    # Примечание: «распознанный» текст сценария или ошибка
    async def transcribe(self, audio: AudioFile) -> Transcript:
        self.calls.append(audio)
        if self.delayS:
            await asyncio.sleep(self.delayS)
        if self.error is not None:
            raise self.error
        return Transcript(
            text=self.text,
            rawText=self.text.lower(),
            confidence=0.4 if self.low else 0.95,
            lowConfidence=self.low,
            durationMs=2100,
        )
