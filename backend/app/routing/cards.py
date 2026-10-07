"""Model cards: what is publicly known about a model BEFORE anyone has run it on our Goals.

docs/plan_2026-10_priors_library_survey.md §2.1. A model with no fitted data and no predecessor used
to start from a pure guess (theta ~ N(0, sigma_theta^2), z ~ N(0, I)). With a card it starts from a
regression on its metadata, learned jointly across every catalogue model with public results:

    theta[m, 0] = beta . x_m + f_theta[family(m)] + sigma_theta * xi        (no predecessor)
    z[m]        = B x_m      + f_z[family(m)]     + xi_z                     (xi_z ~ N(0, I))

x_m are standardised covariates (missing values imputed at the mean, with their own indicator), and
the family effects are pooled (f ~ N(0, tau_f^2)), so a new member of a known family starts near its
family. A version successor still starts from its predecessor first (model.py): the card only
replaces the population prior.

numpy only: the API process imports this (predict.py) as well as the worker (model.py, fit.py).
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from typing import Any, Iterable, Mapping, Optional, Sequence

import numpy as np

# (name, can be missing). Order is part of the stored meta (cov_names), so append only.
COVARIATES: tuple[tuple[str, bool], ...] = (
    ("release_years", True),       # years since 2024-01-01
    ("log_price_in", True),        # ln(USD per Mtok input + 0.01)
    ("log_price_out", True),
    ("open_weights", True),
    ("log_params_b", True),        # ln(total parameters in billions)
    ("log_active_params_b", True),  # ln(active parameters, MoE) -- equals total for dense models
    ("reasoning", True),           # 0 none, 0.33 low, 0.67 medium, 1 high
    ("log_context_k", True),       # ln(context window in thousands of tokens)
)
_EPOCH = date(2024, 1, 1)
EFFORT = {"none": 0.0, "minimal": 0.15, "low": 0.33, "medium": 0.67, "high": 1.0, "xhigh": 1.0, "max": 1.0}


@dataclass(frozen=True)
class ModelCard:
    model_key: str
    family: Optional[str] = None
    provider: Optional[str] = None
    release_date: Optional[date] = None
    training_cutoff: Optional[date] = None
    open_weights: Optional[bool] = None
    params_b: Optional[float] = None
    active_params_b: Optional[float] = None
    reasoning: Optional[float] = None
    context_k: Optional[float] = None
    price_in: Optional[float] = None
    price_out: Optional[float] = None
    aliases: tuple[str, ...] = ()
    source: str = ""
    extra: Mapping[str, Any] = field(default_factory=dict)

    def raw(self) -> list[Optional[float]]:
        """Covariate values in COVARIATES order (None = unknown)."""
        def ln(v: Optional[float], add: float = 0.0) -> Optional[float]:
            return None if v is None or v + add <= 0 else math.log(v + add)

        active = self.active_params_b if self.active_params_b is not None else self.params_b
        return [
            None if self.release_date is None else (self.release_date - _EPOCH).days / 365.25,
            ln(self.price_in, 0.01), ln(self.price_out, 0.01),
            None if self.open_weights is None else float(bool(self.open_weights)),
            ln(self.params_b), ln(active), self.reasoning, ln(self.context_k),
        ]

    def contamination_cutoff(self) -> Optional[date]:
        """The date before which a public benchmark item may be in the training data: the declared
        training cutoff, else six months before release (a common lag; marked as a proxy by callers)."""
        if self.training_cutoff is not None:
            return self.training_cutoff
        if self.release_date is not None:
            r = self.release_date
            months = r.year * 12 + r.month - 1 - 6
            return date(months // 12, months % 12 + 1, min(r.day, 28))
        return None

    def to_row(self) -> dict[str, Any]:
        return {"model_key": self.model_key, "family": self.family, "provider": self.provider,
                "release_date": self.release_date, "training_cutoff": self.training_cutoff,
                "open_weights": self.open_weights, "params_b": self.params_b,
                "active_params_b": self.active_params_b, "reasoning": self.reasoning, "context_k": self.context_k,
                "price_in": self.price_in, "price_out": self.price_out, "aliases": list(self.aliases),
                "source": self.source}


def card_from_row(row: Mapping[str, Any]) -> ModelCard:
    def d(v: Any) -> Optional[date]:
        if v is None or isinstance(v, date) and not isinstance(v, datetime):
            return v
        if isinstance(v, datetime):
            return v.date()
        return date.fromisoformat(str(v)[:10])

    def f(v: Any) -> Optional[float]:
        return None if v is None else float(v)

    return ModelCard(model_key=str(row["model_key"]), family=row.get("family"), provider=row.get("provider"),
                     release_date=d(row.get("release_date")), training_cutoff=d(row.get("training_cutoff")),
                     open_weights=row.get("open_weights"), params_b=f(row.get("params_b")),
                     active_params_b=f(row.get("active_params_b")), reasoning=f(row.get("reasoning")),
                     context_k=f(row.get("context_k")), price_in=f(row.get("price_in")),
                     price_out=f(row.get("price_out")), aliases=tuple(row.get("aliases") or ()),
                     source=row.get("source") or "")


# ---------------------------------------------------------------- canonical keys

_DATE_SUFFIX = re.compile(r"-(20\d{2}-?\d{2}-?\d{2}|latest|preview|instruct|exp)$")   # snapshot dates; NOT "-0905" (a version)
_CLAUDE_ORDER = re.compile(r"^claude-(\d+(?:-\d+)?)-(opus|sonnet|haiku)\b")


def canonical_key(name: str) -> str:
    """One spelling per model, so public evidence, provider connections and callers' clientInfo meet:
    lower case, no provider prefix ("anthropic/"), dots as dashes, no trailing release date or
    -preview / -instruct / -latest, and Claude's two published word orders made one
    ("claude-3-5-sonnet" and "claude-sonnet-3.5" -> "claude-sonnet-3-5")."""
    key = name.strip().lower()
    key = key.split("/")[-1].split(":")[0]
    key = re.sub(r"[._\s]+", "-", key)
    key = re.sub(r"-+", "-", key).strip("-")
    previous = None
    while previous != key:
        previous = key
        key = _DATE_SUFFIX.sub("", key)
    return _CLAUDE_ORDER.sub(r"claude-\2-\1", key)


def resolve(name: str, cards: Mapping[str, ModelCard]) -> Optional[ModelCard]:
    """The card for a model key or any alias of it (exact first, then canonical)."""
    if name in cards:
        return cards[name]
    canon = canonical_key(name)
    for card in cards.values():
        if canon == canonical_key(card.model_key) or canon in {canonical_key(a) for a in card.aliases}:
            return card
    return None


_SIZE = re.compile(r"(?<![\d.])(\d+(?:\.\d+)?)\s*b(?![a-z])")
_ACTIVE = re.compile(r"a(\d+(?:\.\d+)?)b(?![a-z])")


def params_from_name(name: str) -> tuple[Optional[float], Optional[float]]:
    """(total, active) billions read off names like 'qwen3-coder-480b-a35b' or 'llama-3.1-8b'."""
    low = name.lower()
    active = _ACTIVE.search(low)
    rest = _ACTIVE.sub("", low)
    sizes = [float(m) for m in _SIZE.findall(rest)]
    total = max(sizes) if sizes else None
    return total, (float(active.group(1)) if active else total)


# ---------------------------------------------------------------- design matrix

@dataclass(frozen=True)
class Design:
    """Standardisation learned on the fit's models; stored in routing_params.meta so decision time
    builds a new model's x the same way."""
    names: tuple[str, ...]
    mean: np.ndarray
    sd: np.ndarray
    families: tuple[str, ...]

    def to_meta(self) -> dict[str, Any]:
        return {"cov_names": list(self.names), "cov_mean": self.mean.tolist(), "cov_sd": self.sd.tolist(),
                "families": list(self.families)}

    @staticmethod
    def from_meta(meta: Mapping[str, Any]) -> Optional["Design"]:
        if not meta.get("cov_names"):
            return None
        return Design(tuple(meta["cov_names"]), np.asarray(meta["cov_mean"], dtype=np.float64),
                      np.asarray(meta["cov_sd"], dtype=np.float64), tuple(meta.get("families") or ()))

    def row(self, card: Optional[ModelCard]) -> np.ndarray:
        """Standardised covariates + missing indicators for one model (all zeros without a card:
        the population mean, i.e. exactly the old prior)."""
        out = np.zeros(len(self.names))
        if card is None:
            return out
        n_cov = len(COVARIATES)
        for j, ((_name, missable), v) in enumerate(zip(COVARIATES, card.raw())):
            if v is not None:
                out[j] = (v - self.mean[j]) / self.sd[j]
            if missable:                                  # centred indicator: 0 on average over the fit's models
                out[n_cov + j] = (0.0 if v is not None else 1.0) - self.mean[n_cov + j]
        return out

    def family_index(self, card: Optional[ModelCard]) -> int:
        if card is None or not card.family or card.family not in self.families:
            return -1
        return self.families.index(card.family)


def design_for(cards: Sequence[Optional[ModelCard]]) -> Design:
    """Learn the standardisation (and the family list) from the models of a fit."""
    n_cov = len(COVARIATES)
    names = tuple(n for n, _ in COVARIATES) + tuple(f"missing_{n}" for n, _ in COVARIATES)
    values = np.full((max(len(cards), 1), n_cov), np.nan)
    for i, c in enumerate(cards):
        if c is not None:
            values[i] = [np.nan if v is None else v for v in c.raw()]
    mean = np.zeros(2 * n_cov)
    sd = np.ones(2 * n_cov)
    for j in range(n_cov):
        col = values[:, j][~np.isnan(values[:, j])]
        if col.size:
            mean[j] = float(col.mean())
            sd[j] = float(col.std()) if col.size > 1 and col.std() > 1e-9 else 1.0
        mean[n_cov + j] = float(np.isnan(values[:, j]).mean())        # centring of the missing indicator
    families: list[str] = []
    for c in cards:
        if c is not None and c.family and c.family not in families:
            families.append(c.family)
    return Design(names, mean, sd, tuple(sorted(families)))


def design_matrix(design: Design, cards: Sequence[Optional[ModelCard]]) -> tuple[np.ndarray, np.ndarray]:
    """(X (M, C), family index (M,) with -1 = none) for the models of a fit, in their order. An
    indicator that never varies over the fit is centred to a zero column (nothing to learn from it)."""
    x = np.stack([design.row(c) for c in cards]) if cards else np.zeros((0, len(design.names)))
    fam = np.array([design.family_index(c) for c in cards], dtype=np.int32)
    return x, fam


# ---------------------------------------------------------------- decision-time prior

def prior_mean(arrays: Mapping[str, np.ndarray], meta: Mapping[str, Any],
               card: Optional[ModelCard], rng: np.random.Generator) -> tuple[np.ndarray, np.ndarray]:
    """(mu_theta (S,), mu_z (S, K)) of a model the fit has never seen and that has no fitted
    predecessor: its card's regression plus its family effect (an unknown family draws one from the
    family prior). Zeros -- the old population prior -- when the stored parameters predate cards."""
    s_count = int(arrays["tau_c"].shape[0])
    k = int(arrays["z"].shape[2]) if np.ndim(arrays.get("z")) == 3 else 0
    design = Design.from_meta(meta)
    if design is None or "card_beta" not in arrays:
        return np.zeros(s_count), np.zeros((s_count, k))
    x = design.row(card)
    mu_theta = arrays["card_beta"] @ x                                     # (S,)
    mu_z = np.einsum("skc,c->sk", arrays["card_B"], x) if k else np.zeros((s_count, 0))
    fam = design.family_index(card)
    if fam >= 0:
        mu_theta = mu_theta + arrays["fam_theta"][:, fam]
        if k:
            mu_z = mu_z + arrays["fam_z"][:, fam, :]
    elif card is not None and card.family:                            # a family the fit has not seen
        mu_theta = mu_theta + arrays["tau_fam_theta"] * rng.standard_normal(s_count)
        if k:
            mu_z = mu_z + arrays["tau_fam_z"][:, None] * rng.standard_normal((s_count, k))
    return mu_theta, mu_z


def load_cards(rows: Iterable[Mapping[str, Any]]) -> dict[str, ModelCard]:
    out: dict[str, ModelCard] = {}
    for r in rows:
        card = card_from_row(r)
        out[card.model_key] = card
    return out


def with_aliases(card: ModelCard, *names: str) -> ModelCard:
    extra = tuple(n for n in names if n and n != card.model_key and n not in card.aliases)
    return replace(card, aliases=card.aliases + extra) if extra else card
