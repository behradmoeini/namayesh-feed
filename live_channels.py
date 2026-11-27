#!/usr/bin/env python3
"""Live TV channels for the Hengam TV app, from livefarsi.com (by agreement with its owner).

Reads the channel list and the categories, then each channel's page for its HLS stream, and checks
that the stream answers with a playlist. Writes live.json: the channels that work right now, with
name (Persian where known), logo, categories and stream addresses (the first, then backups). The app reads it, shows only those channels, and on
play reads the channel's page again for the newest address (falling back to this one).

  python3 live_channels.py            # writes live.json next to this file

Standard library only. Requests are sequential with a pause between them.
"""
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

SITE = "https://www.livefarsi.com"
UA = "HengamTVFeed/1.0 (+live channel list for the TV app, by agreement with livefarsi.com)"
PAUSE = 0.5
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "live.json")

# Category slug -> (Persian name, order on the tab). Unknown categories are left out of the tab
# but their channels still appear under "all".
CATEGORIES = {
    "iran": ("ایران", 1),
    "persian": ("فارسی", 2),
    "persian-news": ("خبر", 3),
    "persian-movie-channel": ("فیلم و سریال", 4),
    "gem-channels": ("جم", 5),
    "persian-music-channel": ("موسیقی", 6),
    "varzeshi": ("ورزش", 7),
    "persiana-media-group": ("پرشیانا", 8),
    "irib-ostani": ("استانی", 9),
    "mazhabi": ("مذهبی", 10),
    "afghanistan": ("افغانستان", 11),
}

# Persian names for the channels people know by them; the key is the name the site uses, lower-case
# with spaces, so one entry covers every copy of a channel the site lists under different pages.
# Channels with the same Persian name are merged into one, the other streams kept as backups.
NAME_FA = {
    "shabake 3 hd new": "شبکه ۳", "shabake 3 tv hd asli": "شبکه ۳", "shabakeye 1": "شبکه ۱",
    "shabakeye 2": "شبکه ۲", "shabakeye 4": "شبکه ۴", "shabakeye 5": "شبکه ۵", "khabar": "شبکه خبر",
    "varzesh tv live": "شبکه ورزش", "ofogh": "شبکه افق", "nasim": "شبکه نسیم", "ifilm tv persian": "آی‌فیلم",
    "voa persian": "صدای آمریکا", "radio javan tv": "رادیو جوان", "iran international tv": "ایران اینترنشنال",
    "mbc persia": "ام‌بی‌سی پرشیا", "iran independent": "ایندیپندنت فارسی", "manoto tv": "من و تو",
    "gem tv sport": "جم اسپورت", "gem tv": "جم تی‌وی", "gem series": "جم سریال", "gem film": "جم فیلم",
    "gem drama": "جم درام", "gem kids": "جم کیدز", "gem classic": "جم کلاسیک",
    "afghanistan international tv": "افغانستان اینترنشنال", "afghanistan international": "افغانستان اینترنشنال",
    "tarafdari tv live": "طرفداری", "pmc royale": "پی‌ام‌سی رویال", "pmc": "پی‌ام‌سی",
    "persiana tv": "پرشیانا", "persiana plus": "پرشیانا پلاس", "persiana comedy": "پرشیانا کمدی",
    "persiana family": "پرشیانا فمیلی", "persiana nostalgia": "پرشیانا نوستالژی",
    "persiana iranian": "پرشیانا ایرانیان", "persiana documentary": "پرشیانا مستند",
    "persiana junior": "پرشیانا جونیور", "persiana cinema": "پرشیانا سینما",
    "ava series": "آوا سریال", "tapesh tv 2": "تپش ۲", "navahang tv": "نواهنگ", "pars tv": "پارس",
    "tuti tv": "توتی", "oxir tv": "اکسیر", "livefarsi tv": "لایوفارسی", "payame afghan": "پیام افغان",
    "kayhan afghan": "کیهان افغان", "ganje hozoor": "گنج حضور", "kalemeh tv": "کلمه",
    "payvand tv": "پیوند", "omid e iran tv": "امید ایران", "radio farda": "رادیو فردا",
    "irane farda tv": "ایران فردا", "solh tv": "صلح", "arab varzeshi hd": "ورزشی عربی",
}


def display_name(name):
    key = re.sub(r"\s+", " ", name.lower()).strip()
    if key in NAME_FA:
        return NAME_FA[key]
    return re.sub(r"\s+(new|asli|live)$", "", name.strip(), flags=re.I)


_last = [0.0]


def get(url, timeout=25, referer=None):
    wait = PAUSE - (time.time() - _last[0])
    if wait > 0:
        time.sleep(wait)
    headers = {"User-Agent": UA, "Accept": "*/*"}
    if referer:
        headers["Referer"] = referer
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read(200_000).decode("utf-8", "replace")
    finally:
        _last[0] = time.time()


CARD = re.compile(r'<a href="https://www\.livefarsi\.com/live-tv/([a-z0-9-]+)\.html">\s*'
                  r'<img[^>]*src="([^"]+)"[^>]*alt="([^"]*)"')
SOURCE = re.compile(r'<source src="([^"]+\.m3u8[^"]*)"|<source src="([^"]+)" type="application/(?:vnd\.apple\.mpegurl|x-mpegurl)"', re.I)


def channel_cards(page):
    out = {}
    for slug, img, name in CARD.findall(page):
        if slug.startswith("category") or slug in out:
            continue
        out[slug] = {"slug": slug, "name": html.unescape(name).strip(), "logo": img}
    return out


def stream_of(slug):
    page = get(f"{SITE}/live-tv/{slug}.html")
    m = SOURCE.search(page)
    if not m:
        return None
    return html.unescape(m.group(1) or m.group(2))


def works(url):
    """A stream works when its playlist answers and lists segments or variant playlists."""
    try:
        body = get(url, timeout=15, referer=SITE + "/")
    except Exception:
        return False
    return body.lstrip().startswith("#EXTM3U") and ("#EXTINF" in body or "#EXT-X-STREAM-INF" in body)


def main():
    channels = channel_cards(get(f"{SITE}/live-tv.html"))
    print(f"{len(channels)} channels listed")
    for cat in CATEGORIES:
        try:
            for slug in channel_cards(get(f"{SITE}/live-tv/category/{cat}.html")):
                if slug in channels:
                    channels[slug].setdefault("cats", []).append(cat)
        except Exception as e:
            print(f"category {cat}: {e}", file=sys.stderr)

    live = []
    for i, ch in enumerate(channels.values()):
        try:
            url = stream_of(ch["slug"])
        except Exception as e:
            print(f"  {ch['slug']}: page failed ({e})", file=sys.stderr)
            continue
        ok = bool(url) and works(url)
        print(f"  {'ok  ' if ok else 'DOWN'} {ch['name']}")
        if ok:
            ch["url"] = url
            ch["cats"] = sorted(set(ch.get("cats", [])), key=lambda c: CATEGORIES[c][1])
            live.append(ch)

    # One card per channel: copies with the same name become backup streams of the first.
    merged, by_name = [], {}
    order = {slug: n for n, slug in enumerate(channels)}  # the site's own order
    live.sort(key=lambda c: order[c["slug"]])
    for c in live:
        name = display_name(c["name"])
        if name in by_name:
            first = by_name[name]
            first["urls"].append(c["url"])
            first["cats"] = sorted(set(first["cats"]) | set(c["cats"]), key=lambda k: CATEGORIES[k][1])
            continue
        c = dict(c, name=name, urls=[c["url"]])
        by_name[name] = c
        merged.append(c)
    live = merged

    if len(live) < 10:  # the site or its streams are having a bad hour: keep the last good list
        print(f"only {len(live)} channels work; keeping the previous live.json", file=sys.stderr)
        return
    out = {
        "v": 1,
        "site": SITE,
        "categories": [{"id": k, "name": v[0]} for k, v in sorted(CATEGORIES.items(), key=lambda kv: kv[1][1])
                       if any(k in c["cats"] for c in live)],
        "channels": [{"slug": c["slug"], "name": c["name"], "logo": c["logo"], "cats": c["cats"], "urls": c["urls"]}
                     for c in live],
        "checked": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    print(f"live.json: {len(live)} of {len(channels)} channels work")


if __name__ == "__main__":
    main()
