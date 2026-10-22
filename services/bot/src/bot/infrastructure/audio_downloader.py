"""Скачивание голосового из MAX сразу после апдейта: ссылка живёт около суток, лимит — по байтам."""

from typing import Final

import httpx

from bot.domain.updates import VoiceRef
from bot.domain.voice import AudioFile, VoiceFailureError, VoiceFailureReason
from common.logging import getLogger
from contracts.asr import DEFAULT_MIME

# Стандарт для MAX API: 10 с и до 3 попыток; 2 МБ — лимит синхронного распознавания SaluteSpeech
DEFAULT_TIMEOUT_S: Final = 10.0
DEFAULT_MAX_BYTES: Final = 2 * 1024 * 1024
ATTEMPTS: Final = 3
_RETRYABLE_STATUS: Final = 500

_log = getLogger(__name__)


# Примечание: скачивает файл потоком и обрывает на лимите — большое голосовое не держим в памяти
class HttpAudioDownloader:
    # Примечание: http — клиент без сервисного токена (ссылка внешняя), создан в lifespan;
    # httpsOnly — настоящий MAX: ссылка и все редиректы только https (не ходим во внутреннюю сеть)
    def __init__(
        self,
        http: httpx.AsyncClient,
        *,
        maxBytes: int = DEFAULT_MAX_BYTES,
        httpsOnly: bool = False,
    ) -> None:
        self._http = http
        self._maxBytes = maxBytes
        if httpsOnly:
            http.event_hooks.setdefault("request", []).append(_requireHttps)

    # Примечание: превышение лимита — как audio_too_long; сеть и 5xx — повтор, потом «недоступно»
    async def download(self, voice: VoiceRef) -> AudioFile:
        for attempt in range(1, ATTEMPTS + 1):
            try:
                return await self._fetch(voice.url)
            except _InsecureUrlError:
                _log.warning("voice_url_rejected", reason="not_https")
                raise VoiceFailureError(
                    VoiceFailureReason.UNAVAILABLE, "voice_url_rejected"
                ) from None
            except (httpx.TransportError, _RetryableDownloadError) as exc:
                _log.warning("voice_download_retry", attempt=attempt, error=type(exc).__name__)
        raise VoiceFailureError(VoiceFailureReason.UNAVAILABLE, "voice_download_failed")

    # Примечание: одна попытка: статус, Content-Length и подсчёт байт по ходу чтения
    async def _fetch(self, url: str) -> AudioFile:
        async with self._http.stream("GET", url) as response:
            if response.status_code >= _RETRYABLE_STATUS:
                raise _RetryableDownloadError(response.status_code)
            if not response.is_success:
                _log.warning("voice_download_rejected", status=response.status_code)
                raise VoiceFailureError(VoiceFailureReason.UNAVAILABLE, "voice_download_failed")
            declared = response.headers.get("content-length")
            if declared is not None and declared.isdigit() and int(declared) > self._maxBytes:
                raise self._tooLarge(int(declared))
            chunks: list[bytes] = []
            size = 0
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > self._maxBytes:
                    raise self._tooLarge(size)
                chunks.append(chunk)
            mime = response.headers.get("content-type", DEFAULT_MIME).split(";", 1)[0].strip()
        _log.info("voice_downloaded", bytes=size)
        return AudioFile(content=b"".join(chunks), mime=mime or DEFAULT_MIME)

    # Примечание: голосовое больше лимита — тот же ответ пользователю, что на audio_too_long
    def _tooLarge(self, size: int) -> VoiceFailureError:
        _log.info("voice_too_large", bytes=size, limit=self._maxBytes)
        return VoiceFailureError(VoiceFailureReason.TOO_LONG, "voice_too_large")


# Примечание: ссылка или редирект не по https — не идём (в режиме настоящего MAX)
class _InsecureUrlError(httpx.RequestError):
    pass


# Примечание: хук клиента на каждый запрос, включая редиректы
async def _requireHttps(request: httpx.Request) -> None:
    if request.url.scheme != "https":
        raise _InsecureUrlError("ссылка на голосовое не https", request=request)


# Примечание: 5xx от хранилища MAX — временная беда, стоит повторить
class _RetryableDownloadError(Exception):
    # Примечание: статус ответа — в лог
    def __init__(self, status: int) -> None:
        super().__init__(f"HTTP {status}")
