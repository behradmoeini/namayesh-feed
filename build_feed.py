#!/usr/bin/env python3
"""Build catalog.json for the «شبکه نمایش خانگی» TV app from farsiland.com.

Every TV used to read the catalogue straight from the site: each list from the WordPress API,
each title's own page for its Persian name, and the home slider and pardehtv.com for the featured
and most-watched rows (matched by a dozen searches per TV). This does all of that once, on a
schedule, and writes one file the app reads instead. Only what a viewer opens stays live:
the synopsis, a show's episode list and the video link.

State between runs is state.json (every title with its Persian name, so a page is read only once).

  python3 build_feed.py                 # incremental: newest and recently edited titles
  python3 build_feed.py --full          # re-read the whole catalogue (drops deleted titles)
  python3 build_feed.py --fa-budget 400 # at most this many title pages for Persian names

Standard library only. Requests are sequential with a pause between them.
"""
import argparse
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

SITE = "https://farsiland.com"
API = SITE + "/wp-json/wp/v2/"
UPLOADS = SITE + "/wp-content/uploads/"
TRENDING_SITE = "https://pardehtv.com/"
UA = "NamayeshKhanegiFeed/1.0 (+catalogue for the TV app, by agreement with farsiland.com)"
TYPES = ("movies", "tvshows")
FULL_EVERY = 24 * 3600  # seconds between full re-reads, so deleted titles drop out
PAUSE = 0.6  # seconds between requests

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "state.json")
OUT = os.path.join(HERE, "catalog.json")

_last = [0.0]


def get(url, want_headers=False):
    wait = PAUSE - (time.time() - _last[0])
    if wait > 0:
        time.sleep(wait)
    err = None
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
            with urllib.request.urlopen(req, timeout=30) as r:
                body = r.read().decode("utf-8", "replace")
                _last[0] = time.time()
                return (body, r.headers) if want_headers else body
        except urllib.error.HTTPError as e:
            _last[0] = time.time()
            if e.code == 400 and "page=" in url:  # WordPress: page past the end
                return ("[]", e.headers) if want_headers else "[]"
            if e.code == 404:
                raise
            err = e
        except Exception as e:  # network
            _last[0] = time.time()
            err = e
        time.sleep(3 * (attempt + 1))
    raise err


def text(s):
    s = re.sub(r"<[^>]+>", " ", s or "")
    return re.sub(r"\s+", " ", html.unescape(s)).replace("￼", " ").strip()


PERSIAN = re.compile(r"[؀-ۿ]")


def poster(o):
    try:
        m = o["_embedded"]["wp:featuredmedia"][0]
        sizes = (m.get("media_details") or {}).get("sizes") or {}
        for k in ("full", "medium", "dt_poster_a"):
            if sizes.get(k, {}).get("source_url"):
                return sizes[k]["source_url"]
        return m.get("source_url") or None
    except Exception:
        return None


def short(url):
    """Poster URLs share one long prefix; the app puts it back."""
    if url and url.startswith(UPLOADS):
        return url[len(UPLOADS):]
    return url


def read_pages(kind, extra, max_pages):
    out = []
    page, total = 1, 1
    while page <= min(total, max_pages):
        url = (f"{API}{kind}?per_page=100&page={page}&_embed=wp:featuredmedia"
               f"&_fields=id,slug,title,link,date,genres,_links.wp:featuredmedia,_embedded{extra}")
        body, hdr = get(url, want_headers=True)
        try:
            total = int(hdr.get("X-WP-TotalPages") or total)
        except ValueError:
            pass
        arr = json.loads(body)
        if not arr:
            break
        for o in arr:
            out.append({
                "id": o["id"],
                "s": o.get("slug", ""),
                "t": text((o.get("title") or {}).get("rendered", "")),
                "p": short(poster(o)),
                "g": o.get("genres") or [],
                "d": o.get("date", ""),
                "l": o.get("link", ""),
            })
        page += 1
    return out


ORIGINAL = re.compile(r'Original title</b>\s*<span class="valor[^"]*">([^<]+)<')


def fa_title(link):
    try:
        m = ORIGINAL.search(get(link))
    except urllib.error.HTTPError:
        return ""
    fa = text(m.group(1)) if m else ""
    return fa if PERSIAN.search(fa) else ""


# ------------------------------------------------------------------ featured (home slider)

SLIDE = re.compile(r'class="item homeslider"[^>]*>.*?(?:data-bg="|url\()(https?:[^")]+)[")]'
                   r'.*?<h3 class="title">([^<]+)</h3>.*?item_type">\s*([^<]*?)\s*<', re.S)
EP_SUFFIX = re.compile(r"\s+(?:SE?\s*\d+\s+)?EP?\s*\d+\s*$", re.I)


def slugify(name):
    return re.sub(r"^-|-$", "", re.sub(r"[^a-z0-9]+", "-", name.lower()))


def find(items, name):
    slug = slugify(name)
    for it in items:
        if it["s"] == slug:
            return it
    low = name.lower()
    for it in items:
        if it["t"].lower() == low:
            return it
    for it in items:
        if low in it["t"].lower():
            return it
    return None


def featured(state):
    out = []
    page = get(SITE + "/home")
    for m in SLIDE.finditer(page):
        if len(out) >= 10:
            break
        kind = "movies" if "movie" in m.group(3).lower() else "tvshows"
        name = EP_SUFFIX.sub("", text(m.group(2))).strip()
        it = find(state[kind], name)
        if it and all(o["id"] != it["id"] for o in out):
            out.append({"id": it["id"], "type": kind, "b": short(m.group(1))})
    return out


# ------------------------------------------------------------------ most watched (pardehtv.com)

TREND = re.compile(r'data-type="(\w+)">\s*<a class="card-link" href="[^"]*" aria-label="([^"]*)"')
PAREN = re.compile(r"\(([^()]*)\)\s*$")


def skeleton(s):
    s = s.lower().replace("q", "k")
    s = re.sub(r"[^a-z0-9]|[aeiouy]", "", s)
    return re.sub(r"(.)\1+", r"\1", s)


def plain(s):
    s = s.replace("ي", "ی").replace("ك", "ک")
    s = re.sub("[۱1]", "یک", s)
    s = re.sub("[۲2]", "دو", s)
    s = re.sub("[۳3]", "سه", s)
    return re.sub("[\\s‌‏‎ٔ]", "", s)


def trending(state):
    page = get(TRENDING_SITE)
    a = page.find('id="trending-track"')
    if a < 0:
        return []
    b = page.find('class="rail-track"', a + 20)
    if b < 0:
        b = min(len(page), a + 80000)
    out = []
    for m in TREND.finditer(page[a:b]):
        if len(out) >= 15:
            break
        kind = "tvshows" if m.group(1) == "series" else "movies"
        label, fa = text(m.group(2)), None
        p = PAREN.search(label)
        if p and PERSIAN.search(p.group(1)):
            fa = p.group(1).strip()
            label = label[:p.start()].strip()
        keys = {skeleton(n) for n in re.split(r"\s*/\s*", label) if n}
        want = plain(fa) if fa else None
        hit = weak = None
        for it in state[kind]:  # newest first
            if skeleton(it["t"]) not in keys:
                continue
            if want is None or plain(it.get("fa", "")) == want:
                hit = it
                break
            if not it.get("fa") and weak is None:
                weak = it
        if hit is None:
            hit = weak
        if hit is None and want:  # Latin spellings too far apart: go by the Persian title
            hit = next((it for it in state[kind] if it.get("fa") and plain(it["fa"]) == want), None)
        if hit and all(o["id"] != hit["id"] for o in out):
            out.append({"id": hit["id"], "type": kind})
    return out


# ------------------------------------------------------------------ main

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--full", action="store_true")
    ap.add_argument("--fa-budget", type=int, default=400)
    args = ap.parse_args()

    state = {"movies": [], "tvshows": [], "full_at": 0}
    if os.path.exists(STATE):
        with open(STATE, encoding="utf-8") as f:
            state.update(json.load(f))
    full = args.full or not state["movies"] or time.time() - state.get("full_at", 0) > FULL_EVERY

    for kind in TYPES:
        old = {it["id"]: it for it in state[kind]}
        if full:
            fresh = read_pages(kind, "", 1000)
            if len(fresh) < 0.8 * len(old):  # the site answered with far less than before: keep what we had
                print(f"{kind}: full read gave {len(fresh)} of {len(old)}, keeping old list", file=sys.stderr)
                fresh = list(old.values())
        else:
            # Newest 200 by date, and the 100 most recently edited (a changed title or poster).
            fresh = read_pages(kind, "", 2) + read_pages(kind, "&orderby=modified", 1)
            seen = {it["id"] for it in fresh}
            fresh += [it for it in old.values() if it["id"] not in seen]
        merged = {}
        for it in fresh:
            if it["id"] in merged:
                continue
            prev = old.get(it["id"])
            if prev and "fa" in prev and prev.get("l") == it.get("l"):
                it["fa"] = prev["fa"]
            merged[it["id"]] = it
        state[kind] = sorted(merged.values(), key=lambda x: x["d"], reverse=True)
        print(f"{kind}: {len(state[kind])} titles ({'full' if full else 'incremental'})")
    if full:
        state["full_at"] = time.time()

    # Persian names: newest first, a bounded number per run.
    budget = args.fa_budget
    for kind in TYPES:
        for it in state[kind]:
            if budget <= 0:
                break
            if "fa" not in it and it.get("l"):
                it["fa"] = fa_title(it["l"])
                budget -= 1
                if (args.fa_budget - budget) % 100 == 0:
                    print(f"  Persian names: {args.fa_budget - budget} read")
                    save(STATE, state)  # keep progress if the run is stopped
    missing = sum(1 for k in TYPES for it in state[k] if "fa" not in it)
    print(f"Persian names still to read: {missing}")

    genres = []
    try:
        for g in json.loads(get(API + "genres?per_page=100&orderby=count&order=desc&_fields=id,name,count")):
            genres.append({"id": g["id"], "n": text(g.get("name", "")), "c": g.get("count", 0)})
    except Exception as e:
        print(f"genres: {e}", file=sys.stderr)
        genres = state.get("genres", [])
    state["genres"] = genres

    for name, fn in (("featured", featured), ("trending", trending)):
        try:
            rows = fn(state)
            if rows or not state.get(name):
                state[name] = rows
        except Exception as e:
            print(f"{name}: {e}, keeping the last list", file=sys.stderr)
        print(f"{name}: {len(state.get(name, []))}")

    save(STATE, state)

    def slim(it):
        o = {"id": it["id"], "s": it["s"], "t": it["t"], "g": it["g"]}
        if "fa" in it:  # "" = the site has none; absent = not read yet (the app then reads the page)
            o["fa"] = it["fa"]
        if it.get("p"):
            o["p"] = it["p"]
        return o

    catalog = {
        "v": 1,
        "site": SITE,
        "uploads": UPLOADS,
        "movies": [slim(it) for it in state["movies"]],
        "tvshows": [slim(it) for it in state["tvshows"]],
        "genres": state["genres"],
        "featured": state.get("featured", []),
        "trending": state.get("trending", []),
    }
    body = json.dumps(catalog, ensure_ascii=False, separators=(",", ":"))
    old = None
    if os.path.exists(OUT):
        with open(OUT, encoding="utf-8") as f:
            old = json.load(f)
    if old and {k: v for k, v in old.items() if k != "built"} == catalog:
        print("catalog.json unchanged")
        return
    catalog["built"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(json.dumps(catalog, ensure_ascii=False, separators=(",", ":")))
    print(f"catalog.json written: {len(body) // 1024} KB")


def save(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, path)


if __name__ == "__main__":
    main()
