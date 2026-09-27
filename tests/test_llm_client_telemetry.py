from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

from llm_client import (
    LLMProvider,
    LMStudioProvider,
    GenerationOptions,
    _read_usage,
)
from diagnostics import PerformanceRecorder
from runtime_config import LLMRuntimeConfig


class _Recorder:
    def __init__(self):
        self.completed_llm_call = None

    def start_stage(self, *_args, **_kwargs):
        return "stage"

    def complete_stage(self, *_args, **_kwargs):
        pass

    def start_llm_call(self, **_kwargs):
        return "llm-call"

    def complete_llm_call(self, _event_id, **kwargs):
        self.completed_llm_call = kwargs

    def fail_stage(self, *_args, **_kwargs):
        pass

    def fail_llm_call(self, *_args, **_kwargs):
        pass


class _Clock:
    now = 0.0

    def read(self):
        return self.now


class _TimedStream:
    def __init__(self, clock, chunks):
        self.clock = clock
        self.chunks = chunks

    def __iter__(self):
        for advance, chunk in self.chunks:
            self.clock.now += advance
            yield chunk

    def close(self):
        pass


def _chunk(content=None, *, usage=None, has_choice=True):
    choices = (
        [SimpleNamespace(delta=SimpleNamespace(content=content, tool_calls=[]))]
        if has_choice
        else []
    )
    return SimpleNamespace(choices=choices, usage=usage)


def _provider(stream, recorder):
    provider = LMStudioProvider.__new__(LMStudioProvider)
    LLMProvider.__init__(provider, recorder)
    provider.model = "qwen/qwen3-8b"
    provider.runtime_config = LLMRuntimeConfig(64, 64, 30, False)
    provider.max_tokens = 64
    provider.thinking_supported = True
    provider.thinking_enabled = False
    provider.client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=Mock(return_value=stream))
        )
    )
    return provider


class LMStudioTelemetryTests(TestCase):
    def test_stream_usage_ttft_and_local_output_rate_are_recorded(self):
        clock = _Clock()
        recorder = _Recorder()
        usage = SimpleNamespace(prompt_tokens=21, completion_tokens=8, total_tokens=29)
        stream = _TimedStream(
            clock,
            [
                (3.0, _chunk("hello")),
                (2.0, _chunk(usage=usage, has_choice=False)),
            ],
        )
        provider = _provider(stream, recorder)

        with patch("llm_client.perf_counter", side_effect=clock.read), patch(
            "llm_client._print_lmstudio_request_diagnostics"
        ):
            response = provider.generate_response(
                [{"role": "user", "content": "hi"}],
                options=GenerationOptions(tool_calling_enabled=False, max_rounds=1),
            )

        self.assertEqual(response.content, "hello")
        call = recorder.completed_llm_call
        self.assertEqual(call["input_tokens"], 21)
        self.assertEqual(call["output_tokens"], 8)
        self.assertEqual(call["total_tokens"], 29)
        self.assertEqual(call["token_usage_source"], "provider_usage")
        self.assertEqual(call["time_to_first_token_seconds"], 3.0)
        self.assertEqual(call["stream_initial_wait_seconds"], 3.0)
        self.assertEqual(call["stream_generation_seconds"], 2.0)
        self.assertEqual(call["tokens_per_second"], 4.0)
        self.assertEqual(
            call["tokens_per_second_source"],
            "provider_usage_and_local_stream_timer",
        )
        self.assertIsNone(call.get("server_inference_seconds"))
        provider.client.chat.completions.create.assert_called_once()
        self.assertEqual(
            provider.client.chat.completions.create.call_args.kwargs[
                "stream_options"
            ],
            {"include_usage": True},
        )

    def test_stream_without_usage_keeps_response_and_marks_metrics_unavailable(self):
        clock = _Clock()
        recorder = _Recorder()
        stream = _TimedStream(clock, [(1.5, _chunk("answer"))])
        provider = _provider(stream, recorder)

        with patch("llm_client.perf_counter", side_effect=clock.read), patch(
            "llm_client._print_lmstudio_request_diagnostics"
        ):
            response = provider.generate_response(
                [{"role": "user", "content": "hi"}],
                options=GenerationOptions(tool_calling_enabled=False, max_rounds=1),
            )

        self.assertEqual(response.content, "answer")
        call = recorder.completed_llm_call
        self.assertIsNone(call["input_tokens"])
        self.assertIsNone(call["output_tokens"])
        self.assertIsNone(call["total_tokens"])
        self.assertIsNone(call["tokens_per_second"])
        self.assertIsNone(call["token_usage_source"])
        self.assertIsNone(call.get("server_inference_seconds"))
        self.assertEqual(call["time_to_first_token_seconds"], 1.5)

    def test_malformed_optional_usage_is_ignored_without_failing_response(self):
        self.assertEqual(
            _read_usage(
                {"prompt_tokens": "21", "completion_tokens": -2, "total_tokens": True},
                "prompt_tokens",
                "completion_tokens",
            ),
            (None, None, None),
        )
        clock = _Clock()
        recorder = _Recorder()
        malformed_usage = SimpleNamespace(
            prompt_tokens="21", completion_tokens=object(), total_tokens=-1
        )
        stream = _TimedStream(
            clock,
            [
                (0.5, _chunk("safe")),
                (0.5, _chunk(usage=malformed_usage, has_choice=False)),
            ],
        )
        provider = _provider(stream, recorder)
        with patch("llm_client.perf_counter", side_effect=clock.read), patch(
            "llm_client._print_lmstudio_request_diagnostics"
        ):
            response = provider.generate_response(
                [{"role": "user", "content": "hi"}],
                options=GenerationOptions(tool_calling_enabled=False, max_rounds=1),
            )
        self.assertEqual(response.content, "safe")
        self.assertIsNone(recorder.completed_llm_call["tokens_per_second"])

    def test_real_performance_recorder_accepts_additive_telemetry_fields(self):
        usage = SimpleNamespace(prompt_tokens=12, completion_tokens=3, total_tokens=15)
        stream = _TimedStream(
            _Clock(),
            [(0.001, _chunk("ok")), (0.001, _chunk(usage=usage, has_choice=False))],
        )
        recorder = PerformanceRecorder("telemetry", "test", "lmstudio", "qwen")
        provider = _provider(stream, recorder)
        with patch("llm_client._print_lmstudio_request_diagnostics"):
            provider.generate_response(
                [{"role": "user", "content": "hi"}],
                options=GenerationOptions(tool_calling_enabled=False, max_rounds=1),
            )
        call = next(
            event for event in recorder.snapshot().events if event.stage == "llm_call"
        )
        self.assertEqual(call.metadata["input_tokens"], 12)
        self.assertEqual(call.metadata["output_tokens"], 3)
        self.assertEqual(call.metadata["token_usage_source"], "provider_usage")
        self.assertIn("time_to_first_token_seconds", call.metadata)

    def test_optional_temperature_is_sent_only_when_requested(self):
        stream = _TimedStream(_Clock(), [(0.1, _chunk("ok"))])
        provider = _provider(stream, _Recorder())
        diagnostic = Mock()
        with patch("llm_client.perf_counter", return_value=0.0), patch(
            "llm_client._print_lmstudio_request_diagnostics", diagnostic
        ):
            provider.generate_response(
                [{"role": "user", "content": "hi"}],
                options=GenerationOptions(
                    tool_calling_enabled=False, max_rounds=1, temperature=0.0,
                ),
            )
        self.assertEqual(
            provider.client.chat.completions.create.call_args.kwargs["temperature"],
            0.0,
        )
        self.assertEqual(diagnostic.call_args.args[-1], 0.0)
