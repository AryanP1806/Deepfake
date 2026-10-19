"""Safety gate for sensitive cases.

* ``minors_possible``: the system must not download or store media from discovered URLs.
  Only the URL, response metadata and (when explicitly allowed) a page screenshot with the
  media region obscured are kept. The user is routed to NCRP's women/child reporting route.
* ``intimate`` (NCII): previews are blurred by default in the UI; StopNCII and the IT Rules
  2-hour takedown route (IT Rules 3(2)(b), as amended 2026) are surfaced.

Propagation is limited to the public web; there is no Tor/dark-web crawling.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SafetyPolicy:
    minors_possible: bool = False
    intimate: bool = False

    @classmethod
    def from_case(cls, safety: dict | None) -> "SafetyPolicy":
        safety = safety or {}
        return cls(minors_possible=bool(safety.get("minors_possible")), intimate=bool(safety.get("intimate")))

    @property
    def may_download_discovered_media(self) -> bool:
        return not self.minors_possible

    @property
    def may_screenshot_pages(self) -> bool:
        # Screenshots of pages that may contain child sexual abuse material must not be stored.
        return not self.minors_possible

    @property
    def blur_previews(self) -> bool:
        return self.minors_possible or self.intimate

    def notices(self) -> list[dict]:
        out = []
        if self.minors_possible:
            out.append({
                "level": "critical",
                "title": "Possible involvement of a minor",
                "text": ("Do not download, store, forward or share this material — possessing or sharing it can itself "
                         "be an offence. This system records only URLs and response metadata for such locations. "
                         "Report it on cybercrime.gov.in under 'Report Women/Child Related Crime' (anonymous reporting "
                         "is available there for child sexual abuse material), or call 1930."),
            })
        if self.intimate:
            out.append({
                "level": "high",
                "title": "Intimate imagery (NCII)",
                "text": ("Previews are blurred by default. StopNCII.org can create hashes on your own device so "
                         "participating platforms can block re-uploads without you sending the image to anyone. "
                         "Under the IT Rules (as amended in 2026) platforms are expected to remove intimate or "
                         "artificially morphed images within about 2 hours of a complaint from the person depicted. "
                         "Verify the current rule text with a lawyer or the police."),
            })
        return out
