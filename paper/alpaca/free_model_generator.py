from __future__ import annotations

from dataclasses import dataclass
import json
import os
from typing import Any
from urllib import error, request

from alpha_dsl import AlphaDslCandidate, AlphaDslError, validate_candidate_batch


DEFAULT_MAX_MUTATIONS = 20


class FreeModelGeneratorError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class GeneratorConfig:
    api_base: str
    api_key: str
    model: str


def load_generator_config(env: dict[str, str] | None = None) -> GeneratorConfig | None:
    source = env or os.environ
    api_base = str(source.get("ALPHA_GEN_API_BASE", "")).strip()
    api_key = str(source.get("ALPHA_GEN_API_KEY", "")).strip()
    model = str(source.get("ALPHA_GEN_MODEL", "")).strip()
    if not api_base or not api_key or not model:
        return None
    return GeneratorConfig(
        api_base=api_base.rstrip("/"),
        api_key=api_key,
        model=model,
    )


def generator_enabled(env: dict[str, str] | None = None) -> bool:
    return load_generator_config(env) is not None


def _strip_code_fences(text: str) -> str:
    body = str(text or "").strip()
    if body.startswith("```"):
        lines = body.splitlines()
        if len(lines) >= 2:
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        body = "\n".join(lines).strip()
    return body


def _extract_message_content(payload: dict[str, Any]) -> str:
    choices = payload.get("choices", [])
    if not choices:
        raise FreeModelGeneratorError("Generator response had no choices.")
    message = choices[0].get("message", {})
    content = message.get("content", "")
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text", "")))
        content = "\n".join(parts)
    if not isinstance(content, str):
        raise FreeModelGeneratorError("Generator response content was not text.")
    return _strip_code_fences(content)


def _build_system_prompt(max_candidates: int) -> str:
    return (
        "You generate trading alpha mutation candidates as strict JSON only. "
        "Return a JSON array with at most "
        f"{int(max_candidates)} objects. "
        "Stay within the provided registry templates and allowed discrete parameter values. "
        "Do not invent new operators, new families, or free-form expressions. "
        "Each object must include template_name, family, params, group_level, book_mode, top_n, "
        "signal_decay, score_truncation, source, parent_candidates, notes."
    )


def _build_user_prompt(
    *,
    seed_candidates: list[dict[str, Any]],
    family_context: dict[str, list[str]],
    max_candidates: int,
) -> str:
    return json.dumps(
        {
            "task": "mutate_survivor_candidates",
            "max_candidates": int(max_candidates),
            "rules": {
                "allowed_group_level": ["market", "sector", "industry"],
                "allowed_book_mode": ["sector", "none"],
                "allowed_top_n": [30, 50, 75, 100],
                "allowed_signal_decay": [0, 3, 5, 10],
                "allowed_score_truncation": [None, 0.05, 0.10],
                "registry_only": True,
                "strict_json_only": True,
            },
            "family_context": family_context,
            "seed_candidates": seed_candidates,
        },
        indent=2,
    )


def generate_mutation_candidates(
    *,
    seed_candidates: list[dict[str, Any]],
    family_context: dict[str, list[str]],
    max_candidates: int = DEFAULT_MAX_MUTATIONS,
    timeout_seconds: int = 60,
    env: dict[str, str] | None = None,
) -> tuple[list[AlphaDslCandidate], str]:
    config = load_generator_config(env)
    if config is None:
        raise FreeModelGeneratorError("generator_disabled")

    body = {
        "model": config.model,
        "temperature": 0.2,
        "messages": [
            {"role": "system", "content": _build_system_prompt(max_candidates)},
            {
                "role": "user",
                "content": _build_user_prompt(
                    seed_candidates=seed_candidates,
                    family_context=family_context,
                    max_candidates=max_candidates,
                ),
            },
        ],
    }
    endpoint = f"{config.api_base}/chat/completions"
    req = request.Request(
        endpoint,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {config.api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with request.urlopen(req, timeout=timeout_seconds) as response:
            raw_text = response.read().decode("utf-8")
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise FreeModelGeneratorError(f"Generator HTTP error {exc.code}: {detail}") from exc
    except error.URLError as exc:
        raise FreeModelGeneratorError(f"Generator connection error: {exc}") from exc

    try:
        payload = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise FreeModelGeneratorError("Generator did not return valid JSON.") from exc

    content = _extract_message_content(payload)
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        raise FreeModelGeneratorError("Generator message content was not valid JSON.") from exc

    try:
        candidates = validate_candidate_batch(parsed, max_candidates=max_candidates)
    except AlphaDslError as exc:
        raise FreeModelGeneratorError(str(exc)) from exc
    return candidates, content

