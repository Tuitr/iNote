"""YandexSpeechKit: запрос SpeechKit v1, разбор ответа, повторы только на временных сбоях, бюджет.

Провайдер подменён httpx.MockTransport — настоящих (платных) запросов нет.
"""

import asyncio
import json
from collections.abc import Callable, Iterator
from typing import Final

import httpx
import pytest
import structlog
from pydantic import SecretStr

from asr.application.ports import AudioClip
from asr.domain.failures import Failure, TranscribeError
from asr.domain.ogg import AudioInfo
from asr.infrastructure.settings import YANDEX_STT_URL
from asr.infrastructure.yandex_stt import YandexSpeechKit

API_KEY: Final = "AQVN-test-key-not-real"
CLIP: Final = AudioClip(content=b"OggS-opus", lang="ru-RU", info=AudioInfo(1500, 1))

type Handler = Callable[[httpx.Request], httpx.Response]


# Примечание: провайдер по очереди ответов; requests — что ушло в SpeechKit
class ScriptedSpeechKit:
    # Примечание: answers — ответ или исключение на каждую попытку по порядку
    def __init__(self, *answers: httpx.Response | Exception) -> None:
        self._answers: Iterator[httpx.Response | Exception] = iter(answers)
        self.requests: list[httpx.Request] = []

    # Примечание: обработчик MockTransport
    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        answer = next(self._answers)
        if isinstance(answer, Exception):
            raise answer
        return answer


# Примечание: адаптер с короткими паузами; транспорт — сценарий
def speechKit(
    handler: Handler,
    *,
    retries: int = 1,
    deadlineS: float = 5.0,
    folderId: str | None = None,
) -> YandexSpeechKit:
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return YandexSpeechKit(
        http,
        SecretStr(API_KEY),
        url=YANDEX_STT_URL,
        folderId=folderId,
        attemptTimeoutS=1.0,
        retries=retries,
        deadlineS=deadlineS,
        retryDelayS=0.0,
    )


# Примечание: ответ SpeechKit с телом JSON
def answer(status: int, body: object) -> httpx.Response:
    return httpx.Response(status, json=body)


# Примечание: успех: тело — байты как есть, ключ — Api-Key, параметры v1; текст с заглавной,
# сырой — как ответил SpeechKit; уверенности нет
async def test_recognizeSendsAudioAsIs() -> None:
    provider = ScriptedSpeechKit(answer(200, {"result": "напомни завтра в 10 позвонить"}))

    recognition = await speechKit(provider).recognize(CLIP)

    assert (recognition.text, recognition.raw, recognition.confidence) == (
        "Напомни завтра в 10 позвонить",
        "напомни завтра в 10 позвонить",
        None,
    )
    [request] = provider.requests
    assert request.method == "POST"
    assert str(request.url.copy_with(query=None)) == YANDEX_STT_URL
    assert dict(request.url.params) == {
        "lang": "ru-RU",
        "format": "oggopus",
        "topic": "general",
        "rawResults": "false",
    }
    assert request.headers["Authorization"] == f"Api-Key {API_KEY}"
    assert request.content == CLIP.content


# Примечание: folderId уходит, только если задан (ключ пользователя, а не сервисного аккаунта)
async def test_folderIdOnlyWhenSet() -> None:
    provider = ScriptedSpeechKit(answer(200, {"result": "да"}))

    await speechKit(provider, folderId="b1gfolder").recognize(CLIP)

    assert provider.requests[0].url.params["folderId"] == "b1gfolder"


# Примечание: пустой result — это «тишина», её превращает в no_speech сценарий, не адаптер
async def test_emptyResultIsReturned() -> None:
    provider = ScriptedSpeechKit(answer(200, {"result": ""}))

    assert (await speechKit(provider).recognize(CLIP)).text == ""


@pytest.mark.parametrize(
    "first",
    [
        answer(500, {"error_code": "INTERNAL"}),
        answer(503, {}),
        httpx.ConnectError("нет связи"),
        httpx.ReadTimeout("долго"),
    ],
    ids=["500", "503", "connect", "timeout"],
)
# Примечание: временный сбой — один повтор, второй ответ принят
async def test_transientFailureIsRetried(first: httpx.Response | Exception) -> None:
    provider = ScriptedSpeechKit(first, answer(200, {"result": "что у меня завтра"}))

    recognition = await speechKit(provider).recognize(CLIP)

    assert recognition.text == "Что у меня завтра"
    assert len(provider.requests) == 2


@pytest.mark.parametrize(
    ("answers", "failure"),
    [
        ((answer(500, {}), answer(502, {})), Failure.UPSTREAM_UNAVAILABLE),
        ((httpx.ConnectError("x"), httpx.ConnectError("x")), Failure.UPSTREAM_UNAVAILABLE),
        ((httpx.ReadTimeout("x"), httpx.ReadTimeout("x")), Failure.UPSTREAM_TIMEOUT),
        ((answer(500, {}), httpx.ReadTimeout("x")), Failure.UPSTREAM_TIMEOUT),
    ],
    ids=["5xx", "network", "timeouts", "5xx-then-timeout"],
)
# Примечание: повторы кончились — код последнего сбоя
async def test_retriesExhausted(
    answers: tuple[httpx.Response | Exception, ...], failure: Failure
) -> None:
    provider = ScriptedSpeechKit(*answers)

    with pytest.raises(TranscribeError) as caught:
        await speechKit(provider).recognize(CLIP)

    assert caught.value.failure is failure
    assert len(provider.requests) == 2


@pytest.mark.parametrize(
    ("status", "failure"),
    [
        (401, Failure.UPSTREAM_UNAVAILABLE),
        (403, Failure.UPSTREAM_UNAVAILABLE),
        (429, Failure.UPSTREAM_UNAVAILABLE),
        (400, Failure.UNSUPPORTED_FORMAT),
        (413, Failure.UNSUPPORTED_FORMAT),
    ],
)
# Примечание: ключ, квота и «не принял аудио» — без повторов (повтор не поможет и стоит денег)
async def test_finalFailuresAreNotRetried(status: int, failure: Failure) -> None:
    provider = ScriptedSpeechKit(answer(status, {"error_code": "X", "error_message": "секрет"}))

    with pytest.raises(TranscribeError) as caught:
        await speechKit(provider).recognize(CLIP)

    assert caught.value.failure is failure
    assert caught.value.details == {"provider_status": status}
    assert len(provider.requests) == 1


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, text="не json"),
        answer(200, {"text": "не то поле"}),
        answer(200, ["result"]),
        answer(200, {"result": 42}),
    ],
    ids=["text", "field", "list", "number"],
)
# Примечание: 200 не по формату — провайдер сломан, а не «тишина»
async def test_badSuccessBody(response: httpx.Response) -> None:
    with pytest.raises(TranscribeError) as caught:
        await speechKit(ScriptedSpeechKit(response)).recognize(CLIP)

    assert caught.value.failure is Failure.UPSTREAM_UNAVAILABLE


# Примечание: без повторов — один запрос даже при 5xx
async def test_zeroRetries() -> None:
    provider = ScriptedSpeechKit(answer(500, {}))

    with pytest.raises(TranscribeError):
        await speechKit(provider, retries=0).recognize(CLIP)

    assert len(provider.requests) == 1


# Примечание: общий бюджет: провайдер «висит» дольше — upstream_timeout, а не ожидание до бота
async def test_deadlineStopsRetries() -> None:
    # Примечание: каждая попытка ждёт дольше бюджета
    async def hang(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(5)
        return answer(200, {"result": "поздно"})

    http = httpx.AsyncClient(transport=httpx.MockTransport(hang))
    provider = YandexSpeechKit(
        http,
        SecretStr(API_KEY),
        url=YANDEX_STT_URL,
        attemptTimeoutS=10.0,
        retries=2,
        deadlineS=0.1,
    )

    with pytest.raises(TranscribeError) as caught:
        await provider.recognize(CLIP)

    assert caught.value.failure is Failure.UPSTREAM_TIMEOUT


# Примечание: ключ и текст ошибки SpeechKit в лог не попадают
async def test_logsHaveNoKeyAndNoText() -> None:
    provider = ScriptedSpeechKit(
        answer(401, {"error_code": "UNAUTHORIZED", "error_message": "секретное сообщение"}),
    )
    ok = ScriptedSpeechKit(answer(200, {"result": "личный текст пользователя"}))

    with structlog.testing.capture_logs() as logs:
        with pytest.raises(TranscribeError):
            await speechKit(provider).recognize(CLIP)
        await speechKit(ok).recognize(CLIP)

    dumped = json.dumps(logs, ensure_ascii=False)
    assert API_KEY not in dumped
    assert "секретное сообщение" not in dumped
    assert "личный текст" not in dumped
    assert [(log["event"], log.get("code")) for log in logs] == [
        ("asr_provider_auth_failed", "UNAUTHORIZED"),
        ("asr_provider_recognized", None),
    ]


# Примечание: имя провайдера для логов
def test_name() -> None:
    assert speechKit(ScriptedSpeechKit()).name == "yandex"
