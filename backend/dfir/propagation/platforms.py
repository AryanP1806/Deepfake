"""URL → platform / handle heuristics (public URL structure only)."""
from __future__ import annotations

import ipaddress
import re
import socket
from urllib.parse import urlparse

PLATFORMS = [
    (r"(^|\.)instagram\.com$", "Instagram", r"^/([A-Za-z0-9_.]+)/?(?:$|p/|reel/)"),
    (r"(^|\.)(x|twitter)\.com$", "X (Twitter)", r"^/([A-Za-z0-9_]{1,15})(?:/|$)"),
    (r"(^|\.)facebook\.com$|(^|\.)fb\.watch$", "Facebook", r"^/([A-Za-z0-9.]+)/?"),
    (r"(^|\.)youtube\.com$|(^|\.)youtu\.be$", "YouTube", r"^/@([A-Za-z0-9_.-]+)"),
    (r"(^|\.)tiktok\.com$", "TikTok", r"^/@([A-Za-z0-9_.]+)"),
    (r"(^|\.)snapchat\.com$", "Snapchat", r"^/add/([A-Za-z0-9_.-]+)"),
    (r"(^|\.)t\.me$|(^|\.)telegram\.(me|org)$", "Telegram", r"^/(?:s/)?([A-Za-z0-9_]{4,})"),
    (r"(^|\.)reddit\.com$", "Reddit", r"^/(?:u|user)/([A-Za-z0-9_-]+)"),
    (r"(^|\.)sharechat\.com$", "ShareChat", r"^/profile/([A-Za-z0-9_.]+)"),
    (r"(^|\.)mojapp\.in$", "Moj", r"^/@([A-Za-z0-9_.]+)"),
    (r"(^|\.)threads\.(net|com)$", "Threads", r"^/@([A-Za-z0-9_.]+)"),
    (r"(^|\.)linkedin\.com$", "LinkedIn", r"^/in/([A-Za-z0-9_-]+)"),
    (r"(^|\.)dailymotion\.com$", "Dailymotion", r"^/([A-Za-z0-9_-]+)"),
    (r"(^|\.)whatsapp\.com$", "WhatsApp (public channel)", r"^/channel/([A-Za-z0-9]+)"),
]
NOT_HANDLES = {"p", "reel", "reels", "watch", "status", "share", "explore", "s", "stories", "video", "videos", "tv",
               "home", "search", "i", "intent", "hashtag", "photo", "permalink.php", "story.php", "groups", "events"}


def classify(url: str) -> dict:
    u = urlparse(url)
    host = (u.hostname or "").lower()
    for dom_re, name, handle_re in PLATFORMS:
        if re.search(dom_re, host):
            m = re.search(handle_re, u.path or "")
            h = m.group(1) if m else ""
            return {"platform": name, "domain": host, "handle": "" if h.lower() in NOT_HANDLES else h}
    return {"platform": "Website" if not is_local(host) else "Local/test site", "domain": host, "handle": ""}


def is_local(host: str) -> bool:
    if host in ("localhost",) or host.endswith(".localhost") or host.endswith(".test"):
        return True
    try:
        return ipaddress.ip_address(host).is_private or ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def resolves_to_private(host: str) -> bool:
    try:
        return any(ipaddress.ip_address(ai[4][0]).is_private for ai in socket.getaddrinfo(host, None))
    except (socket.gaierror, ValueError):
        return False
