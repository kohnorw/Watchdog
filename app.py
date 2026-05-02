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
log_queue       = queue.Queue()
log_history     = []
LOG_HISTORY_MAX = 2000
watchdog_running = False
watchdog_thread  = None

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

# ── Constants ─────────────────────────────────────────────────────────────────
ONGOING_STATUSES = {"returning series", "in production", "planned", "pilot", "continuing"}

# ── Sonarr helpers ────────────────────────────────────────────────────────────
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

def sonarr_put(path, payload):
    r = requests.put(f"{CONFIG['SONARR_URL']}/api/v3/{path}",
                     headers={"X-Api-Key": CONFIG["SONARR_API_KEY"]},
                     json=payload, timeout=10)
    r.raise_for_status()
    return r.json()

def get_quality_profile_id():
    profiles = sonarr_get("qualityprofile")
    for p in profiles:
        if p["name"].lower() == CONFIG["SONARR_QUALITY_PROFILE"].lower():
            return p["id"]
    return profiles[0]["id"] if profiles else 1

def get_sonarr_series_map():
    """Returns {tvdbId: series_dict} for all series in Sonarr."""
    series = sonarr_get("series")
    return {s["tvdbId"]: s for s in series if s.get("tvdbId")}

# ── TMDB helpers ──────────────────────────────────────────────────────────────
def get_show_status(tvdb_id, title, tmdb_id=None):
    """Returns (is_ongoing, status_str). Checks TMDB then Sonarr lookup."""
    key = CONFIG.get("TMDB_API_KEY", "")
    if key:
        try:
            sid = tmdb_id
            if not sid and tvdb_id:
                r = requests.get(f"https://api.themoviedb.org/3/find/{tvdb_id}",
                                 params={"api_key": key, "external_source": "tvdb_id"}, timeout=8)
                results = r.json().get("tv_results", [])
                if results:
                    sid = results[0]["id"]
            if sid:
                r = requests.get(f"https://api.themoviedb.org/3/tv/{sid}",
                                 params={"api_key": key}, timeout=8)
                data   = r.json()
                status = data.get("status", "").lower()
                return status in ONGOING_STATUSES, status
        except Exception as e:
            logger.debug(f"TMDB status check failed for '{title}': {e}")
    # Fallback: Sonarr lookup
    try:
        results = sonarr_get(f"series/lookup?term=tvdb:{tvdb_id}")
        if results:
            status = results[0].get("status", "").lower()
            return status in {"continuing", "upcoming"}, status
    except Exception:
        pass
    return True, "unknown"

# ── Add series to Sonarr ──────────────────────────────────────────────────────
def add_series_to_sonarr(tvdb_id, title, quality_profile_id, tmdb_id=None):
    """Add a continuing show to Sonarr monitoring future episodes only."""
    try:
        results = sonarr_get(f"series/lookup?term=tvdb:{tvdb_id}")
        if not results:
            logger.warning(f"  ⚠ No Sonarr lookup result for '{title}' (TVDB:{tvdb_id})")
            return False
        lookup = results[0]
        # Only monitor future — don't search for missing back catalogue
        seasons = [dict(s, monitored=True) for s in lookup.get("seasons", [])]
        payload = {
            "title":            lookup["title"],
            "tvdbId":           tvdb_id,
            "qualityProfileId": quality_profile_id,
            "rootFolderPath":   CONFIG["SONARR_ROOT_FOLDER"],
            "seasonFolder":     True,
            "monitored":        True,
            "addOptions": {
                "searchForMissingEpisodes":     False,
                "searchForCutoffUnmetEpisodes": False,
                "monitor":                      "future",
            },
            "seasons": seasons,
            "images":  lookup.get("images", []),
            "path":    f"{CONFIG['SONARR_ROOT_FOLDER']}/{lookup['title']}",
        }
        sonarr_post("series", payload)
        logger.info(f"  ✅ Added to Sonarr: {lookup['title']} (TVDB:{tvdb_id}) — monitoring future episodes")
        return True
    except requests.HTTPError as e:
        if e.response.status_code == 400:
            logger.debug(f"  Already in Sonarr: {title}")
        else:
            logger.error(f"  ❌ Failed to add '{title}': {e}")
        return False

# ── Link episodes ─────────────────────────────────────────────────────────────
def link_episodes_for_series(plex_show, sonarr_series_id, title):
    """
    Cross-reference Plex episodes against Sonarr and mark files as downloaded.
    Returns (newly_marked, already_had, not_in_sonarr).
    """
    try:
        sonarr_episodes = sonarr_get(f"episode?seriesId={sonarr_series_id}")
        existing_file_ids = {ef["id"] for ef in sonarr_get(f"episodefile?seriesId={sonarr_series_id}")}
    except Exception as e:
        logger.error(f"  ❌ Could not fetch Sonarr episodes for '{title}': {e}")
        return 0, 0, 0

    ep_map = {}
    for ep in sonarr_episodes:
        ep_map[(ep["seasonNumber"], ep["episodeNumber"])] = ep

    newly_marked = 0
    already_had  = 0
    not_in_sonarr = 0

    for plex_season in plex_show.seasons():
        snum = plex_season.seasonNumber
        if snum == 0:
            continue
        for plex_ep in plex_season.episodes():
            enum = plex_ep.index
            sonarr_ep = ep_map.get((snum, enum))
            if not sonarr_ep:
                not_in_sonarr += 1
                continue
            if sonarr_ep.get("hasFile") or sonarr_ep.get("episodeFileId") in existing_file_ids:
                already_had += 1
                continue
            try:
                file_path = plex_ep.media[0].parts[0].file
            except (IndexError, AttributeError):
                continue
            try:
                sonarr_post("command", {
                    "name": "ManualImport",
                    "files": [{
                        "path":       file_path,
                        "seriesId":   sonarr_series_id,
                        "episodeIds": [sonarr_ep["id"]],
                        "quality":    {"quality": {"id": 4, "name": "HDTV-1080p"}, "revision": {"version": 1}},
                        "languages":  [{"id": 1, "name": "English"}],
                    }],
                    "importMode": "auto",
                })
                newly_marked += 1
                logger.info(f"    🔗 Linked S{snum:02}E{enum:02} — {os.path.basename(file_path)}")
            except Exception as e:
                logger.warning(f"    ⚠ Could not link S{snum:02}E{enum:02}: {e}")

    return newly_marked, already_had, not_in_sonarr

def link_all_series(plex, sonarr_map):
    """Link episodes for all series in Sonarr from Plex."""
    logger.info("🔗 Starting episode link pass for all series...")
    tv_lib = plex.library.section(CONFIG["PLEX_TV_LIBRARY"])
    total_new = 0
    for show in tv_lib.all():
        tvdb_id = None
        for guid in show.guids:
            if "tvdb://" in guid.id:
                try: tvdb_id = int(guid.id.replace("tvdb://", ""))
                except ValueError: pass
        if not tvdb_id or tvdb_id not in sonarr_map:
            continue
        sonarr_series = sonarr_map[tvdb_id]
        new, had, missing = link_episodes_for_series(show, sonarr_series["id"], show.title)
        total_new += new
        if new:
            logger.info(f"  📺 '{show.title}' — {new} newly linked, {had} already linked")
    logger.info(f"🔗 Link pass complete — {total_new} episodes newly linked across all series")

# ── Initial Plex scan ─────────────────────────────────────────────────────────
def do_initial_scan():
    """Scan Plex, add only continuing shows to Sonarr, then start watchdog."""
    global watchdog_running, watchdog_thread
    logger.info("🔍 Starting initial Plex scan...")
    try:
        plex   = PlexServer(CONFIG["PLEX_URL"], CONFIG["PLEX_TOKEN"])
        tv_lib = plex.library.section(CONFIG["PLEX_TV_LIBRARY"])
    except Exception as e:
        logger.error(f"❌ Plex connection failed: {e}")
        return

    try:
        sonarr_map         = get_sonarr_series_map()
        quality_profile_id = get_quality_profile_id()
    except Exception as e:
        logger.error(f"❌ Sonarr connection failed: {e}")
        return

    shows = tv_lib.all()
    logger.info(f"📺 Found {len(shows)} shows in Plex")
    added = skipped_ended = skipped_exists = 0

    for show in shows:
        tvdb_id = None
        for guid in show.guids:
            if "tvdb://" in guid.id:
                try: tvdb_id = int(guid.id.replace("tvdb://", ""))
                except ValueError: pass

        if not tvdb_id:
            logger.debug(f"  ⚠ No TVDB ID for '{show.title}', skipping")
            continue

        if tvdb_id in sonarr_map:
            skipped_exists += 1
            continue

        is_ongoing, status = get_show_status(tvdb_id, show.title)
        if not is_ongoing:
            logger.debug(f"  ⏭ '{show.title}' is {status} — skipping")
            skipped_ended += 1
            continue

        logger.info(f"  📡 Adding continuing show: {show.title} [{status}]")
        add_series_to_sonarr(tvdb_id, show.title, quality_profile_id)
        added += 1
        time.sleep(0.3)

    logger.info(f"✅ Initial scan done — {added} added, {skipped_exists} already in Sonarr, {skipped_ended} ended/skipped")

    # Link episodes for all added series
    logger.info("🔗 Linking episodes for all series...")
    sonarr_map = get_sonarr_series_map()
    link_all_series(plex, sonarr_map)

    CONFIG["INITIAL_SCAN_DONE"] = True
    save_config_to_disk(CONFIG)
    logger.info("🐕 Starting watchdog loop...")
    watchdog_running = True
    watchdog_thread  = threading.Thread(target=do_watchdog_loop, daemon=True)
    watchdog_thread.start()

# ── Watchdog loop ─────────────────────────────────────────────────────────────
def do_watchdog_loop():
    """
    Every POLL_INTERVAL seconds:
    1. Check Plex for continuing shows not yet in Sonarr → add them
    2. Link any new episodes for all series already in Sonarr
    """
    logger.info(f"🐕 Watchdog loop running every {CONFIG['POLL_INTERVAL']}s")
    while watchdog_running:
        try:
            plex   = PlexServer(CONFIG["PLEX_URL"], CONFIG["PLEX_TOKEN"])
            tv_lib = plex.library.section(CONFIG["PLEX_TV_LIBRARY"])
            sonarr_map         = get_sonarr_series_map()
            quality_profile_id = get_quality_profile_id()

            # Step 1: find new continuing shows
            for show in tv_lib.all():
                tvdb_id = None
                for guid in show.guids:
                    if "tvdb://" in guid.id:
                        try: tvdb_id = int(guid.id.replace("tvdb://", ""))
                        except ValueError: pass
                if not tvdb_id or tvdb_id in sonarr_map:
                    continue
                is_ongoing, status = get_show_status(tvdb_id, show.title)
                if not is_ongoing:
                    continue
                logger.info(f"🆕 New continuing show in Plex: {show.title} [{status}]")
                add_series_to_sonarr(tvdb_id, show.title, quality_profile_id)

            # Step 2: link new episodes for all series
            sonarr_map = get_sonarr_series_map()
            link_all_series(plex, sonarr_map)

        except Exception as e:
            logger.error(f"Watchdog error: {e}")

        logger.debug(f"💤 Sleeping {CONFIG['POLL_INTERVAL']}s...")
        time.sleep(CONFIG["POLL_INTERVAL"])

# ── Auto-start on restart ─────────────────────────────────────────────────────
def auto_start():
    """Called on container restart when INITIAL_SCAN_DONE is already True."""
    global watchdog_running, watchdog_thread
    logger.info("🔁 Restarted — auto-scanning Plex for new continuing shows...")
    try:
        plex               = PlexServer(CONFIG["PLEX_URL"], CONFIG["PLEX_TOKEN"])
        tv_lib             = plex.library.section(CONFIG["PLEX_TV_LIBRARY"])
        sonarr_map         = get_sonarr_series_map()
        quality_profile_id = get_quality_profile_id()
        added = 0
        for show in tv_lib.all():
            tvdb_id = None
            for guid in show.guids:
                if "tvdb://" in guid.id:
                    try: tvdb_id = int(guid.id.replace("tvdb://", ""))
                    except ValueError: pass
            if not tvdb_id or tvdb_id in sonarr_map:
                continue
            is_ongoing, status = get_show_status(tvdb_id, show.title)
            if not is_ongoing:
                continue
            logger.info(f"  🆕 New continuing show: {show.title} [{status}]")
            add_series_to_sonarr(tvdb_id, show.title, quality_profile_id)
            added += 1
            time.sleep(0.3)
        sonarr_map = get_sonarr_series_map()
        link_all_series(plex, sonarr_map)
        logger.info(f"✅ Auto-start complete — {added} new shows added")
    except Exception as e:
        logger.error(f"Auto-start error: {e}")
    logger.info("🐕 Starting watchdog loop...")
    watchdog_running = True
    watchdog_thread  = threading.Thread(target=do_watchdog_loop, daemon=True)
    watchdog_thread.start()

# ── Routes ────────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return render_template("index.html", config=CONFIG)

@app.route("/api/config", methods=["GET","POST"])
def api_config():
    if request.method == "POST":
        for k, v in (request.json or {}).items():
            if k in CONFIG:
                CONFIG[k] = v
        save_config_to_disk(CONFIG)
        return jsonify({"ok": True})
    return jsonify(CONFIG)

@app.route("/api/setup", methods=["POST"])
def api_setup():
    data = request.json or {}
    for k, v in data.items():
        if k in CONFIG:
            CONFIG[k] = v
    CONFIG["SETUP_COMPLETE"] = True
    save_config_to_disk(CONFIG)
    logger.info("✅ Setup complete — config saved")
    return jsonify({"ok": True})

@app.route("/api/test-connections", methods=["POST"])
def api_test_connections():
    data = request.json or {}
    results = {"plex": False, "sonarr": False, "tmdb": False, "errors": {}}
    try:
        plex = PlexServer(data.get("PLEX_URL"), data.get("PLEX_TOKEN"))
        plex.library.section(data.get("PLEX_TV_LIBRARY","TV Shows"))
        results["plex"] = True
    except Exception as e:
        results["errors"]["plex"] = str(e)
    try:
        r = requests.get(f"{data.get('SONARR_URL')}/api/v3/system/status",
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

@app.route("/api/start-initial-scan", methods=["POST"])
def api_start_initial_scan():
    t = threading.Thread(target=do_initial_scan, daemon=True)
    t.start()
    return jsonify({"ok": True})

@app.route("/api/status")
def api_status():
    return jsonify({
        "setupComplete":    CONFIG.get("SETUP_COMPLETE", False),
        "initialScanDone":  CONFIG.get("INITIAL_SCAN_DONE", False),
        "watchdogRunning":  watchdog_running,
    })

@app.route("/api/series/list")
def api_series_list():
    try:
        series = sonarr_get("series")
        out = []
        for s in series:
            stats = s.get("statistics", {})
            out.append({
                "id":               s["id"],
                "title":            s["title"],
                "tvdbId":           s.get("tvdbId"),
                "status":           s.get("status",""),
                "monitored":        s.get("monitored", False),
                "episodeCount":     stats.get("episodeCount", 0),
                "episodeFileCount": stats.get("episodeFileCount", 0),
                "seasonCount":      s.get("seasonCount", 0),
                "poster":           next((i["remoteUrl"] for i in s.get("images",[]) if i.get("coverType")=="poster"), None),
            })
        out.sort(key=lambda x: x["title"])
        return jsonify(out)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/series/<int:sonarr_id>/link", methods=["POST"])
def api_series_link(sonarr_id):
    """Link Plex episodes for a single Sonarr series."""
    def run():
        try:
            series  = sonarr_get(f"series/{sonarr_id}")
            title   = series["title"]
            tvdb_id = series.get("tvdbId")
            logger.info(f"🔗 Manually linking episodes for: {title}")
            plex   = PlexServer(CONFIG["PLEX_URL"], CONFIG["PLEX_TOKEN"])
            tv_lib = plex.library.section(CONFIG["PLEX_TV_LIBRARY"])
            plex_show = None
            for show in tv_lib.all():
                for guid in show.guids:
                    if "tvdb://" in guid.id:
                        try:
                            if int(guid.id.replace("tvdb://","")) == tvdb_id:
                                plex_show = show; break
                        except ValueError: pass
                if plex_show: break
            if not plex_show:
                logger.warning(f"  ⚠ '{title}' not found in Plex")
                return
            new, had, missing = link_episodes_for_series(plex_show, sonarr_id, title)
            logger.info(f"  ✅ '{title}' — {new} newly linked, {had} already linked, {missing} not in Sonarr")
        except Exception as e:
            logger.error(f"Link failed for series {sonarr_id}: {e}")
    threading.Thread(target=run, daemon=True).start()
    return jsonify({"ok": True})

@app.route("/api/series/link-all", methods=["POST"])
def api_series_link_all():
    """Link episodes for every series in Sonarr."""
    def run():
        try:
            logger.info("🔗 Bulk link — linking all series...")
            plex       = PlexServer(CONFIG["PLEX_URL"], CONFIG["PLEX_TOKEN"])
            sonarr_map = get_sonarr_series_map()
            link_all_series(plex, sonarr_map)
        except Exception as e:
            logger.error(f"Bulk link failed: {e}")
    threading.Thread(target=run, daemon=True).start()
    return jsonify({"ok": True})

@app.route("/api/watchdog/stop", methods=["POST"])
def api_watchdog_stop():
    global watchdog_running
    watchdog_running = False
    logger.info("🛑 Watchdog stopped")
    return jsonify({"ok": True})

@app.route("/api/logs/history")
def api_logs_history():
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
                    headers={"Cache-Control":"no-cache","X-Accel-Buffering":"no"})

@app.route("/api/quality-profiles")
def api_quality_profiles():
    try:
        return jsonify(sonarr_get("qualityprofile"))
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    if CONFIG.get("INITIAL_SCAN_DONE") and CONFIG.get("SETUP_COMPLETE"):
        logger.info("🔁 Previous scan detected — auto-starting...")
        threading.Thread(target=auto_start, daemon=True).start()
    app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)
