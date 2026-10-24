"""AudioProbe для настоящих голосовых: разбор OGG/Opus по байтам (domain/ogg.py)."""

from asr.domain.ogg import AudioInfo, probeOggOpus


# Примечание: длительность и каналы настоящего OGG/Opus
class OggOpusProbe:
    # Примечание: не OGG/Opus или битый файл — TranscribeError(unsupported_format)
    def probe(self, content: bytes) -> AudioInfo:
        return probeOggOpus(content)
