"""
steam_table.py
--------------
Generates HTML pages with a table of Steam games, their store links,
and embedded trailers played via MPEG-DASH (dash.js).

Uses async Playwright to process multiple games concurrently (default: 10 at a time),
and splits output into files of 100 games each named rando_bin_1.html, rando_bin_2.html, etc.

Setup (one time):
    pip install playwright requests
    playwright install chromium

Usage:
    python steam_table.py                         # uses hardcoded appids list below
    python steam_table.py 405006 1091500 292030   # pass appids as CLI args
"""

import asyncio
import collections
import random
import sys
import time
from pathlib import Path

import requests
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

CONCURRENCY    = 10   # number of games to process in parallel
CHUNK_SIZE     = 100  # games per output HTML file
OUTPUT_PREFIX  = "rando_bin"

# How long (seconds) to wait for an MPD request to appear after clicking play
MPD_WAIT_SECONDS = 15

# Cookies to bypass age gate and mature content warnings
STEAM_COOKIES = [
    {"name": "birthtime",       "value": "0",        "domain": ".steampowered.com", "path": "/"},
    {"name": "mature_content",  "value": "1",        "domain": ".steampowered.com", "path": "/"},
    {"name": "lastagecheckage", "value": "1-0-1990", "domain": ".steampowered.com", "path": "/"},
]


# ---------------------------------------------------------------------------
# Rate limiter — sliding window, thread-safe via asyncio lock
# ---------------------------------------------------------------------------

class RateLimiter:
    """
    Sliding-window rate limiter.

    Allows at most `max_calls` calls within any `period` second window.
    Callers await `acquire()` which blocks until a slot is available,
    then logs a message if it has to wait so you know what's happening.
    """

    def __init__(self, max_calls: int, period: float):
        self.max_calls = max_calls
        self.period    = period
        self.timestamps: collections.deque = collections.deque()
        self.lock = asyncio.Lock()

    async def acquire(self):
        async with self.lock:
            now = time.monotonic()

            # Drop timestamps that have fallen outside the window
            while self.timestamps and now - self.timestamps[0] >= self.period:
                self.timestamps.popleft()

            if len(self.timestamps) >= self.max_calls:
                # Oldest timestamp tells us when a slot opens up
                wait_until = self.timestamps[0] + self.period
                wait_for   = wait_until - now
                print(f"  [rate limiter] API limit reached — waiting {wait_for:.1f}s "
                      f"for next window...", file=sys.stderr)
                await asyncio.sleep(wait_for)

                # Purge again after sleeping
                now = time.monotonic()
                while self.timestamps and now - self.timestamps[0] >= self.period:
                    self.timestamps.popleft()

            self.timestamps.append(time.monotonic())


# One shared limiter: 195 requests per 300 seconds (a little headroom under the 200 limit)
api_limiter = RateLimiter(max_calls=195, period=300)




def get_steam_app_data(appid: int, retries: int = 5) -> dict | None:
    """Fetch basic app metadata (name, header image) from the Steam Store API.
    
    Retries with exponential backoff on failure to handle rate limiting.
    """
    url = f"https://store.steampowered.com/api/appdetails?appids={appid}"
    for attempt in range(1, retries + 1):
        try:
            resp = requests.get(url, timeout=15)
            resp.raise_for_status()
            entry = resp.json().get(str(appid), {})
            if entry.get("success"):
                return entry["data"]
            # success=false often means rate limited — retry
            wait = 2 ** attempt
            print(f"  [!] API success=false for appid {appid} "
                  f"(attempt {attempt}/{retries}, retrying in {wait}s)", file=sys.stderr)
            time.sleep(wait)
        except Exception as e:
            wait = 2 ** attempt
            print(f"  [!] API error for appid {appid}: {e} "
                  f"(attempt {attempt}/{retries}, retrying in {wait}s)", file=sys.stderr)
            time.sleep(wait)
    print(f"  [!] Giving up on appid {appid} after {retries} attempts", file=sys.stderr)
    return None


# ---------------------------------------------------------------------------
# Async Playwright: intercept the MPD URL
# ---------------------------------------------------------------------------

async def get_mpd_url_via_browser(context, appid: int) -> str | None:
    """
    Open a new page in the given browser context, navigate to the Steam store
    page for `appid`, click play, and intercept the .mpd network request.

    Returns the MPD URL string, or None if not found within the timeout.
    """
    mpd_url = None
    page = await context.new_page()

    try:
        def handle_request(request):
            nonlocal mpd_url
            if mpd_url is None and ".mpd" in request.url:
                mpd_url = request.url
                print(f"  [{appid}] Intercepted MPD: {mpd_url}")

        page.on("request", handle_request)

        store_url = f"https://store.steampowered.com/app/{appid}/"
        print(f"  [{appid}] Navigating...")
        await page.goto(store_url, wait_until="domcontentloaded", timeout=30_000)

        # Dismiss any cookie/age-gate banners
        for selector in [
            "#cookieAgreementPopup .btn_medium",
            ".agegate_text_container .btn_medium",
            "#age_gate_btn_continue",
        ]:
            try:
                btn = page.locator(selector).first
                if await btn.is_visible(timeout=2_000):
                    await btn.click()
            except Exception:
                pass

        # Click the trailer play button
        play_selectors = [
            "[data-trailer-player] .vXKdKnTS2vXaw2YxC0Yc1",
            "[data-trailer-player]",
        ]
        clicked = False
        for selector in play_selectors:
            try:
                el = page.locator(selector).first
                await el.wait_for(state="visible", timeout=8_000)
                await el.click()
                print(f"  [{appid}] Clicked play")
                clicked = True
                break
            except Exception:
                continue

        if not clicked:
            print(f"  [{appid}] [!] Could not find/click play button", file=sys.stderr)

        # Wait for the MPD request to be intercepted
        deadline = time.time() + MPD_WAIT_SECONDS
        while mpd_url is None and time.time() < deadline:
            await asyncio.sleep(0.25)

        if mpd_url is None:
            print(f"  [{appid}] [!] No MPD URL intercepted within {MPD_WAIT_SECONDS}s",
                  file=sys.stderr)

    except PlaywrightTimeout as e:
        print(f"  [{appid}] [!] Timeout: {e}", file=sys.stderr)
    except Exception as e:
        print(f"  [{appid}] [!] Error: {e}", file=sys.stderr)
    finally:
        await page.close()

    return mpd_url


# ---------------------------------------------------------------------------
# Worker: processes a single appid
# ---------------------------------------------------------------------------

async def process_appid(semaphore, context, appid: int, idx: int) -> dict:
    async with semaphore:
        print(f"\nProcessing appid {appid}...")

        await asyncio.sleep(random.uniform(0, 2))

        await api_limiter.acquire()
        api_data = await asyncio.to_thread(get_steam_app_data, appid)
        name         = api_data.get("name", f"App {appid}") if api_data else f"App {appid}"
        header_image = api_data.get("header_image", "")      if api_data else ""
        store_url    = f"https://store.steampowered.com/app/{appid}/"

        mpd_url = await get_mpd_url_via_browser(context, appid)

        return {
            "idx":          idx,
            "appid":        appid,
            "name":         name,
            "store_url":    store_url,
            "header_image": header_image,
            "mpd_url":      mpd_url,
        }


# ---------------------------------------------------------------------------
# HTML generation
# ---------------------------------------------------------------------------

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Steam Games — {title}</title>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/dashjs/4.7.4/dash.all.min.js"></script>
    <style>
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{
            font-family: 'Segoe UI', Arial, sans-serif;
            background: #1b2838;
            color: #c6d4df;
            padding: 24px;
        }}
        h1 {{
            color: #66c0f4;
            text-align: center;
            margin-bottom: 28px;
            font-size: 1.8rem;
            letter-spacing: 0.04em;
        }}
        table {{
            width: auto;
            margin: 0 auto;
            border-collapse: collapse;
            background: #16202d;
            border-radius: 10px;
            overflow: hidden;
            box-shadow: 0 6px 24px rgba(0,0,0,0.5);
        }}
        thead {{ background: #1e3a5f; }}
        th {{
            padding: 14px 20px;
            text-align: left;
            font-size: 0.85rem;
            color: #66c0f4;
            text-transform: uppercase;
            letter-spacing: 0.08em;
        }}
        td {{
            padding: 18px 20px;
            border-bottom: 1px solid #2a3f5f;
            vertical-align: middle;
        }}
        tr:last-child td {{ border-bottom: none; }}
        tr:hover {{ background: #1a2e47; }}
        .widget-cell {{ padding: 12px 20px; vertical-align: middle; }}
        .widget-cell iframe {{ display: block; }}
        .video-wrapper video {{
            display: block;
            border-radius: 6px;
            background: #000;
            max-width: 100%;
        }}
        .no-trailer {{ color: #7a8fa8; font-style: italic; }}
        .error-msg {{ color: #e06c6c; font-size: 0.85rem; }}
    </style>
</head>
<body>
    <h1>🎮 Steam Games</h1>
    <table>
        <thead>
            <tr><th>Store</th><th>Trailer</th></tr>
        </thead>
        <tbody>
            {rows}
        </tbody>
    </table>
    <script>
    document.addEventListener('DOMContentLoaded', function () {{
        document.querySelectorAll('video[data-mpd]').forEach(function (videoEl) {{
            var mpdUrl = videoEl.getAttribute('data-mpd');
            if (!mpdUrl) return;

            var player = null;
            var ready = false;
            var pendingPlay = false;

            function initPlayer() {{
                if (player) return;
                try {{
                    player = dashjs.MediaPlayer().create();
                    player.initialize(videoEl, mpdUrl, false);
                    player.updateSettings({{
                        streaming: {{ abr: {{ autoSwitchBitrate: {{ video: true }} }} }}
                    }});
                    player.on(dashjs.MediaPlayer.events.CAN_PLAY, function () {{
                        ready = true;
                        if (pendingPlay) {{
                            pendingPlay = false;
                            videoEl.play();
                        }}
                    }});
                }} catch (err) {{
                    videoEl.parentElement.innerHTML =
                        '<span class="error-msg">Could not initialise DASH player: ' + err.message + '</span>';
                }}
            }}

            videoEl.addEventListener('mouseenter', function () {{
                if (!player) {{
                    pendingPlay = true;
                    initPlayer();
                }} else if (ready) {{
                    videoEl.play();
                }} else {{
                    pendingPlay = true;
                }}
            }});

            videoEl.addEventListener('mouseleave', function () {{
                pendingPlay = false;
                videoEl.pause();
            }});
        }});
    }});
    </script>
</body>
</html>
"""


def build_html(results: list[dict], title: str) -> str:
    rows = []
    for r in results:
        if r["mpd_url"]:
            video_cell = f"""
                <div class="video-wrapper">
                    <video
                        id="video-{r['appid']}"
                        data-mpd="{r['mpd_url']}"
                        poster="{r['header_image']}"
                        muted
                        controls
                        width="960"
                        preload="none">
                    </video>
                </div>"""
        else:
            video_cell = "<em class='no-trailer'>No trailer available</em>"

        rows.append(f"""
        <tr>
            <td class="widget-cell">
                <iframe src="https://store.steampowered.com/widget/{r['appid']}/"
                    frameborder="0" width="646" height="190">
                </iframe>
            </td>
            <td class="trailer-cell">
                {video_cell}
            </td>
        </tr>""")

    return HTML_TEMPLATE.format(title=title, rows="".join(rows))


# ---------------------------------------------------------------------------
# Main async entry point
# ---------------------------------------------------------------------------

async def main(appids: list[int]):
    # Split into chunks
    chunks = [appids[i:i + CHUNK_SIZE] for i in range(0, len(appids), CHUNK_SIZE)]
    total_chunks = len(chunks)
    print(f"Processing {len(appids)} appids → {total_chunks} file(s) of up to {CHUNK_SIZE} each\n")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            ),
        )
        await context.add_cookies(STEAM_COOKIES)

        semaphore = asyncio.Semaphore(CONCURRENCY)

        for i, batch in enumerate(chunks, start=1):
            output_file = f"{OUTPUT_PREFIX}_{i}.html"
            print(f"\n{'='*60}")
            print(f"Batch {i}/{total_chunks} → {output_file} ({len(batch)} games)")
            print(f"{'='*60}")

            # Process all games in this batch concurrently (up to CONCURRENCY at once)
            tasks = [process_appid(semaphore, context, appid, idx)
                     for idx, appid in enumerate(batch)]
            results = await asyncio.gather(*tasks)
            results = sorted(results, key=lambda r: r["idx"])

            html = build_html(results, title=output_file)
            Path(output_file).write_text(html, encoding="utf-8")

            found = sum(1 for r in results if r["mpd_url"])
            print(f"\n✅ Written: {output_file} ({found}/{len(results)} trailers found)")

        await browser.close()

    print(f"\n🎉 All done! {total_chunks} file(s) generated.")


def load_appids_from_file(path: Path) -> list[int]:
    """
    Read appids from a .txt file. Handles:
      - comma-delimited (one or many per line, trailing commas OK)
      - space-delimited (one or many per line)
      - one appid per line
      - any mix of the above
    """
    appids = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            # Replace commas with spaces, then split on whitespace
            tokens = line.replace(",", " ").split()
            for token in tokens:
                token = token.strip()
                if token.isdigit():
                    appids.append(int(token))
                elif token:
                    print(f"  [!] Ignoring non-numeric token in file: {token!r}", file=sys.stderr)
    return appids


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # Priority order: CLI args > steam_appids.txt > hardcoded list
    HARDCODED_APPIDS = [
        4050060,
        # 1091500,   # Cyberpunk 2077
        # 292030,    # The Witcher 3
        # 1245620,   # Elden Ring
    ]

    appids_file = Path(__file__).parent / "steam_appids.txt"

    if len(sys.argv) > 1:
        appids = [int(a) for a in sys.argv[1:]]
        print(f"Using {len(appids)} appid(s) from command-line arguments.")
    elif appids_file.exists():
        appids = load_appids_from_file(appids_file)
        print(f"Using {len(appids)} appid(s) from {appids_file}.")
    else:
        appids = HARDCODED_APPIDS
        print(f"Using {len(appids)} hardcoded appid(s).")

    if not appids:
        print("No appids found. Provide CLI args, a steam_appids.txt file, or edit HARDCODED_APPIDS.")
        sys.exit(1)

    asyncio.run(main(appids))