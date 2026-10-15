"""Keyed search adapters. All ship disabled until keys are configured; image-sending adapters
additionally need per-use consent. Results become *candidate* Locations that must be captured
and matched before they count as evidence."""
from __future__ import annotations

import re

import httpx

from ...core.claims import NotRun
from ...core.config import get_key
from .base import Candidate, SearchAdapter


class SerpApiLens(SearchAdapter):
    name, title, kind = "serpapi_lens", "Google Lens via SerpAPI (reverse image)", "image"
    key_names, consent_action = ("SERPAPI_KEY",), "search:serpapi"
    notes = "Google Lens requires a publicly reachable image URL; uses the media URL of a captured location."

    def search_image_url(self, image_url: str) -> list[Candidate]:
        r = httpx.get("https://serpapi.com/search.json",
                      params={"engine": "google_lens", "url": image_url, "api_key": get_key("SERPAPI_KEY")}, timeout=60)
        r.raise_for_status()
        j = r.json()
        return [Candidate(url=m.get("link"), source=self.name, title=m.get("title", ""),
                          extra={"thumbnail": m.get("thumbnail"), "source_name": m.get("source")})
                for m in j.get("visual_matches", []) if m.get("link")]


class SerpApiWeb(SearchAdapter):
    name, title, kind = "serpapi_web", "Google web search via SerpAPI (caption/text)", "text"
    key_names, consent_action = ("SERPAPI_KEY",), None

    def search_text(self, query: str) -> list[Candidate]:
        r = httpx.get("https://serpapi.com/search.json",
                      params={"engine": "google", "q": query, "api_key": get_key("SERPAPI_KEY"), "num": 20}, timeout=60)
        r.raise_for_status()
        return [Candidate(url=o["link"], source=self.name, title=o.get("title", ""), snippet=o.get("snippet", ""),
                          seen_at=o.get("date")) for o in r.json().get("organic_results", []) if o.get("link")]


class TinEye(SearchAdapter):
    name, title, kind = "tineye", "TinEye reverse image search API", "image"
    key_names, consent_action = ("TINEYE_API_KEY",), "search:tineye"

    def search_image_bytes(self, data: bytes, filename: str) -> list[Candidate]:
        r = httpx.post("https://api.tineye.com/rest/search/", headers={"x-api-key": get_key("TINEYE_API_KEY")},
                       files={"image_upload": (filename, data)}, timeout=120)
        r.raise_for_status()
        out = []
        for m in (r.json().get("results") or {}).get("matches", []):
            for b in m.get("backlinks", []):
                out.append(Candidate(url=b.get("backlink") or b.get("url"), source=self.name,
                                     seen_at=b.get("crawl_date"), extra={"image_url": b.get("url"),
                                                                         "domain": m.get("domain")}))
        return [c for c in out if c.url]


class BingVisualSearch(SearchAdapter):
    name, title, kind = "bing_visual", "Bing Visual Search", "image"

    def available(self):
        return NotRun(component=self.name,
                      reason="Microsoft retired the Bing Search APIs (including Visual Search) in August 2025.",
                      how_to_enable="Use another reverse-image provider.")


class YouTubeSearch(SearchAdapter):
    name, title, kind = "youtube", "YouTube Data API v3 (text search)", "text"
    key_names = ("YOUTUBE_API_KEY",)

    def search_text(self, query: str) -> list[Candidate]:
        r = httpx.get("https://www.googleapis.com/youtube/v3/search",
                      params={"part": "snippet", "q": query, "type": "video", "maxResults": 25,
                              "key": get_key("YOUTUBE_API_KEY")}, timeout=60)
        r.raise_for_status()
        return [Candidate(url=f"https://www.youtube.com/watch?v={i['id']['videoId']}", source=self.name,
                          title=i["snippet"]["title"], snippet=i["snippet"].get("description", ""),
                          seen_at=i["snippet"].get("publishedAt"),
                          extra={"channel": i["snippet"].get("channelTitle"), "channel_id": i["snippet"].get("channelId")})
                for i in r.json().get("items", []) if i.get("id", {}).get("videoId")]


class RedditSearch(SearchAdapter):
    name, title, kind = "reddit", "Reddit API (text search)", "text"
    key_names = ("REDDIT_CLIENT_ID", "REDDIT_CLIENT_SECRET")

    def search_text(self, query: str) -> list[Candidate]:
        tok = httpx.post("https://www.reddit.com/api/v1/access_token", data={"grant_type": "client_credentials"},
                         auth=(get_key("REDDIT_CLIENT_ID"), get_key("REDDIT_CLIENT_SECRET")),
                         headers={"User-Agent": "dfir-investigation/0.1"}, timeout=30).json()["access_token"]
        r = httpx.get("https://oauth.reddit.com/search", params={"q": query, "limit": 25, "sort": "new"},
                      headers={"Authorization": f"bearer {tok}", "User-Agent": "dfir-investigation/0.1"}, timeout=30)
        r.raise_for_status()
        out = []
        for ch in r.json().get("data", {}).get("children", []):
            d = ch["data"]
            out.append(Candidate(url="https://www.reddit.com" + d["permalink"], source=self.name, title=d.get("title", ""),
                                 seen_at=str(d.get("created_utc")), extra={"author": d.get("author"),
                                                                          "subreddit": d.get("subreddit")}))
        return out


class TelegramPublicChannel(SearchAdapter):
    name, title, kind = "telegram_public", "Telegram public channel preview (t.me/s/…)", "channel"
    notes = "Reads only the public web preview of channels you name. Private groups are not accessible."

    def available(self):
        return None

    def list_posts(self, channel: str) -> list[Candidate]:
        channel = channel.lstrip("@").strip("/")
        r = httpx.get(f"https://t.me/s/{channel}", timeout=30, follow_redirects=True,
                      headers={"User-Agent": "Mozilla/5.0 DFIR-evidence-capture"})
        r.raise_for_status()
        out = []
        for m in re.finditer(r'data-post="([^"]+)".*?(?:<time[^>]+datetime="([^"]+)")', r.text, re.S):
            post, dt = m.group(1), m.group(2)
            out.append(Candidate(url=f"https://t.me/{post}", source=self.name, seen_at=dt,
                                 extra={"channel": channel}))
        return out


ADAPTERS: list[SearchAdapter] = [SerpApiLens(), SerpApiWeb(), TinEye(), BingVisualSearch(), YouTubeSearch(),
                                 RedditSearch(), TelegramPublicChannel()]


def get_adapter(name: str) -> SearchAdapter:
    for a in ADAPTERS:
        if a.name == name:
            return a
    raise KeyError(name)
