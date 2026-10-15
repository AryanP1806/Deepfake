"""Search / OSINT adapter contract. Candidates are never evidence until captured and matched."""
from __future__ import annotations

from dataclasses import dataclass, field

from ...core.claims import NotRun
from ...core.config import get_key


@dataclass
class Candidate:
    url: str
    source: str
    title: str = ""
    snippet: str = ""
    seen_at: str | None = None
    extra: dict = field(default_factory=dict)


class SearchAdapter:
    name = ""
    title = ""
    key_names: tuple[str, ...] = ()
    consent_action: str | None = None  # set when the query sends case media/data to a third party
    kind = "image"  # image | text | channel | archive | domain
    notes = ""

    def available(self) -> NotRun | None:
        missing = [k for k in self.key_names if not get_key(k)]
        if missing:
            return NotRun(component=self.name, reason=f"API key not configured ({', '.join(missing)})",
                          how_to_enable="Add the key under Settings → External services")
        return None

    def describe(self) -> dict:
        nr = self.available()
        return {"name": self.name, "title": self.title, "kind": self.kind, "enabled": nr is None,
                "reason": nr.reason if nr else "", "how_to_enable": nr.how_to_enable if nr else "",
                "consent_action": self.consent_action, "notes": self.notes}
