"""A transport smoke must reject false-positive and truncated responses."""

import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

PATH = Path(__file__).resolve().parents[2] / "test/smoke/riscv64-standalone.py"


class NativeSmokeResponseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("native_smoke", PATH)
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)

    def test_buffered_requires_selected_model_and_content(self):
        body = {"model": "code-model", "choices": [{"message": {"content": "ok"}}]}
        self.assertEqual(
            self.module.check_response(json.dumps(body), False, "code-model"), "ok"
        )
        with self.assertRaises(ValueError):
            self.module.check_response(json.dumps(body), False, "general-model")

    def test_stream_without_done_is_rejected(self):
        raw = 'data: {"model":"code-model","choices":[{"delta":{"content":"ok"}}]}\n\n'
        with self.assertRaisesRegex(ValueError, "DONE"):
            self.module.check_response(raw, True, "code-model")
        self.assertEqual(
            self.module.check_response(raw + "data: [DONE]\n\n", True, "code-model"),
            "ok",
        )

    def test_done_without_content_is_rejected(self):
        with self.assertRaises(ValueError):
            self.module.check_response("data: [DONE]\n\n", True, "code-model")

    def test_buffered_empty_choices_is_rejected(self):
        with self.assertRaises(ValueError):
            self.module.check_response(
                '{"model":"code-model","choices":[]}', False, "code-model"
            )

    def test_stream_requires_event_delimiters(self):
        raw = 'data: {"model":"code-model","choices":[{"delta":{"content":"ok"}}]}\ndata: [DONE]\n'
        with self.assertRaisesRegex(ValueError, "event"):
            self.module.check_response(raw, True, "code-model")

    def test_wrong_media_type_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "media type"):
            self.module.check_media_type("application/json", True)
        self.module.check_media_type("text/event-stream; charset=utf-8", True)

    def test_resume_uses_persisted_config_not_original_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original, active = root / "source.json", root / "active.yaml"
            original.write_text("source")
            with self.assertRaises(FileNotFoundError):
                self.module.resume_command(
                    ["cli", "serve", "--config", str(original)], original, active
                )
            active.write_text("saved runtime configuration")
            command = self.module.resume_command(
                ["cli", "serve", "--config", str(original)], original, active
            )
            self.assertFalse(original.exists())
            self.assertEqual(command, ["cli", "serve", "--config", str(active)])

    def test_cleanup_timeout_does_not_skip_remaining_actions_or_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            called = []

            def timeout():
                raise subprocess.TimeoutExpired("cli stop", 1)

            report = Path(directory) / "result.json"
            receipt = {"passed": True}
            self.module.finish_cleanup(
                [("stop", timeout), ("provider", lambda: called.append("provider"))],
                receipt,
                report,
            )
            self.assertEqual(called, ["provider"])
            saved = json.loads(report.read_text())
            self.assertFalse(saved["passed"])
            self.assertTrue(saved["cleanup_errors"])


if __name__ == "__main__":
    unittest.main()
