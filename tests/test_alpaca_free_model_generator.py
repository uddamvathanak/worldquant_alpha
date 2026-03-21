from __future__ import annotations

from pathlib import Path
import sys

import pytest


ALPACA_DIR = Path(__file__).resolve().parents[1] / "paper" / "alpaca"
if str(ALPACA_DIR) not in sys.path:
    sys.path.insert(0, str(ALPACA_DIR))

from free_model_generator import (  # type: ignore  # noqa: E402
    FreeModelGeneratorError,
    generate_mutation_candidates,
    generator_enabled,
)


class _FakeResponse:
    def __init__(self, payload: str) -> None:
        self._payload = payload.encode("utf-8")

    def read(self) -> bytes:
        return self._payload

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None


def test_generator_enabled_requires_all_env_values() -> None:
    assert generator_enabled({}) is False
    assert (
        generator_enabled(
            {
                "ALPHA_GEN_API_BASE": "https://example.test/v1",
                "ALPHA_GEN_API_KEY": "key",
                "ALPHA_GEN_MODEL": "model",
            }
        )
        is True
    )


def test_generate_mutation_candidates_rejects_out_of_bounds_model_output(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_urlopen(request, timeout=0):  # type: ignore[no-untyped-def]
        return _FakeResponse(
            """
            {"choices":[{"message":{"content":"[{\\\"template_name\\\":\\\"smooth_momentum\\\",\\\"family\\\":\\\"momentum\\\",\\\"params\\\":{\\\"window\\\":999},\\\"group_level\\\":\\\"sector\\\",\\\"book_mode\\\":\\\"sector\\\",\\\"top_n\\\":50,\\\"signal_decay\\\":3,\\\"score_truncation\\\":0.05,\\\"source\\\":\\\"mutation\\\",\\\"parent_candidates\\\":[\\\"seed_a\\\"],\\\"notes\\\":\\\"bad\\\"}]"} }]}
            """.strip()
        )

    monkeypatch.setattr("free_model_generator.request.urlopen", fake_urlopen)

    with pytest.raises(FreeModelGeneratorError):
        generate_mutation_candidates(
            seed_candidates=[],
            family_context={"momentum": ["smooth_momentum"]},
            env={
                "ALPHA_GEN_API_BASE": "https://example.test/v1",
                "ALPHA_GEN_API_KEY": "key",
                "ALPHA_GEN_MODEL": "model",
            },
        )

