"""AudioConverter на ffmpeg: голосовые MAX приходят в MP3 (audio/mpeg), SpeechKit v1 ждёт OGG/Opus.

ffmpeg читает файл из stdin и пишет OGG/Opus моно в stdout — без временных файлов на диске.
"""

import asyncio
from typing import Final

from asr.domain.failures import Failure, TranscribeError
from common.logging import getLogger

# Голосовое до 30 с перекодируется за доли секунды; больше — ffmpeg завис
DEFAULT_TIMEOUT_S: Final = 10.0
FFMPEG_ARGS: Final = (
    "ffmpeg",
    "-hide_banner",
    "-loglevel",
    "error",
    "-i",
    "pipe:0",
    "-vn",
    "-ac",
    "1",
    "-c:a",
    "libopus",
    "-b:a",
    "32k",
    "-f",
    "ogg",
    "pipe:1",
)
# stderr ffmpeg — в лог не целиком: хватит начала, чтобы понять причину
STDERR_LOG_CHARS: Final = 300

_log = getLogger(__name__)


# Примечание: любое аудио → OGG/Opus моно; не аудио или сбой ffmpeg — unsupported_format
class FfmpegOggConverter:
    def __init__(self, timeoutS: float = DEFAULT_TIMEOUT_S) -> None:
        self._timeoutS = timeoutS

    # Примечание: один запуск ffmpeg на голосовое; по таймауту процесс убивается
    async def toOggOpus(self, content: bytes) -> bytes:
        process = await asyncio.create_subprocess_exec(
            *FFMPEG_ARGS,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            async with asyncio.timeout(self._timeoutS):
                output, errors = await process.communicate(content)
        except TimeoutError as exc:
            process.kill()
            await process.wait()
            raise TranscribeError(Failure.UNSUPPORTED_FORMAT, "ffmpeg не уложился") from exc
        if process.returncode != 0 or not output:
            _log.info(
                "audio_convert_failed",
                returncode=process.returncode,
                stderr=errors.decode(errors="replace")[:STDERR_LOG_CHARS],
            )
            raise TranscribeError(Failure.UNSUPPORTED_FORMAT, "не удалось перекодировать аудио")
        return output
