from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Iterable
import uuid


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(slots=True)
class Hypothesis:
    hypothesis_id: str
    title: str
    rationale: str
    expression: str
    market: str = ""
    tags: list[str] = field(default_factory=list)
    fields_used: list[str] = field(default_factory=list)
    template_id: str = ""
    setting_notes: str = ""
    economic_hypothesis: str = ""
    behavioral_mechanism: str = ""
    risk_hypothesis: str = ""
    failure_modes: str = ""
    created_at: str = field(default_factory=_utc_now_iso)


class HypothesisStore:
    """JSONL-backed hypothesis registry."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.write_text("", encoding="utf-8")

    def create(
        self,
        title: str,
        rationale: str,
        expression: str,
        market: str = "",
        tags: Iterable[str] | None = None,
        fields_used: Iterable[str] | None = None,
        template_id: str = "",
        setting_notes: str = "",
        economic_hypothesis: str = "",
        behavioral_mechanism: str = "",
        risk_hypothesis: str = "",
        failure_modes: str = "",
    ) -> Hypothesis:
        hypothesis = Hypothesis(
            hypothesis_id=uuid.uuid4().hex[:12],
            title=title.strip(),
            rationale=rationale.strip(),
            expression=expression.strip(),
            market=market.strip(),
            tags=[t.strip() for t in (tags or []) if t.strip()],
            fields_used=[f.strip() for f in (fields_used or []) if f.strip()],
            template_id=template_id.strip(),
            setting_notes=setting_notes.strip(),
            economic_hypothesis=economic_hypothesis.strip(),
            behavioral_mechanism=behavioral_mechanism.strip(),
            risk_hypothesis=risk_hypothesis.strip(),
            failure_modes=failure_modes.strip(),
        )
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(asdict(hypothesis), ensure_ascii=True) + "\n")
        return hypothesis

    def list(self) -> list[Hypothesis]:
        hypotheses: list[Hypothesis] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            payload = json.loads(line)
            payload.setdefault("fields_used", [])
            payload.setdefault("template_id", "")
            payload.setdefault("setting_notes", "")
            payload.setdefault("economic_hypothesis", "")
            payload.setdefault("behavioral_mechanism", "")
            payload.setdefault("risk_hypothesis", "")
            payload.setdefault("failure_modes", "")
            hypotheses.append(Hypothesis(**payload))
        hypotheses.sort(key=lambda h: h.created_at, reverse=True)
        return hypotheses

    def get(self, hypothesis_id: str) -> Hypothesis | None:
        for hypothesis in self.list():
            if hypothesis.hypothesis_id == hypothesis_id:
                return hypothesis
        return None

    def update(self, hypothesis_id: str, **updates: str) -> Hypothesis | None:
        if not updates:
            return self.get(hypothesis_id)

        payloads: list[dict[str, object]] = []
        target: dict[str, object] | None = None
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            payload = json.loads(line)
            payloads.append(payload)
            if payload.get("hypothesis_id") == hypothesis_id:
                target = payload

        if target is None:
            return None

        allowed = {
            "title",
            "rationale",
            "expression",
            "market",
            "template_id",
            "setting_notes",
            "economic_hypothesis",
            "behavioral_mechanism",
            "risk_hypothesis",
            "failure_modes",
        }
        for key, value in updates.items():
            if key not in allowed:
                continue
            target[key] = value.strip()

        with self.path.open("w", encoding="utf-8") as handle:
            for payload in payloads:
                handle.write(json.dumps(payload, ensure_ascii=True) + "\n")

        refreshed = self.get(hypothesis_id)
        return refreshed
