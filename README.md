# namayesh-feed

The catalogue for the «شبکه نمایش خانگی» Android TV app (`alphadl-tv/namayesh`), built from
farsiland.com by agreement with its owner.

The app used to read everything from the site on every TV: each list from the WordPress API,
each title's own page for its Persian name, and a dozen searches per TV to match the featured and
most-watched rows. Now this repository does that once an hour and the app reads one file:

```
https://raw.githubusercontent.com/behradmoeini/namayesh-feed/main/catalog.json
```

What stays live in the app is only what a viewer opens: a title's synopsis, a show's episode list
and the video link. If the file can't be had and the TV has no copy, the app reads the site as before.

## Files

| File | What it is |
| --- | --- |
| `build_feed.py` | The builder. Standard library only; requests are one at a time with a pause between them. |
| `catalog.json` | What the app reads (about 400 KB). |
| `state.json` | The builder's memory between runs: every title with its Persian name, so a title's page is read only once. |
| `.github/workflows/build.yml` | Runs the builder every hour at :17 and commits when something changed. |

## catalog.json

```
{
  "v": 1,                         format version; the app ignores a file with another
  "site": "https://farsiland.com",
  "uploads": "https://farsiland.com/wp-content/uploads/",   prefix of short poster paths
  "movies":  [ {"id", "s" slug, "t" Latin title, "fa" Persian title, "p" poster, "g" [genre ids]} ],
  "tvshows": [ same ],            both newest first, like the site's own lists
  "genres":  [ {"id", "n" name, "c" count} ],
  "featured":[ {"id", "type", "b" wide cover} ],   the site's home slider
  "trending":[ {"id", "type"} ],                   most watched, from pardehtv.com (same team)
  "built": "2026-11-26T23:00Z"
}
```

`"fa": ""` means the title's page has no Persian name. No `fa` key means it hasn't been read yet,
and the app then reads that one page itself.

## Runs

- **Hourly:** the newest 200 titles of each kind and the 100 most recently edited, plus at most 300
  Persian names not yet read, the genres, the home slider and the most-watched list.
- **Daily (first run after 24 h):** the whole catalogue again, about 40 requests, so deleted titles
  drop out. If the site answers with far fewer titles than before, the old list is kept.

By hand:

```bash
python3 build_feed.py                 # what the hourly run does
python3 build_feed.py --full          # read the whole catalogue now
python3 build_feed.py --fa-budget 0   # no title pages
```
