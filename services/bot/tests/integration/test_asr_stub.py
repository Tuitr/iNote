"""HttpAsrGateway по HTTP против временной ASR-заглушки (deploy/stub/asr): все спецслучаи."""

import json
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Final

import httpx
import pytest
from pydantic import SecretStr

from bot.domain.voice import AudioFile, Transcript, VoiceFailureError, VoiceFailureReason
from bot.infrastructure.http_asr import HttpAsrGateway
from common.http import RetryPolicy, ServiceHttpClient
from common.trace import traceContext

pytestmark = pytest.mark.integration

REPO_ROOT: Final = Path(__file__).resolve().parents[4]
STUB: Final = REPO_ROOT / "deploy" / "stub" / "asr" / "server.py"
TOKEN: Final = "t" * 32
TRACE: Final = "ab12f0c4d5e6f708192a3b4c5d6e7f80"


# Примечание: свободный порт на loopback
def freePort() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]
        return port


# Примечание: заглушка в подпроцессе; stdout — в файл, чтобы проверить логи
@pytest.fixture(scope="module")
def stub(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[str, Path]]:
    port = freePort()
    logPath = tmp_path_factory.mktemp("asr") / "stub.log"
    with logPath.open("ab") as log:
        process = subprocess.Popen(  # noqa: S603 — свой скрипт из репо
            [sys.executable, str(STUB)],
            env={"STUB_PORT": str(port), "SERVICE_TOKEN": TOKEN, "PATH": ""},
            stdout=log,
            stderr=subprocess.STDOUT,
        )
    base = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        try:
            if httpx.get(f"{base}/health/live").status_code == 200:
                break
        except httpx.TransportError:
            time.sleep(0.1)
    try:
        yield base, logPath
    finally:
        process.terminate()
        process.wait(timeout=10)


# Примечание: распознать через клиент бота; token=None — проверить 401; клиент закрывается сразу
async def transcribe(base: str, audio: AudioFile, token: str | None = TOKEN) -> Transcript:
    async with ServiceHttpClient(
        baseUrl=base,
        timeoutS=15,
        serviceToken=SecretStr(token) if token else None,
        retry=RetryPolicy(retries=2, initialDelayS=0, maxDelayS=0),
        upstreamName="asr",
    ) as http:
        return await HttpAsrGateway(http).transcribe(audio)


# Примечание: фейковое аудио: OggS + текст
def fakeVoice(text: str) -> AudioFile:
    return AudioFile(content=b"OggS" + text.encode(), mime="audio/ogg")


# Примечание: обычное голосовое — текст как есть, raw_text без пунктуации, высокая уверенность
async def test_recognized(stub: tuple[str, Path]) -> None:
    base, logPath = stub
    with traceContext(TRACE):
        transcript = await transcribe(base, fakeVoice("Напомни завтра в 10, позвонить Ивану!"))
    assert transcript.text == "Напомни завтра в 10, позвонить Ивану!"
    assert transcript.rawText == "напомни завтра в 10 позвонить ивану"
    assert (transcript.lowConfidence, transcript.confidence) == (False, 0.95)
    assert transcript.durationMs > 0
    records = [
        json.loads(line) for line in logPath.read_text().splitlines() if line.startswith("{")
    ]
    [done] = [r for r in records if r["event"] == "transcribed" and r["trace_id"] == TRACE]
    assert "Ивану" not in json.dumps(done, ensure_ascii=False)


# Примечание: ~low~ — low_confidence, текст без префикса
async def test_lowConfidence(stub: tuple[str, Path]) -> None:
    transcript = await transcribe(stub[0], fakeVoice("~low~встреча в пятницу"))
    assert (transcript.text, transcript.lowConfidence) == ("встреча в пятницу", True)


# Примечание: спецслучаи заглушки → причины ответа бота
@pytest.mark.parametrize(
    ("audio", "reason"),
    [
        (AudioFile(content=b"RIFF....WAVE", mime="audio/wav"), VoiceFailureReason.UNSUPPORTED),
        (fakeVoice(""), VoiceFailureReason.NO_SPEECH),
        (fakeVoice("   "), VoiceFailureReason.NO_SPEECH),
        (fakeVoice("~long~очень долго"), VoiceFailureReason.TOO_LONG),
        (fakeVoice("~down~"), VoiceFailureReason.UNAVAILABLE),
    ],
    ids=["not_ogg", "empty", "spaces", "long", "down"],
)
async def test_specialCases(
    stub: tuple[str, Path], audio: AudioFile, reason: VoiceFailureReason
) -> None:
    with pytest.raises(VoiceFailureError) as caught:
        await transcribe(stub[0], audio)
    assert caught.value.reason is reason


# Примечание: без сервисного токена — unauthorized, а для бота — «не получилось распознать»
async def test_requiresToken(stub: tuple[str, Path]) -> None:
    with pytest.raises(VoiceFailureError) as caught:
        await transcribe(stub[0], fakeVoice("привет"), token=None)
    assert (caught.value.reason, caught.value.code) == (
        VoiceFailureReason.UNAVAILABLE,
        "unauthorized",
    )
