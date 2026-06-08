from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Protocol

from omni_modal.ingestion.models import (
    ExtractedTextSegment,
    IngestionErrorCode,
    SourceReference,
)
from omni_modal.ingestion.normalization import normalize_text
from omni_modal.observability import observability


class ExtractionError(RuntimeError):
    def __init__(self, code: IngestionErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code


class PdfExtractor(Protocol):
    def extract(self, file_path: Path) -> list[ExtractedTextSegment]:
        raise NotImplementedError


class AudioTranscriber(Protocol):
    def transcribe(self, file_path: Path) -> list[ExtractedTextSegment]:
        raise NotImplementedError


class LocalPdfTextExtractor:
    def extract(self, file_path: Path) -> list[ExtractedTextSegment]:
        try:
            from pypdf import PdfReader  # type: ignore[import-not-found]
        except ImportError:
            try:
                from PyPDF2 import PdfReader  # type: ignore[import-not-found]
            except ImportError as exc:
                raise ExtractionError(
                    IngestionErrorCode.EXTRACTION_FAILED,
                    "PDF extraction requires local pypdf or PyPDF2 installation.",
                ) from exc

        try:
            reader = PdfReader(str(file_path))
            segments: list[ExtractedTextSegment] = []
            for page_index, page in enumerate(reader.pages, start=1):
                text = normalize_text(page.extract_text() or "")
                if not text:
                    continue
                segments.append(
                    ExtractedTextSegment(
                        text=text,
                        reference=SourceReference(
                            source_path=str(file_path),
                            source_kind="pdf",
                            page_number=page_index,
                        ),
                        metadata={"page_number": page_index},
                    )
                )
            return segments
        except ExtractionError:
            raise
        except Exception as exc:
            raise ExtractionError(
                IngestionErrorCode.EXTRACTION_FAILED,
                f"PDF text extraction failed: {exc}",
            ) from exc


class LocalWhisperTranscriber:
    def __init__(
        self,
        whisper_binary: str = "whisper",
        model: str | None = None,
        language: str | None = None,
    ) -> None:
        self._whisper_binary = whisper_binary
        self._model = model
        self._language = language

    def transcribe(self, file_path: Path) -> list[ExtractedTextSegment]:
        # Phase breadcrumb: model_load
        try:
            observability.add_breadcrumb(
                message="Transcription phase started: model_load",
                category="transcription",
                level="info",
                data={"phase": "model_load", "timestamp": time.monotonic()},
            )
        except Exception:
            pass

        binary = shutil.which(self._whisper_binary)
        if not binary:
            try:
                import psutil  # type: ignore[import-not-found]
                available_memory_bytes = psutil.virtual_memory().available
            except ImportError:
                available_memory_bytes = None

            exc = ExtractionError(
                IngestionErrorCode.TRANSCRIPTION_FAILED,
                "Local Whisper CLI is not installed or not on PATH.",
            )
            try:
                observability.capture_exception(
                    exc,
                    operation="transcription.model_load",
                    context={
                        "model_name": self._model or "default",
                        "available_memory_bytes": available_memory_bytes,
                    },
                )
            except Exception:
                pass  # Req 4.5: if Sentry fails, log and continue
            raise exc

        with tempfile.TemporaryDirectory(prefix="omni-whisper-") as output_dir:
            command = [
                binary,
                str(file_path),
                "--output_format",
                "json",
                "--output_dir",
                output_dir,
            ]
            if self._model:
                command.extend(["--model", self._model])
            if self._language:
                command.extend(["--language", self._language])

            # Phase breadcrumb: audio_decode
            try:
                observability.add_breadcrumb(
                    message="Transcription phase started: audio_decode",
                    category="transcription",
                    level="info",
                    data={"phase": "audio_decode", "timestamp": time.monotonic()},
                )
            except Exception:
                pass

            transcription_start = time.monotonic()
            completed = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=60 * 60,
            )
            transcription_elapsed = time.monotonic() - transcription_start

            if completed.returncode != 0:
                audio_exc = ExtractionError(
                    IngestionErrorCode.TRANSCRIPTION_FAILED,
                    "Local Whisper transcription failed.",
                )
                try:
                    observability.capture_exception(
                        audio_exc,
                        operation="transcription.audio_decode",
                        context={
                            "file_extension": file_path.suffix.lower(),
                            "file_size_bytes": file_path.stat().st_size if file_path.exists() else 0,
                            "audio_duration": "unknown",
                        },
                    )
                except Exception:
                    pass  # Req 4.5: if Sentry fails, continue
                raise audio_exc

            # Warn if processing exceeded the default timeout threshold (Req 4.3)
            _TRANSCRIPTION_TIMEOUT_DEFAULT = 300  # 5 minutes
            if transcription_elapsed > _TRANSCRIPTION_TIMEOUT_DEFAULT:
                try:
                    observability.capture_message(
                        "Transcription processing time exceeded configured timeout",
                        operation="transcription.timeout",
                        context={
                            "elapsed_seconds": transcription_elapsed,
                            "audio_duration": "unknown",
                        },
                        level="warning",
                    )
                except Exception:
                    pass

            # Phase breadcrumb: transcription (after subprocess succeeds, before reading JSON)
            try:
                observability.add_breadcrumb(
                    message="Transcription phase started: transcription",
                    category="transcription",
                    level="info",
                    data={"phase": "transcription", "timestamp": time.monotonic()},
                )
            except Exception:
                pass

            output_path = Path(output_dir) / f"{file_path.stem}.json"
            if not output_path.exists():
                raise ExtractionError(
                    IngestionErrorCode.TRANSCRIPTION_FAILED,
                    "Local Whisper did not produce a JSON transcript.",
                )

            payload = json.loads(output_path.read_text(encoding="utf-8"))

        segments_payload = payload.get("segments") or []
        segments: list[ExtractedTextSegment] = []
        if segments_payload:
            for index, segment in enumerate(segments_payload):
                text = normalize_text(str(segment.get("text", "")))
                if not text:
                    continue
                start_seconds = float(segment.get("start", 0))
                end_seconds = float(segment.get("end", 0))
                segments.append(
                    ExtractedTextSegment(
                        text=text,
                        reference=SourceReference(
                            source_path=str(file_path),
                            source_kind="audio",
                            segment_index=index,
                            start_ms=int(start_seconds * 1000),
                            end_ms=int(end_seconds * 1000),
                        ),
                        metadata={"segment_index": index},
                    )
                )
            return segments

        text = normalize_text(str(payload.get("text", "")))
        if text:
            return [
                ExtractedTextSegment(
                    text=text,
                    reference=SourceReference(
                        source_path=str(file_path),
                        source_kind="audio",
                        segment_index=0,
                    ),
                    metadata={"segment_index": 0},
                )
            ]

        return []
