"""Audio transcription through Groq's hosted Whisper (OpenAI-compatible API).

The deployment has 512 MB of memory and no Whisper binary, so a local model
cannot run there. Groq hosts Whisper on its free tier and the project already
has a Groq key, so deployed audio goes there. Each returned segment keeps its
start and end time, so citations can point at a moment in the recording.

Only the audio file is sent, and only when ``GROQ_API_KEY`` is set.
"""

from __future__ import annotations

import json
import mimetypes
import os
import shutil
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from omni_modal.ingestion.extractors import ExtractionError, LocalWhisperTranscriber
from omni_modal.ingestion.models import ExtractedTextSegment, IngestionErrorCode, SourceReference
from omni_modal.ingestion.normalization import normalize_text

GROQ_TRANSCRIBE_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
# Groq's documented free-tier upload limit for audio.
GROQ_MAX_AUDIO_BYTES = 25 * 1024 * 1024


def _multipart(fields: dict[str, str], file_field: str, file_path: Path) -> tuple[bytes, str]:
    boundary = f"omero-{uuid.uuid4().hex}"
    parts: list[bytes] = []
    for name, value in fields.items():
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()
        )
    mime = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
    parts.append(
        f'--{boundary}\r\nContent-Disposition: form-data; name="{file_field}"; '
        f'filename="audio{file_path.suffix.lower()}"\r\nContent-Type: {mime}\r\n\r\n'.encode()
        + file_path.read_bytes()
        + b"\r\n"
    )
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


class GroqWhisperTranscriber:
    def __init__(self, *, api_key: str, model: str = "whisper-large-v3-turbo", timeout: float = 120.0) -> None:
        if not api_key:
            raise ValueError("GroqWhisperTranscriber requires an API key.")
        self._api_key = api_key
        self._model = model
        self._timeout = timeout

    def transcribe(self, file_path: Path) -> list[ExtractedTextSegment]:
        if file_path.stat().st_size > GROQ_MAX_AUDIO_BYTES:
            raise ExtractionError(
                IngestionErrorCode.TRANSCRIPTION_FAILED,
                f"Audio files over {GROQ_MAX_AUDIO_BYTES // (1024 * 1024)} MB cannot be transcribed yet.",
            )
        body, content_type = _multipart(
            {"model": self._model, "response_format": "verbose_json", "temperature": "0"}, "file", file_path,
        )
        request = urllib.request.Request(
            GROQ_TRANSCRIBE_URL, data=body, method="POST",
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": content_type,
                "User-Agent": "omero-api/1.0",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code == 429 or exc.code >= 500:
                # Rate limit or an outage on their side: temporary, so the job is retried.
                raise ConnectionError(f"Transcription service unavailable (HTTP {exc.code}).") from exc
            raise ExtractionError(
                IngestionErrorCode.TRANSCRIPTION_FAILED,
                "The transcription service could not read this audio file. Try converting it to MP3 or WAV.",
            ) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            # Network trouble is temporary: let the job be retried.
            raise ConnectionError(f"Transcription service unreachable: {exc}") from exc

        segments: list[ExtractedTextSegment] = []
        for index, seg in enumerate(payload.get("segments") or []):
            text = normalize_text(str(seg.get("text", "")))
            if not text:
                continue
            segments.append(ExtractedTextSegment(
                text=text,
                reference=SourceReference(
                    source_path=str(file_path), source_kind="audio", segment_index=index,
                    start_ms=int(float(seg.get("start", 0)) * 1000), end_ms=int(float(seg.get("end", 0)) * 1000),
                ),
                metadata={"transcriber": f"groq:{self._model}"},
            ))
        if not segments and normalize_text(str(payload.get("text", ""))):
            segments.append(ExtractedTextSegment(
                text=normalize_text(str(payload["text"])),
                reference=SourceReference(source_path=str(file_path), source_kind="audio", segment_index=0),
                metadata={"transcriber": f"groq:{self._model}"},
            ))
        return segments


def select_audio_transcriber():
    """Local Whisper when it is installed and configured, else Groq when a key is set,
    else the local transcriber (which explains clearly that Whisper is missing)."""
    model_path = os.environ.get("WHISPER_MODEL_PATH") or None
    if model_path and shutil.which("whisper"):
        return LocalWhisperTranscriber(model=model_path)
    groq_key = os.environ.get("GROQ_API_KEY", "").strip()
    if groq_key:
        return GroqWhisperTranscriber(
            api_key=groq_key, model=os.environ.get("GROQ_WHISPER_MODEL", "whisper-large-v3-turbo"),
        )
    return LocalWhisperTranscriber(model=model_path)
