"""Скачивание голосового: лимит размера, повторы на сеть и 5xx, без повторов на 4xx."""

from collections.abc import AsyncIterator
from typing import Final, override

import httpx
import pytest
import respx

from bot.domain.updates import VoiceRef
from bot.domain.voice import AudioFile, VoiceFailureError, VoiceFailureReason
from bot.infrastructure.audio_downloader import HttpAudioDownloader

URL: Final = "https://files.max.invalid/voice-1.ogg"
VOICE: Final = VoiceRef(url=URL, token="t")
LIMIT: Final = 1000


# Примечание: скачать загрузчиком с маленьким лимитом; клиент закрывается сразу
async def download() -> AudioFile:
    async with httpx.AsyncClient(timeout=1) as http:
        return await HttpAudioDownloader(http, maxBytes=LIMIT).download(VOICE)


# Примечание: тело частями и без Content-Length — лимит считается по ходу чтения
async def chunks(total: int) -> AsyncIterator[bytes]:
    for _ in range(total // 100):
        yield b"x" * 100


# Примечание: успех: байты и mime из ответа (без параметров)
@respx.mock
async def test_download() -> None:
    respx.get(URL).mock(
        return_value=httpx.Response(
            200, content=b"OggS" + b"a" * 10, headers={"content-type": "audio/ogg; codecs=opus"}
        )
    )
    audio = await download()
    assert audio.content.startswith(b"OggS")
    assert audio.mime == "audio/ogg"


# Примечание: больше лимита — «слишком длинное»: по Content-Length сразу и по факту при потоке
@pytest.mark.parametrize("declared", [True, False], ids=["content_length", "streamed"])
@respx.mock
async def test_tooLarge(declared: bool) -> None:
    if declared:
        response = httpx.Response(200, content=b"x" * (LIMIT + 1))
    else:
        response = httpx.Response(200, stream=_Stream())
    route = respx.get(URL).mock(return_value=response)
    with pytest.raises(VoiceFailureError) as caught:
        await download()
    assert caught.value.reason is VoiceFailureReason.TOO_LONG
    assert route.call_count == 1


# Примечание: таймаут и 5xx — 3 попытки, затем «недоступно»
@pytest.mark.parametrize(
    "respond", [httpx.ReadTimeout("slow"), httpx.Response(502)], ids=["timeout", "502"]
)
@respx.mock
async def test_retries(respond: httpx.Response | Exception) -> None:
    route = respx.get(URL).mock(side_effect=[respond, respond, respond])
    with pytest.raises(VoiceFailureError) as caught:
        await download()
    assert caught.value.reason is VoiceFailureReason.UNAVAILABLE
    assert route.call_count == 3


# Примечание: 404 (ссылка протухла) — без повторов
@respx.mock
async def test_notFoundNoRetry() -> None:
    route = respx.get(URL).mock(return_value=httpx.Response(404))
    with pytest.raises(VoiceFailureError):
        await download()
    assert route.call_count == 1


# Примечание: поток без Content-Length длиннее лимита
class _Stream(httpx.AsyncByteStream):
    # Примечание: отдаёт 1100 байт кусками по 100
    @override
    async def __aiter__(self) -> AsyncIterator[bytes]:
        async for chunk in chunks(LIMIT + 100):
            yield chunk


# Примечание: настоящий MAX — только https: http-ссылка и редирект на http не скачиваются
@respx.mock
async def test_httpsOnly() -> None:
    plain = respx.get("http://internal:8000/x").mock(return_value=httpx.Response(200))
    respx.get("https://cdn.example/voice").mock(
        return_value=httpx.Response(302, headers={"location": "http://internal:8000/x"})
    )
    async with httpx.AsyncClient(follow_redirects=True) as http:
        downloader = HttpAudioDownloader(http, httpsOnly=True)
        for url in ("http://internal:8000/x", "https://cdn.example/voice"):
            with pytest.raises(VoiceFailureError) as failure:
                await downloader.download(VoiceRef(url=url, token="t"))
            assert failure.value.reason is VoiceFailureReason.UNAVAILABLE
    assert plain.call_count == 0
    respx.get(URL).mock(return_value=httpx.Response(200, content=b"OggS"))
    async with httpx.AsyncClient() as http:
        audio = await HttpAudioDownloader(http, httpsOnly=True).download(VOICE)
    assert audio.content == b"OggS"
