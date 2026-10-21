from common.errors import ErrorEnvelope
from contracts.asr import NO_SPEECH, TranscribeResponse
from libs.contracts.tests.examples import loadExample


# Примечание: пример ответа J1 разбирается и сериализуется обратно без потерь
def test_responseRoundTrip() -> None:
    raw = loadExample("asr/transcribe_response.json")
    response = TranscribeResponse.fromWire(raw)
    assert response.toWire() == raw
    assert response.lowConfidence is False


# Примечание: ошибка J1 — стандартный конверт, код no_speech
def test_noSpeechIsStandardEnvelope() -> None:
    assert ErrorEnvelope.fromWire(loadExample("asr/no_speech.json")).error.code == NO_SPEECH


# Примечание: незнакомые поля A игнорируются (правило 8), low_confidence по умолчанию false
def test_tolerantReader() -> None:
    response = TranscribeResponse.fromWire(
        {"text": "да", "raw_text": "да", "confidence": 0.5, "duration_ms": 300, "lang": "ru-RU"}
    )
    assert response.lowConfidence is False
