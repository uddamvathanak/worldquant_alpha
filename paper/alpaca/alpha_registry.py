from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
from pathlib import Path
from typing import Any


DEFAULT_ALPHA_SET = "wave1"
MODEL_RESEARCH_SELECTED = "research_selected"


@dataclass(frozen=True, slots=True)
class AlphaDefinition:
    name: str
    family: str
    formula_fn: str
    default_params: dict[str, Any]
    parameter_grid: dict[str, list[Any]]
    supports_group_level: bool = True


@dataclass(slots=True)
class StrategyMember:
    name: str
    alpha_name: str
    family: str
    weight: float
    params: dict[str, Any] = field(default_factory=dict)
    group_level: str = "sector"
    book_mode: str = "sector"
    top_n: int = 30
    signal_decay: int = 0
    score_truncation: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class StrategySpec:
    strategy_type: str
    feed: str
    gross_exposure: float
    book_mode: str
    top_n: int
    group_level: str
    members: list[StrategyMember]
    approved: bool = True
    source_run_id: str = ""
    promotion_profile: str = "balanced"
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["members"] = [member.to_dict() for member in self.members]
        return payload


def _build_registry() -> dict[str, AlphaDefinition]:
    entries = [
        AlphaDefinition(
            name="rev_close_1d",
            family="short_reversion",
            formula_fn="rev_close_1d",
            default_params={},
            parameter_grid={},
        ),
        AlphaDefinition(
            name="rev_close_3d",
            family="short_reversion",
            formula_fn="rev_close_3d",
            default_params={"lookback": 3},
            parameter_grid={"lookback": [3, 5]},
        ),
        AlphaDefinition(
            name="intraday_fade",
            family="short_reversion",
            formula_fn="intraday_fade",
            default_params={},
            parameter_grid={},
        ),
        AlphaDefinition(
            name="gap_fade",
            family="short_reversion",
            formula_fn="gap_fade",
            default_params={},
            parameter_grid={},
        ),
        AlphaDefinition(
            name="vwap_gap_revert",
            family="vwap_reversion",
            formula_fn="vwap_gap_revert",
            default_params={},
            parameter_grid={},
        ),
        AlphaDefinition(
            name="vwap_extreme_revert",
            family="vwap_reversion",
            formula_fn="vwap_extreme_revert",
            default_params={"window": 3},
            parameter_grid={"window": [3, 5, 10]},
        ),
        AlphaDefinition(
            name="pv_corr_contra",
            family="price_volume",
            formula_fn="pv_corr_contra",
            default_params={"corr_window": 5, "rank_window": 3},
            parameter_grid={"corr_window": [5, 10, 20], "rank_window": [3, 5, 10]},
        ),
        AlphaDefinition(
            name="volshock_reversal",
            family="price_volume",
            formula_fn="volshock_reversal",
            default_params={},
            parameter_grid={},
        ),
        AlphaDefinition(
            name="adv_participation_revert",
            family="price_volume",
            formula_fn="adv_participation_revert",
            default_params={"lookback": 3},
            parameter_grid={"lookback": [3, 5]},
        ),
        AlphaDefinition(
            name="smooth_momentum",
            family="momentum",
            formula_fn="smooth_momentum",
            default_params={"window": 20},
            parameter_grid={"window": [20, 42, 63]},
        ),
        AlphaDefinition(
            name="skip_month_momentum",
            family="literature_momentum",
            formula_fn="skip_month_momentum",
            default_params={"lookback": 126, "skip": 21},
            parameter_grid={"lookback": [126, 189, 252], "skip": [21]},
        ),
        AlphaDefinition(
            name="high_52w_proximity",
            family="literature_momentum",
            formula_fn="high_52w_proximity",
            default_params={"window": 252},
            parameter_grid={"window": [126, 189, 252]},
        ),
        AlphaDefinition(
            name="low_volatility_defensive",
            family="low_volatility",
            formula_fn="low_volatility_defensive",
            default_params={"window": 63},
            parameter_grid={"window": [42, 63, 126]},
        ),
        AlphaDefinition(
            name="breakout_quality",
            family="momentum",
            formula_fn="breakout_quality",
            default_params={"window": 20},
            parameter_grid={"window": [20, 42, 63]},
        ),
        AlphaDefinition(
            name="momentum_with_volume_confirm",
            family="momentum",
            formula_fn="momentum_with_volume_confirm",
            default_params={"window": 10},
            parameter_grid={"window": [10, 20, 42]},
        ),
        AlphaDefinition(
            name="profit_asset_gate_proxy_v1",
            family="proxy_control",
            formula_fn="profit_asset_gate_proxy_v1",
            default_params={
                "profit_window": 63,
                "asset_window": 63,
                "mom_window": 5,
                "asset_gate_threshold": 0.5,
            },
            parameter_grid={},
        ),
        AlphaDefinition(
            name="profit_asset_gate_proxy_v2",
            family="proxy_control",
            formula_fn="profit_asset_gate_proxy_v2",
            default_params={
                "profit_window": 63,
                "asset_window": 42,
                "mom_window": 5,
                "asset_gate_threshold": 0.6,
            },
            parameter_grid={
                "profit_window": [42, 63, 84],
                "asset_window": [21, 42, 63],
                "mom_window": [3, 5, 10],
                "asset_gate_threshold": [0.5, 0.6],
            },
        ),
    ]
    return {entry.name: entry for entry in entries}


ALPHA_REGISTRY = _build_registry()
ALPHA_SET_ALIASES: dict[str, list[str]] = {
    "literature_core": [
        "skip_month_momentum",
        "high_52w_proximity",
        "low_volatility_defensive",
        "smooth_momentum",
        "breakout_quality",
        "momentum_with_volume_confirm",
        "vwap_gap_revert",
        "profit_asset_gate_proxy_v1",
    ]
}


def get_alpha_definition(name: str) -> AlphaDefinition:
    key = str(name).strip().lower()
    if key not in ALPHA_REGISTRY:
        raise KeyError(f"Unknown alpha definition: {name}")
    return ALPHA_REGISTRY[key]


def get_alpha_registry() -> dict[str, AlphaDefinition]:
    return dict(ALPHA_REGISTRY)


def registry_model_names() -> list[str]:
    return sorted(ALPHA_REGISTRY)


def resolve_alpha_set(alpha_set: str | None) -> list[AlphaDefinition]:
    raw = str(alpha_set or DEFAULT_ALPHA_SET).strip()
    if not raw or raw.lower() == DEFAULT_ALPHA_SET:
        return [ALPHA_REGISTRY[name] for name in sorted(ALPHA_REGISTRY)]
    alias_key = raw.lower()
    if alias_key in ALPHA_SET_ALIASES:
        return [ALPHA_REGISTRY[name] for name in ALPHA_SET_ALIASES[alias_key]]

    selected: list[AlphaDefinition] = []
    seen: set[str] = set()
    tokens = [token.strip().lower() for token in raw.split(",") if token.strip()]
    families = {definition.family for definition in ALPHA_REGISTRY.values()}
    for token in tokens:
        if token in ALPHA_SET_ALIASES:
            for name in ALPHA_SET_ALIASES[token]:
                if name not in seen:
                    selected.append(ALPHA_REGISTRY[name])
                    seen.add(name)
            continue
        if token in ALPHA_REGISTRY and token not in seen:
            selected.append(ALPHA_REGISTRY[token])
            seen.add(token)
            continue
        if token in families:
            for definition in sorted(ALPHA_REGISTRY.values(), key=lambda item: item.name):
                if definition.family == token and definition.name not in seen:
                    selected.append(definition)
                    seen.add(definition.name)
            continue
        raise KeyError(f"Unknown alpha-set token: {token}")

    if not selected:
        raise KeyError(f"No alpha definitions resolved from alpha-set: {raw}")
    return selected


def selected_strategy_path(private_dir: Path) -> Path:
    return private_dir / "selected_strategy.json"


def shadow_strategy_path(private_dir: Path) -> Path:
    return private_dir / "shadow_strategy.json"


def write_strategy_spec(path: Path, strategy: StrategySpec) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(strategy.to_dict(), indent=2), encoding="utf-8")
    return path


def load_strategy_spec(path: Path, *, require_approved: bool = False) -> StrategySpec:
    if not path.exists():
        raise FileNotFoundError(f"Strategy spec not found: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    members = [
        StrategyMember(
            name=str(member.get("name", "")),
            alpha_name=str(member.get("alpha_name", "")),
            family=str(member.get("family", "")),
            weight=float(member.get("weight", 0.0)),
            params=dict(member.get("params", {})),
            group_level=str(member.get("group_level", "sector") or "sector"),
            book_mode=str(member.get("book_mode", "sector") or "sector"),
            top_n=int(member.get("top_n", payload.get("top_n", 30))),
            signal_decay=int(member.get("signal_decay", 0)),
            score_truncation=(
                None
                if member.get("score_truncation", None) in {"", None}
                else float(member.get("score_truncation"))
            ),
        )
        for member in payload.get("members", [])
    ]
    strategy = StrategySpec(
        strategy_type=str(payload.get("strategy_type", "single") or "single"),
        feed=str(payload.get("feed", "sip") or "sip"),
        gross_exposure=float(payload.get("gross_exposure", 4.0)),
        book_mode=str(payload.get("book_mode", "sector") or "sector"),
        top_n=int(payload.get("top_n", 30)),
        group_level=str(payload.get("group_level", "sector") or "sector"),
        members=members,
        approved=bool(payload.get("approved", True)),
        source_run_id=str(payload.get("source_run_id", "")),
        promotion_profile=str(payload.get("promotion_profile", "balanced") or "balanced"),
        notes=[str(item) for item in payload.get("notes", [])],
    )
    if require_approved and not strategy.approved:
        raise ValueError(f"Strategy spec is not approved for runtime use: {path}")
    if not strategy.members:
        raise ValueError(f"Strategy spec has no members: {path}")
    return strategy
