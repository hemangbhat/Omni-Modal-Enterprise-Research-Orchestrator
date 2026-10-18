"""Hosted Whisper transcription (Groq); the HTTP call is mocked."""

from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

import _path  # noqa: F401

from omni_modal.ingestion.extractors import ExtractionError, LocalWhisperTranscriber
from omni_modal.ingestion.groq_transcriber import GroqWhisperTranscriber, select_audio_transcriber


class GroqTranscriberTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.audio = Path(self.tmp.name) / "call.mp3"
        self.audio.write_bytes(b"ID3fake-audio")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_segments_keep_their_time_range(self) -> None:
        payload = {"text": "Hello. Budget approved.", "segments": [
            {"start": 0.0, "end": 2.5, "text": " Hello. "},
            {"start": 65.2, "end": 70.0, "text": "Budget approved."},
        ]}
        with mock.patch("urllib.request.urlopen", return_value=io.BytesIO(json.dumps(payload).encode())) as urlopen:
            segments = GroqWhisperTranscriber(api_key="k").transcribe(self.audio)
        req = urlopen.call_args[0][0]
        self.assertIn(b'name="model"\r\n\r\nwhisper-large-v3-turbo', req.data)
        self.assertIn(b"ID3fake-audio", req.data)
        self.assertEqual([s.text for s in segments], ["Hello.", "Budget approved."])
        self.assertEqual((segments[1].reference.start_ms, segments[1].reference.end_ms), (65200, 70000))

    def test_refusal_is_a_transcription_failure_and_network_errors_are_retryable(self) -> None:
        refused = urllib.error.HTTPError("u", 400, "bad", {}, io.BytesIO(b"unsupported format"))
        with mock.patch("urllib.request.urlopen", side_effect=refused):
            with self.assertRaises(ExtractionError):
                GroqWhisperTranscriber(api_key="k").transcribe(self.audio)
        with mock.patch("urllib.request.urlopen", side_effect=urllib.error.URLError("down")):
            with self.assertRaises(ConnectionError):
                GroqWhisperTranscriber(api_key="k").transcribe(self.audio)
        for code in (429, 503):
            busy = urllib.error.HTTPError("u", code, "busy", {}, io.BytesIO(b"slow down"))
            with mock.patch("urllib.request.urlopen", side_effect=busy):
                with self.assertRaises(ConnectionError):
                    GroqWhisperTranscriber(api_key="k").transcribe(self.audio)

    def test_selection(self) -> None:
        with mock.patch.dict(os.environ, {"GROQ_API_KEY": "k", "WHISPER_MODEL_PATH": ""}):
            self.assertIsInstance(select_audio_transcriber(), GroqWhisperTranscriber)
        env = {k: v for k, v in os.environ.items() if k not in ("GROQ_API_KEY", "WHISPER_MODEL_PATH")}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertIsInstance(select_audio_transcriber(), LocalWhisperTranscriber)


if __name__ == "__main__":
    unittest.main()
