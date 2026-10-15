"""Free public sources that need no key: Internet Archive CDX, RDAP, DNS / Team Cymru ASN."""
from __future__ import annotations

import socket
from datetime import datetime, timezone
from urllib.parse import urlparse

import httpx

from ...core.claims import NotRun
from ..platforms import is_local, resolves_to_private
from .base import Candidate, SearchAdapter

UA = {"User-Agent": "DFIR-investigation-tool/0.1 (evidence preservation; contact: local user)"}


class WaybackCDX(SearchAdapter):
    name, title, kind = "wayback", "Internet Archive (Wayback CDX — existing snapshots)", "archive"
    notes = "Read-only lookup of existing public snapshots. Does not request new archiving."

    def available(self):
        return None

    def snapshots(self, url: str, limit: int = 25) -> list[dict] | NotRun:
        host = urlparse(url).hostname or ""
        if is_local(host):
            return NotRun(component=self.name, reason="local/test URL — not archived publicly")
        try:
            r = httpx.get("https://web.archive.org/cdx/search/cdx",
                          params={"url": url, "output": "json", "limit": limit,
                                  "fl": "timestamp,original,statuscode,digest,mimetype"}, headers=UA, timeout=30)
            r.raise_for_status()
            rows = r.json() if r.text.strip() else []
        except (httpx.HTTPError, ValueError) as e:
            return NotRun(component=self.name, reason=f"Wayback CDX unavailable: {type(e).__name__}")
        out = []
        for row in rows[1:]:
            ts, orig, code, digest, mime = row
            out.append({"timestamp": datetime.strptime(ts, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc).isoformat(),
                        "url": orig, "status": code, "digest": digest, "mime": mime,
                        "snapshot_url": f"https://web.archive.org/web/{ts}/{orig}"})
        return out


class DomainIntel(SearchAdapter):
    name, title, kind = "domain_intel", "Domain registration (RDAP) & hosting (DNS/ASN)", "domain"
    notes = "Hosting location describes the server/CDN, never the uploader's location."

    def available(self):
        return None

    def lookup(self, domain: str) -> dict | NotRun:
        if is_local(domain) or resolves_to_private(domain):
            return NotRun(component=self.name, reason="local/private address — no public registration data")
        out: dict = {"domain": domain}
        # registrable domain heuristic (last two labels, or three for co.in style)
        parts = domain.split(".")
        reg = ".".join(parts[-3:]) if len(parts) >= 3 and parts[-2] in ("co", "com", "org", "net", "gov", "ac") \
            else ".".join(parts[-2:])
        out["registrable_domain"] = reg
        try:
            r = httpx.get(f"https://rdap.org/domain/{reg}", headers=UA, timeout=30, follow_redirects=True)
            if r.status_code == 200:
                j = r.json()
                ev = {e.get("eventAction"): e.get("eventDate") for e in j.get("events", [])}
                registrar = next((ent for ent in j.get("entities", []) if "registrar" in ent.get("roles", [])), {})
                vcard = registrar.get("vcardArray", [None, []])[1] if registrar.get("vcardArray") else []
                out["rdap"] = {"registered": ev.get("registration"), "updated": ev.get("last changed"),
                               "expires": ev.get("expiration"), "status": j.get("status"),
                               "registrar": next((v[3] for v in vcard if v[0] == "fn"), None),
                               "nameservers": [n.get("ldhName") for n in j.get("nameservers", [])],
                               "abuse_contact": _abuse(j)}
            else:
                out["rdap_error"] = f"HTTP {r.status_code}"
        except (httpx.HTTPError, ValueError) as e:
            out["rdap_error"] = type(e).__name__
        try:
            ips = sorted({ai[4][0] for ai in socket.getaddrinfo(domain, None) if ":" not in ai[4][0]})[:4]
            out["ips"] = ips
            out["asn"] = [_cymru(ip) for ip in ips[:2]]
        except socket.gaierror:
            out["ips"] = []
        return out


def _abuse(j: dict) -> str | None:
    stack = list(j.get("entities", []))
    while stack:
        e = stack.pop()
        if "abuse" in e.get("roles", []):
            for v in (e.get("vcardArray") or [None, []])[1]:
                if v[0] == "email":
                    return v[3]
        stack.extend(e.get("entities", []))
    return None


def _cymru(ip: str) -> dict:
    try:
        import dns.resolver
        q = ".".join(reversed(ip.split("."))) + ".origin.asn.cymru.com"
        txt = str(dns.resolver.resolve(q, "TXT", lifetime=5)[0]).strip('"')
        asn, prefix, cc, rir, date = [x.strip() for x in txt.split("|")]
        name = ""
        try:
            name = str(dns.resolver.resolve(f"AS{asn.split()[0]}.asn.cymru.com", "TXT", lifetime=5)[0]).strip('"').split("|")[-1].strip()
        except Exception:  # noqa: BLE001
            pass
        return {"ip": ip, "asn": asn, "prefix": prefix, "country": cc, "as_name": name,
                "note": "Hosting network of the server/CDN — not the uploader's location"}
    except Exception as e:  # noqa: BLE001
        return {"ip": ip, "error": type(e).__name__}


__all__ = ["WaybackCDX", "DomainIntel", "Candidate"]
