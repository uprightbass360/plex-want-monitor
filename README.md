# Want Monitor

A small dashboard that watches a Plex Friends' Watchlist RSS feed, checks your
Plex Media Server for titles you already own, and keeps a queue of what is
still missing. It is a FastAPI app with a SQLite database, served on one port.

## Run

```bash
mkdir -p data            # before the first "up", see the note below
docker compose up -d --build
```

The `./data` bind mount must be owned by uid 1000, which is the `app` user in
the image. If Docker creates `./data` for you it will be owned by root and the
app cannot write to it. Run `mkdir -p data` as your own user first (uid 1000 on
most single-user hosts), or `chown 1000:1000 data` afterwards.

## First run

1. Open `http://host:8787/settings`.
2. In Plex Web, open your settings and copy the Friends' Watchlist RSS URL.
   Paste it into the RSS URL field of the Watchlist feed card and press Save
   (Test checks it first).
3. In the Plex connection card, press Connect to Plex. Go to plex.tv/link and
   enter the code shown. The card updates itself once you authorize.
4. Pick your server from the list.
5. Tick the libraries to scan, then press Scan now (or wait for the interval).

## Statuses

| status    | meaning                                                          |
|-----------|------------------------------------------------------------------|
| `review`  | seen in the feed, not in Plex, you have not decided              |
| `queued`  | you said yes; it is on the working list                          |
| `grabbed` | you ticked it off; waiting for the Plex scan to see it           |
| `library` | the scan matched it to a Plex item                               |
| `ignored` | you said no                                                      |

## JSON endpoints

Item responses never include the Plex token or the feed URL.

- `GET /healthz`
- `GET /api/items?status=&type=&q=&author=&in_feed=` (`status` takes a comma list)
- `GET /api/items/queue.txt` (search strings, one per line)
- `POST /api/items/{id}/status` with `{"status": "queued"}`
- `POST /api/items/bulk` with `{"ids": [...], "status": "..."}`
- `POST /api/items/{id}/skip`
- `POST /api/items/{id}/not-a-match`
- `GET /api/status`
- `POST /api/feed/fetch`, `POST /api/plex/scan`
- `POST /api/plex/pin`, `GET /api/plex/pin`, `GET /api/plex/servers`,
  `POST /api/plex/server`, `POST /api/plex/reconnect`, `POST /api/plex/signout`
- `GET /api/plex/libraries` (the selected server's libraries: key, type, title)
- `POST /api/friends/resolve` (look up friend names and avatars from Plex now)
- `GET /api/settings`, `PUT /api/settings` (the feed URL is masked except its
  last four characters)

## State and configuration

All state lives in `/data` (mounted from `./data`): the SQLite database
`want.db` and `plex.json`, which holds the Plex token and is written with mode
0600. Back up or delete that directory to keep or reset everything.

Environment variables: `WM_DATA_DIR` (default `/data` in the image),
`WM_PORT` (`8787`), `WM_LOG_LEVEL`, and `TZ`.
