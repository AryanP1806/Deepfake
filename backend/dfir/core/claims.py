"""The Claim model — the backbone of every finding in the system.

A detector score is never shown on its own. Every observation becomes a Claim
with an explicit epistemic status, the evidence it rests on, the tool that produced it,
its limitations and what would confirm it.

Status semantics (enforced by ``derive_status`` / ``validate_claim``):

* Verified     – direct or cryptographic observation (hash identity, valid C2PA signature,
                 a captured page that shows X, a geometrically verified match).
* Corroborated – two or more *independent* sources agree.
* Inferred     – derived by reasoning from other claims.
* Lead         – technical similarity only; worth following, not a conclusion.
* Unknown      – an explicit gap. Absence of a signal is always Unknown, never "clean".
"""
from __future__ import annotations

import re
import uuid
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class Status(str, Enum):
    VERIFIED = "Verified"
    CORROBORATED = "Corroborated"
    INFERRED = "Inferred"
    LEAD = "Lead"
    UNKNOWN = "Unknown"


class Strength(str, Enum):
    STRONG = "Strong"
    MODERATE = "Moderate"
    WEAK = "Weak"
    NONE = "None"


# Direction of a claim with respect to the manipulation question.
class Polarity(str, Enum):
    SUPPORTS_MANIPULATION = "supports_manipulation"
    SUPPORTS_AUTHENTIC = "supports_authentic"
    NEUTRAL = "neutral"


STATUS_RANK = {
    Status.VERIFIED: 4,
    Status.CORROBORATED: 3,
    Status.INFERRED: 2,
    Status.LEAD: 1,
    Status.UNKNOWN: 0,
}


class Claim(BaseModel):
    id: str = Field(default_factory=lambda: "clm_" + uuid.uuid4().hex[:12])
    case_id: str | None = None
    media_id: str | None = None
    stage: str  # decomposition | genealogy | propagation | graph | timeline | attribution | ...
    category: str  # e.g. metadata, c2pa, watermark, ela, detector, match, capture
    statement: str
    status: Status
    strength: Strength = Strength.NONE
    polarity: Polarity = Polarity.NEUTRAL
    evidence_refs: list[str] = Field(default_factory=list)  # vault sha256s
    produced_by: str  # "analyzer@version[#model_sha]"
    sources: list[str] = Field(default_factory=list)  # independent source identifiers
    limitations: str = ""
    what_would_confirm: str = ""
    why: str = ""  # plain-language explanation
    data: dict[str, Any] = Field(default_factory=dict)


class NotRun(BaseModel):
    """Returned by any analyzer/adapter that could not run. Rendered as a grey chip."""

    component: str
    reason: str
    how_to_enable: str = ""


def derive_status(*, direct_observation: bool = False, independent_sources: int = 0,
                  reasoning: bool = False, similarity_only: bool = False) -> Status:
    """Single source of truth for status assignment."""
    if direct_observation:
        return Status.VERIFIED
    if independent_sources >= 2:
        return Status.CORROBORATED
    if similarity_only:
        return Status.LEAD
    if reasoning or independent_sources == 1:
        return Status.INFERRED
    return Status.UNKNOWN


def corroborate(claims: list[Claim]) -> Status:
    """Status for a conclusion supported by ``claims``.

    Only claims from *different* producers with distinct source identifiers count as
    independent. Two outputs of the same model never corroborate each other.
    """
    producers = {c.produced_by.split("@")[0] for c in claims if c.status != Status.UNKNOWN}
    if any(c.status == Status.VERIFIED for c in claims):
        return Status.VERIFIED
    return derive_status(independent_sources=len(producers),
                         similarity_only=all(c.status == Status.LEAD for c in claims) and len(producers) < 2)


# ---------------------------------------------------------------------------
# Language guard for generated text. Attribution must never read as accusation.
# ---------------------------------------------------------------------------
BANNED_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\bcreated by\b", re.I), "originated from an account/source that was observed"),
    (re.compile(r"\bperpetrators?\b", re.I), "account observed"),
    (re.compile(r"\bculprits?\b", re.I), "account observed"),
    (re.compile(r"\bsuspects?\b", re.I), "account observed"),
    (re.compile(r"\bguilty\b", re.I), "[unproven]"),
    (re.compile(r"\bdefinitely (fake|real|authentic)\b", re.I), "assessed as \\1 with stated uncertainty"),
    (re.compile(r"\b(100|99(\.\d+)?)\s?% (fake|real|certain)\b", re.I), "[no certainty claimed]"),
    (re.compile(r"\bproves? (that )?(he|she|they) (made|created)\b", re.I), "[attribution unproven]"),
]


def lint_text(text: str) -> list[str]:
    """Return the banned phrases found in ``text``."""
    return [m.group(0) for pat, _ in BANNED_PATTERNS for m in pat.finditer(text)]


def sanitize_text(text: str) -> str:
    for pat, repl in BANNED_PATTERNS:
        text = pat.sub(repl, text)
    return text


def validate_claim(c: Claim) -> list[str]:
    """Return a list of rule violations (empty = valid)."""
    problems = []
    if c.status in (Status.VERIFIED, Status.CORROBORATED) and not (c.evidence_refs or c.sources):
        problems.append("Verified/Corroborated claims must reference evidence or sources")
    if c.status == Status.CORROBORATED and len(set(c.sources)) < 2:
        problems.append("Corroborated requires >= 2 independent sources")
    if lint_text(c.statement):
        problems.append(f"Banned phrasing: {lint_text(c.statement)}")
    return problems
