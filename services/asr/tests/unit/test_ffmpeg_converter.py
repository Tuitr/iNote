"""FfmpegOggConverter на настоящем ffmpeg: MP3 → OGG/Opus моно; не аудио — unsupported_format.

Без ffmpeg в системе тесты пропускаются: в образе asr он есть (deploy/docker-compose.yml).
"""

import asyncio
import shutil

import pytest

from asr.domain.failures import Failure, TranscribeError
from asr.domain.ogg import probeOggOpus
from asr.infrastructure.ffmpeg_converter import FfmpegOggConverter

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="нет ffmpeg")


# Примечание: 1 с стерео-синуса в MP3 — как голосовое MAX
async def _mp3() -> bytes:
    process = await asyncio.create_subprocess_exec(
        *("ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "sine=duration=1"),
        *("-ac", "2", "-f", "mp3", "pipe:1"),
        stdout=asyncio.subprocess.PIPE,
    )
    output, _ = await process.communicate()
    return output


# Примечание: MP3 стерео → OGG/Opus моно той же длительности
async def test_mp3BecomesMonoOggOpus() -> None:
    converted = await FfmpegOggConverter().toOggOpus(await _mp3())

    info = probeOggOpus(converted)
    assert info.channels == 1
    assert 900 <= info.durationMs <= 1100


# Примечание: не аудио — unsupported_format
async def test_garbageIsUnsupported() -> None:
    with pytest.raises(TranscribeError) as caught:
        await FfmpegOggConverter().toOggOpus(b"not audio at all")

    assert caught.value.failure is Failure.UNSUPPORTED_FORMAT
