import os
import time
import threading
import logging
import json
import queue
import requests
from datetime import datetime
from flask import Flask, render_template, jsonify, request, Response, stream_with_context
from plexapi.server import PlexServer

app = Flask(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
CONFIG_FILE = "/data/config.json"

CONFIG_DEFAULTS = {
    "PLEX_URL":               "",
    "PLEX_TOKEN":             "",
    "PLEX_TV_LIBRARY":        "TV Shows",
    "SONARR_URL":             "",
    "SONARR_API_KEY":         "",
    "SONARR_ROOT_FOLDER":     "/tv",
    "SONARR_QUALITY_PROFILE": "HD-1080p",
    "TMDB_API_KEY":           "",
    "POLL_INTERVAL":          30,
    "SETUP_COMPLETE":         False,
    "INITIAL_SCAN_DONE":      False,
}

def load_config():
    cfg = dict(CONFIG_DEFAULTS)
    try:
        os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
        with open(CONFIG_FILE) as f:
            saved = json.load(f)
            cfg.update(saved)
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return cfg

def save_config_to_disk(cfg):
    try:
        os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
        with open(CONFIG_FILE, "w") as f:
            json.dump(cfg, f, indent=2)
    except Exception as e:
        logger.warning(f"Could not save config: {e}")

CONFIG = load_config()

# ── State ─────────────────────────────────────────────────────────────────────
log_queue    = queue.Queue()
log_history  = []           # persists across page refreshes (capped at 2000)
LOG_HISTORY_MAX = 2000

scan_results = []
scan_complete = False
scan_approved = False
watchdog_running = False
watchdog_thread = None
first_run = True

# ── Logging ───────────────────────────────────────────────────────────────────
class QueueHandler(logging.Handler):
    def emit(self, record):
        msg   = self.format(record)
        level = record.levelname
        entry = {"time": datetime.now().strftime("%H:%M:%S"), "level": level, "msg": msg}
        log_queue.put(entry)
        log_history.append(entry)
        if len(log_history) > LOG_HISTORY_MAX:
            log_history.pop(0)

logger = logging.getLogger("watchdog")
logger.setLevel(logging.DEBUG)
qh = QueueHandler()
qh.setFormatter(logging.Formatter("%(message)s"))
logger.addHandler(qh)
sh = logging.StreamHandler()
sh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
logger.addHandler(sh)

# ── API helpers ────────────────────────────────────────────────────────────────
def sonarr_get(path):
    r = requests.get(f"{CONFIG['SONARR_URL']}/api/v3/{path}",
                     headers={"X-Api-Key": CONFIG["SONARR_API_KEY"]}, timeout=10)
    r.raise_for_status()
    return r.json()

def sonarr_post(path, payload):
    r = requests.post(f"{CONFIG['SONARR_URL']}/api/v3/{path}",
                      headers={"X-Api-Key": CONFIG["SONARR_API_KEY"]},
                      json=payload, timeout=10)
    r.raise_for_status()
    return r.json()

def tmdb_search(title, tvdb_id=None):
    key = CONFIG["TMDB_API_KEY"]
    if not key:
        return {}
    try:
        if tvdb_id:
            r = requests.get(
                f"https://api.themoviedb.org/3/find/{tvdb_id}",
                params={"api_key": key, "external_source": "tvdb_id"}, timeout=8)
            data = r.json()
            results = data.get("tv_results", [])
            if results:
                return results[0]
        r = requests.get("https://api.themoviedb.org/3/search/tv",
                         params={"api_key": key, "query": title}, timeout=8)
        results = r.json().get("results", [])
        return results[0] if results else {}
    except Exception as e:
        logger.warning(f"TMDB lookup failed for '{title}': {e}")
        return {}

def get_quality_profile_id():
    profiles = sonarr_get("qualityprofile")
    for p in profiles:
        if p["name"].lower() == CONFIG["SONARR_QUALITY_PROFILE"].lower():
            return p["id"]
    return profiles[0]["id"] if profiles else 1

def get_sonarr_series_map():
    series = sonarr_get("series")
    return {s["tvdbId"]: s for s in series if s.get("tvdbId")}

# Statuses considered "still airing" — monitor for new episodes
ONGOING_STATUSES = {"returning series", "in production", "planned", "pilot", "continuing"}

def get_show_status(tvdb_id, title, tmdb_id=None):
    """
    Returns (is_ongoing, status_str).
    Checks TMDB first (most reliable), falls back to Sonarr lookup data.
    """
    # Try TMDB
    key = CONFIG.get("TMDB_API_KEY", "")
    if key:
        try:
            # Use tvdb external lookup if we have it
            search_id = tmdb_id
            if not search_id and tvdb_id:
                r = requests.get(
                    f"https://api.themoviedb.org/3/find/{tvdb_id}",
                    params={"api_key": key, "external_source": "tvdb_id"}, timeout=8)
                results = r.json().get("tv_results", [])
                if results:
                    search_id = results[0]["id"]
            if search_id:
                r = requests.get(
                    f"https://api.themoviedb.org/3/tv/{search_id}",
                    params={"api_key": key}, timeout=8)
                data = r.json()
                status = data.get("status", "").lower()
                is_ongoing = status in ONGOING_STATUSES
                return is_ongoing, status
        except Exception as e:
            logger.debug(f"TMDB status check failed for '{title}': {e}")

    # Fallback: use Sonarr lookup status field
    try:
        results = sonarr_get(f"series/lookup?term=tvdb:{tvdb_id}")
        if results:
            status = results[0].get("status", "").lower()
            # Sonarr uses: continuing, ended, upcoming, deleted
            is_ongoing = status in {"continuing", "upcoming"}
            return is_ongoing, status
    except Exception as e:
        logger.debug(f"Sonarr status fallback failed for '{title}': {e}")

    # Unknown — default to monitored to be safe
    return True, "unknown"

def add_series_to_sonarr(show, quality_profile_id):
    tvdb_id = show["tvdbId"]
    title   = show["title"]
    try:
        results = sonarr_get(f"series/lookup?term=tvdb:{tvdb_id}")
        if not results:
            logger.warning(f"No Sonarr lookup result for TVDB:{tvdb_id} '{title}'")
            return False
        lookup = results[0]

        # Determine monitoring based on show status
        is_ongoing, status_str = get_show_status(tvdb_id, title, tmdb_id=show.get("tmdbId"))

        if is_ongoing:
            # Ongoing — monitor everything, Sonarr will grab new episodes as they air
            monitor_mode    = "all"
            series_monitored = True
            seasons = [dict(s, monitored=True) for s in lookup.get("seasons", [])]
            logger.info(f"  📡 '{title}' is ongoing ({status_str}) — monitoring all episodes")
        else:
            # Ended — set to none so Sonarr won't monitor or search for anything
            monitor_mode    = "none"
            series_monitored = False
            seasons = [dict(s, monitored=False) for s in lookup.get("seasons", [])]
            logger.info(f"  🏁 '{title}' has ended ({status_str}) — setting monitor to none")

        payload = {
            "title":            lookup["title"],
            "tvdbId":           tvdb_id,
            "qualityProfileId": show.get("qualityProfileId", quality_profile_id),
            "rootFolderPath":   CONFIG["SONARR_ROOT_FOLDER"],
            "seasonFolder":     True,
            "monitored":        series_monitored,
            "addOptions": {
                "searchForMissingEpisodes":     False,
                "searchForCutoffUnmetEpisodes": False,
                "monitor":                      monitor_mode,
            },
            "seasons": seasons,
            "images":  lookup.get("images", []),
            "path":    f"{CONFIG['SONARR_ROOT_FOLDER']}/{lookup['title']}",
        }
        sonarr_post("series", payload)
        status_icon = "📡" if is_ongoing else "🏁"
        logger.info(f"✅ {status_icon} Added: {title} (TVDB:{tvdb_id}) [{status_str}] monitor={monitor_mode}")
        return True
    except requests.HTTPError as e:
        if e.response.status_code == 400:
            logger.info(f"Already exists in Sonarr: {title}")
        else:
            logger.error(f"Failed to add {title}: {e}")
        return False

# ── Episode marking ──────────────────────────────────────────────────────────
def mark_episodes_downloaded(plex_show, sonarr_series_id, title):
    """Cross-reference Plex episodes against Sonarr and mark as downloaded."""
    try:
        sonarr_episodes = sonarr_get(f"episode?seriesId={sonarr_series_id}")
        existing_files  = sonarr_get(f"episodefile?seriesId={sonarr_series_id}")
        sonarr_ep_file_ids = {ef["id"] for ef in existing_files}
    except Exception as e:
        logger.error(f"  ❌ Failed to fetch Sonarr data for '{title}': {e}")
        return 0, 0

    # Build map (season, episode) -> sonarr episode
    sonarr_ep_map = {}
    for ep in sonarr_episodes:
        key = (ep["seasonNumber"], ep["episodeNumber"])
        sonarr_ep_map[key] = ep

    plex_ep_count    = 0
    already_marked   = 0
    newly_marked     = 0
    not_in_sonarr    = 0
    mark_failed      = 0

    for plex_season in plex_show.seasons():
        season_num = plex_season.seasonNumber
        if season_num == 0:
            continue  # skip specials
        for plex_ep in plex_season.episodes():
            ep_index = plex_ep.index
            plex_ep_count += 1
            key = (season_num, ep_index)

            sonarr_ep = sonarr_ep_map.get(key)
            if not sonarr_ep:
                not_in_sonarr += 1
                logger.debug(f"    ⚠ S{season_num:02}E{ep_index:02} not found in Sonarr for '{title}'")
                continue

            # Already tracked by Sonarr
            if sonarr_ep.get("hasFile") or sonarr_ep.get("episodeFileId") in sonarr_ep_file_ids:
                already_marked += 1
                continue

            # Get file path from Plex
            try:
                file_path = plex_ep.media[0].parts[0].file
            except (IndexError, AttributeError):
                logger.warning(f"    ⚠ No file path for S{season_num:02}E{ep_index:02} of '{title}'")
                mark_failed += 1
                continue

            # Tell Sonarr about this file
            try:
                payload = {
                    "name": "ManualImport",
                    "files": [{
                        "path": file_path,
                        "seriesId": sonarr_series_id,
                        "episodeIds": [sonarr_ep["id"]],
                        "quality": {"quality": {"id": 4, "name": "HDTV-1080p"}, "revision": {"version": 1}},
                        "languages": [{"id": 1, "name": "English"}]
                    }],
                    "importMode": "auto"
                }
                sonarr_post("command", payload)
                newly_marked += 1
                logger.info(f"    ✅ Marked S{season_num:02}E{ep_index:02} downloaded — {file_path}")
            except Exception as e:
                mark_failed += 1
                logger.warning(f"    ❌ Failed to mark S{season_num:02}E{ep_index:02} of '{title}': {e}")

    # Per-show summary
    if newly_marked or mark_failed:
        logger.info(
            f"  📺 '{title}' — {plex_ep_count} in Plex | "
            f"{already_marked} already in Sonarr | "
            f"{newly_marked} newly marked | "
            f"{not_in_sonarr} not in Sonarr | "
            f"{mark_failed} failed"
        )
    else:
        logger.debug(f"  ✓ '{title}' — {already_marked}/{plex_ep_count} already tracked, nothing new")

    return newly_marked, not_in_sonarr

def do_episode_index(plex, sonarr_map):
    """Index all Plex episodes and mark them as downloaded in Sonarr."""
    logger.info("📂 Starting episode index pass...")
    try:
        tv_lib = plex.library.section(CONFIG["PLEX_TV_LIBRARY"])
        shows = tv_lib.all()
    except Exception as e:
        logger.error(f"Could not load Plex library: {e}")
        return

    total_marked = 0
    total_missing = 0
    matched_shows = 0

    for show in shows:
        tvdb_id = None
        for guid in show.guids:
            if "tvdb://" in guid.id:
                try:
                    tvdb_id = int(guid.id.replace("tvdb://", ""))
                except ValueError:
                    pass

        if not tvdb_id or tvdb_id not in sonarr_map:
            continue

        # Only import episodes for ongoing shows — ended shows are already complete
        is_ongoing, status_str = get_show_status(tvdb_id, show.title)
        if not is_ongoing:
            logger.debug(f"  ⏭ Skipping episode import for ended show: {show.title} [{status_str}]")
            continue

        sonarr_series = sonarr_map[tvdb_id]
        logger.info(f"  🔎 Indexing: {show.title} [{status_str}]")
        marked, missing = mark_episodes_downloaded(show, sonarr_series["id"], show.title)
        total_marked  += marked
        total_missing += missing
        matched_shows += 1
        if marked:
            logger.info(f"    ✅ Marked {marked} new episodes as downloaded")

    logger.info(f"📂 Episode index complete — {matched_shows} shows checked, {total_marked} episodes marked, {total_missing} not found in Sonarr")

# ── Scan ──────────────────────────────────────────────────────────────────────
def do_scan():
    global scan_results, scan_complete, first_run
    logger.info("🔍 Connecting to Plex...")
    try:
        plex = PlexServer(CONFIG["PLEX_URL"], CONFIG["PLEX_TOKEN"])
        tv_lib = plex.library.section(CONFIG["PLEX_TV_LIBRARY"])
        logger.info(f"📺 Connected to Plex library: {CONFIG['PLEX_TV_LIBRARY']}")
    except Exception as e:
        logger.error(f"Plex connection failed: {e}")
        scan_complete = True
        return

    logger.info("🔍 Fetching Sonarr series list...")
    try:
        sonarr_map = get_sonarr_series_map()
        quality_profile_id = get_quality_profile_id()
        logger.info(f"📋 Found {len(sonarr_map)} series in Sonarr")
    except Exception as e:
        logger.error(f"Sonarr connection failed: {e}")
        scan_complete = True
        return

    shows = tv_lib.all()
    logger.info(f"📺 Found {len(shows)} shows in Plex — enriching with TMDB...")

    for i, show in enumerate(shows):
        tvdb_id = None
        for guid in show.guids:
            if "tvdb://" in guid.id:
                try:
                    tvdb_id = int(guid.id.replace("tvdb://", ""))
                except ValueError:
                    pass

        in_sonarr = tvdb_id in sonarr_map if tvdb_id else False
        tmdb = tmdb_search(show.title, tvdb_id)
        poster = None
        if tmdb.get("poster_path"):
            poster = f"https://image.tmdb.org/t/p/w185{tmdb['poster_path']}"
        overview = tmdb.get("overview") or show.summary or ""
        first_air = tmdb.get("first_air_date", "")
        vote = tmdb.get("vote_average", 0)

        # Get show status for monitoring logic
        tmdb_status = tmdb.get("status", "").lower()
        if tmdb_status:
            is_ongoing = tmdb_status in ONGOING_STATUSES
        else:
            is_ongoing = True  # unknown, default to monitored

        entry = {
            "id": i,
            "title": show.title,
            "tvdbId": tvdb_id,
            "tmdbId": tmdb.get("id"),
            "inSonarr": in_sonarr,
            "poster": poster,
            "overview": overview[:200] + ("..." if len(overview) > 200 else ""),
            "firstAir": first_air,
            "rating": round(vote, 1),
            "include": not in_sonarr,
            "qualityProfileId": quality_profile_id,
            "seasons": len(show.seasons()),
            "episodes": sum(len(s.episodes()) for s in show.seasons()),
            "isOngoing": is_ongoing,
            "showStatus": tmdb_status or "unknown",
        }
        scan_results.append(entry)  # append live so UI count updates
        if (i + 1) % 5 == 0:
            logger.info(f"  Processed {i+1}/{len(shows)} shows...")

    scan_results.sort(key=lambda x: (x["inSonarr"], x["title"]))
    scan_complete = True
    new_count = sum(1 for r in results if not r["inSonarr"])
    logger.info(f"✅ Scan complete. {len(results)} shows found, {new_count} not in Sonarr.")

def do_watchdog_loop():
    logger.info(f"🐕 Watchdog loop started (every {CONFIG['POLL_INTERVAL']}s)")
    while watchdog_running:
        try:
            plex = PlexServer(CONFIG["PLEX_URL"], CONFIG["PLEX_TOKEN"])
            tv_lib = plex.library.section(CONFIG["PLEX_TV_LIBRARY"])
            sonarr_map = get_sonarr_series_map()
            quality_profile_id = get_quality_profile_id()
            shows = tv_lib.all()
            for show in shows:
                tvdb_id = None
                for guid in show.guids:
                    if "tvdb://" in guid.id:
                        try:
                            tvdb_id = int(guid.id.replace("tvdb://", ""))
                        except ValueError:
                            pass
                if tvdb_id and tvdb_id not in sonarr_map:
                    logger.info(f"🆕 New show detected in Plex: {show.title}")
                    entry = {"title": show.title, "tvdbId": tvdb_id,
                             "qualityProfileId": quality_profile_id}
                    add_series_to_sonarr(entry, quality_profile_id)
            logger.info(f"💤 Sleeping {CONFIG['POLL_INTERVAL']}s...")
        except Exception as e:
            logger.error(f"Watchdog error: {e}")
        time.sleep(CONFIG["POLL_INTERVAL"])

def auto_start_watchdog():
    """On restart, auto-scan Plex for new shows and start watchdog loop."""
    global watchdog_running, watchdog_thread, first_run
    logger.info("🔄 Auto-start: previous scan detected, scanning for new shows...")
    try:
        plex = PlexServer(CONFIG["PLEX_URL"], CONFIG["PLEX_TOKEN"])
        tv_lib = plex.library.section(CONFIG["PLEX_TV_LIBRARY"])
        sonarr_map = get_sonarr_series_map()
        quality_profile_id = get_quality_profile_id()
        shows = tv_lib.all()
        added = 0
        for show in shows:
            tvdb_id = None
            for guid in show.guids:
                if "tvdb://" in guid.id:
                    try:
                        tvdb_id = int(guid.id.replace("tvdb://", ""))
                    except ValueError:
                        pass
            if not tvdb_id or tvdb_id in sonarr_map:
                continue
            logger.info(f"  🆕 New show found: {show.title}")
            entry = {"title": show.title, "tvdbId": tvdb_id, "qualityProfileId": quality_profile_id}
            add_series_to_sonarr(entry, quality_profile_id)
            added += 1
            time.sleep(0.3)
        if added:
            logger.info(f"✅ Auto-start: added {added} new shows to Sonarr")
        else:
            logger.info("✅ Auto-start: no new shows found, Sonarr is up to date")
    except Exception as e:
        logger.error(f"Auto-start scan failed: {e}")
    logger.info("🐕 Starting watchdog loop...")
    first_run = False
    watchdog_running = True
    watchdog_thread = threading.Thread(target=do_watchdog_loop, daemon=True)
    watchdog_thread.start()

# ── Routes ─────────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return render_template("index.html", config=CONFIG)

@app.route("/api/config", methods=["GET", "POST"])
def api_config():
    if request.method == "POST":
        data = request.json
        for k, v in data.items():
            if k in CONFIG:
                CONFIG[k] = v
        save_config_to_disk(CONFIG)
        return jsonify({"ok": True})
    return jsonify(CONFIG)

@app.route("/api/setup", methods=["POST"])
def api_setup():
    """Save initial setup config and mark setup complete."""
    data = request.json
    for k, v in data.items():
        if k in CONFIG:
            CONFIG[k] = v
    CONFIG["SETUP_COMPLETE"] = True
    save_config_to_disk(CONFIG)
    logger.info("✅ Setup complete. Config saved.")
    return jsonify({"ok": True})

@app.route("/api/test-connections", methods=["POST"])
def api_test_connections():
    """Test Plex and Sonarr connections with provided credentials."""
    data = request.json
    results = {"plex": False, "sonarr": False, "tmdb": False, "errors": {}}
    try:
        plex = PlexServer(data.get("PLEX_URL"), data.get("PLEX_TOKEN"))
        plex.library.section(data.get("PLEX_TV_LIBRARY", "TV Shows"))
        results["plex"] = True
    except Exception as e:
        results["errors"]["plex"] = str(e)
    try:
        r = requests.get(
            f"{data.get('SONARR_URL')}/api/v3/system/status",
            headers={"X-Api-Key": data.get("SONARR_API_KEY")}, timeout=8)
        r.raise_for_status()
        results["sonarr"] = True
    except Exception as e:
        results["errors"]["sonarr"] = str(e)
    if data.get("TMDB_API_KEY"):
        try:
            r = requests.get("https://api.themoviedb.org/3/configuration",
                             params={"api_key": data.get("TMDB_API_KEY")}, timeout=8)
            r.raise_for_status()
            results["tmdb"] = True
        except Exception as e:
            results["errors"]["tmdb"] = str(e)
    return jsonify(results)

@app.route("/api/scan", methods=["POST"])
def api_scan():
    global scan_complete, scan_approved, scan_results
    scan_complete = False
    scan_approved = False
    scan_results = []
    logger.info("🚀 Starting Plex scan...")
    t = threading.Thread(target=do_scan, daemon=True)
    t.start()
    return jsonify({"ok": True})

@app.route("/api/scan/status")
def api_scan_status():
    return jsonify({
        "complete": scan_complete,
        "approved": scan_approved,
        "count": len(scan_results),
        "results": scan_results,
    })

@app.route("/api/approve", methods=["POST"])
def api_approve():
    global scan_approved, watchdog_running, watchdog_thread, first_run
    scan_approved = True
    data = request.json or {}
    results = data.get("results", scan_results)

    def do_import():
        global watchdog_running, watchdog_thread, first_run
        logger.info("📥 Starting import of approved shows...")
        quality_profile_id = get_quality_profile_id()
        to_add = [r for r in results if r.get("include") and not r.get("inSonarr")]
        logger.info(f"📋 {len(to_add)} shows queued for import")
        for show in to_add:
            add_series_to_sonarr(show, quality_profile_id)
            time.sleep(0.5)
        logger.info("✅ Series import complete. Use 'Connect Files' on each series to link Plex files.")
        CONFIG["INITIAL_SCAN_DONE"] = True
        save_config_to_disk(CONFIG)
        logger.info("🐕 Starting watchdog loop...")
        first_run = False
        watchdog_running = True
        watchdog_thread = threading.Thread(target=do_watchdog_loop, daemon=True)
        watchdog_thread.start()

    t = threading.Thread(target=do_import, daemon=True)
    t.start()
    return jsonify({"ok": True})

@app.route("/api/series/list")
def api_series_list():
    """Return all series in Sonarr with their Plex match status."""
    try:
        sonarr_series = sonarr_get("series")
        series_list = []
        for s in sonarr_series:
            series_list.append({
                "id":       s["id"],
                "title":    s["title"],
                "tvdbId":   s.get("tvdbId"),
                "status":   s.get("status", ""),
                "monitored": s.get("monitored", False),
                "episodeCount":    s.get("statistics", {}).get("episodeCount", 0),
                "episodeFileCount": s.get("statistics", {}).get("episodeFileCount", 0),
                "seasons":  s.get("seasonCount", 0),
            })
        series_list.sort(key=lambda x: x["title"])
        return jsonify(series_list)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/series/<int:sonarr_id>/connect", methods=["POST"])
def api_series_connect(sonarr_id):
    """Connect Plex files for a single series into Sonarr."""
    def run():
        try:
            # Get series info from Sonarr
            series = sonarr_get(f"series/{sonarr_id}")
            title   = series["title"]
            tvdb_id = series.get("tvdbId")
            logger.info(f"🔗 Connecting files for: {title}")

            # Find it in Plex
            plex    = PlexServer(CONFIG["PLEX_URL"], CONFIG["PLEX_TOKEN"])
            tv_lib  = plex.library.section(CONFIG["PLEX_TV_LIBRARY"])
            plex_show = None

            for show in tv_lib.all():
                for guid in show.guids:
                    if "tvdb://" in guid.id:
                        try:
                            if int(guid.id.replace("tvdb://", "")) == tvdb_id:
                                plex_show = show
                                break
                        except ValueError:
                            pass
                if plex_show:
                    break

            if not plex_show:
                logger.warning(f"  ⚠ '{title}' not found in Plex library")
                return

            marked, missing = mark_episodes_downloaded(plex_show, sonarr_id, title)
            logger.info(f"  ✅ Connect complete for '{title}' — {marked} episodes marked, {missing} not in Sonarr")
        except Exception as e:
            logger.error(f"Connect failed for series {sonarr_id}: {e}")

    threading.Thread(target=run, daemon=True).start()
    return jsonify({"ok": True})

@app.route("/api/watchdog/stop", methods=["POST"])
def api_watchdog_stop():
    global watchdog_running
    watchdog_running = False
    logger.info("🛑 Watchdog stopped.")
    return jsonify({"ok": True})

@app.route("/api/watchdog/status")
def api_watchdog_status():
    return jsonify({"running": watchdog_running, "firstRun": first_run,
                    "scanComplete": scan_complete,
                    "setupComplete": CONFIG.get("SETUP_COMPLETE", False),
                    "initialScanDone": CONFIG.get("INITIAL_SCAN_DONE", False)})

@app.route("/api/logs/history")
def api_logs_history():
    """Return all buffered log entries for page reload replay."""
    return jsonify(log_history)

@app.route("/api/logs/stream")
def api_logs_stream():
    def generate():
        while True:
            try:
                entry = log_queue.get(timeout=1)
                yield f"data: {json.dumps(entry)}\n\n"
            except queue.Empty:
                yield f"data: {json.dumps({'ping': True})}\n\n"
    return Response(stream_with_context(generate()),
                    mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

@app.route("/api/quality-profiles")
def api_quality_profiles():
    try:
        profiles = sonarr_get("qualityprofile")
        return jsonify(profiles)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    # Auto-start watchdog on restart if initial scan was already completed
    if CONFIG.get("INITIAL_SCAN_DONE") and CONFIG.get("SETUP_COMPLETE"):
        logger.info("🔁 Detected previous completed scan — auto-starting watchdog...")
        t = threading.Thread(target=auto_start_watchdog, daemon=True)
        t.start()
    app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)
