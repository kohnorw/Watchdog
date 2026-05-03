import os
import re
import time
import threading
import logging
import json
import queue
import subprocess
import requests
from datetime import datetime
from flask import Flask, jsonify, request, Response, stream_with_context
from plexapi.server import PlexServer
from jinja2 import Template

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
    "DEBRID_PATH":            "/docker/zurg/mnt/zurg/shows",
    "POLL_INTERVAL":          30,
    "AUTO_SYMLINK":           False,
    "SETUP_COMPLETE":         False,
    "INITIAL_SCAN_DONE":      False,
    "IGNORED_SERIES":         [],
}

def load_config():
    cfg = dict(CONFIG_DEFAULTS)
    try:
        os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
        with open(CONFIG_FILE) as f:
            cfg.update(json.load(f))
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return cfg

def save_config():
    try:
        os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
        with open(CONFIG_FILE, "w") as f:
            json.dump(CONFIG, f, indent=2)
    except Exception as e:
        logger.warning(f"Could not save config: {e}")

CONFIG = load_config()

# ── State ─────────────────────────────────────────────────────────────────────
log_queue        = queue.Queue()
log_history      = []
LOG_HISTORY_MAX  = 2000
watchdog_running = False
watchdog_thread  = None
scan_running     = False
pending_readd    = []

# ── Logging ───────────────────────────────────────────────────────────────────
class QueueHandler(logging.Handler):
    def emit(self, record):
        entry = {"time": datetime.now().strftime("%H:%M:%S"), "level": record.levelname, "msg": self.format(record)}
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

ONGOING_STATUSES = {"returning series", "in production", "planned", "pilot", "continuing"}
VIDEO_EXTS = {".mkv", ".mp4", ".avi", ".m4v", ".ts", ".mov", ".wmv"}

# ── Sonarr ────────────────────────────────────────────────────────────────────
def sonarr_get(path):
    r = requests.get(f"{CONFIG['SONARR_URL']}/api/v3/{path}", headers={"X-Api-Key": CONFIG["SONARR_API_KEY"]}, timeout=10)
    r.raise_for_status()
    return r.json()

def sonarr_post(path, payload):
    r = requests.post(f"{CONFIG['SONARR_URL']}/api/v3/{path}", headers={"X-Api-Key": CONFIG["SONARR_API_KEY"]}, json=payload, timeout=10)
    r.raise_for_status()
    return r.json()

def get_quality_profile_id():
    profiles = sonarr_get("qualityprofile")
    for p in profiles:
        if p["name"].lower() == CONFIG["SONARR_QUALITY_PROFILE"].lower():
            return p["id"]
    return profiles[0]["id"] if profiles else 1

def get_sonarr_series_map():
    return {s["tvdbId"]: s for s in sonarr_get("series") if s.get("tvdbId")}

def refresh_and_rescan_series(sonarr_id, title):
    """
    1. RefreshSeries  — pulls latest episode/metadata from TVDB
    2. RescanSeries   — scans disk and imports any symlinked files
    """
    try:
        sonarr_post("command", {"name": "RefreshSeries", "seriesId": sonarr_id})
        logger.info(f"  🔄 Refresh triggered for '{title}'")
    except Exception as e:
        logger.warning(f"  ⚠ Refresh failed for '{title}': {e}")
    time.sleep(2)  # give Sonarr time to finish refresh before disk scan
    try:
        sonarr_post("command", {"name": "RescanSeries", "seriesId": sonarr_id})
        logger.info(f"  🔍 Rescan triggered for '{title}'")
        return True
    except Exception as e:
        logger.warning(f"  ⚠ Rescan failed for '{title}': {e}")
        return False

def rescan_series(sonarr_id, title):
    try:
        sonarr_post("command", {"name": "RescanSeries", "seriesId": sonarr_id})
        logger.info(f"  🔍 Rescan triggered for '{title}'")
        return True
    except Exception as e:
        logger.warning(f"  ⚠ Rescan failed for '{title}': {e}")
        return False

# ── TMDB ──────────────────────────────────────────────────────────────────────
def get_show_status(tvdb_id, title):
    key = CONFIG.get("TMDB_API_KEY", "")
    if key:
        try:
            r = requests.get(f"https://api.themoviedb.org/3/find/{tvdb_id}",
                             params={"api_key": key, "external_source": "tvdb_id"}, timeout=8)
            results = r.json().get("tv_results", [])
            if results:
                sid = results[0]["id"]
                r2 = requests.get(f"https://api.themoviedb.org/3/tv/{sid}", params={"api_key": key}, timeout=8)
                status = r2.json().get("status", "").lower()
                return status in ONGOING_STATUSES, status
        except Exception as e:
            logger.debug(f"TMDB check failed for '{title}': {e}")
    try:
        results = sonarr_get(f"series/lookup?term=tvdb:{tvdb_id}")
        if results:
            status = results[0].get("status", "").lower()
            return status in {"continuing", "upcoming"}, status
    except Exception:
        pass
    return True, "unknown"

# ── Add series ────────────────────────────────────────────────────────────────
def add_series_to_sonarr(tvdb_id, title, quality_profile_id):
    try:
        results = sonarr_get(f"series/lookup?term=tvdb:{tvdb_id}")
        if not results:
            logger.warning(f"  ⚠ No lookup result for '{title}' (TVDB:{tvdb_id})")
            return False
        lookup = results[0]
        seasons = [dict(s, monitored=True) for s in lookup.get("seasons", [])]
        payload = {
            "title":            lookup["title"],
            "tvdbId":           tvdb_id,
            "qualityProfileId": quality_profile_id,
            "rootFolderPath":   CONFIG["SONARR_ROOT_FOLDER"],
            "seasonFolder":     True,
            "monitored":        True,
            "addOptions":       {"searchForMissingEpisodes": False, "searchForCutoffUnmetEpisodes": False, "monitor": "all"},
            "seasons":          seasons,
            "images":           lookup.get("images", []),
            "path":             f"{CONFIG['SONARR_ROOT_FOLDER']}/{lookup['title']}",
        }
        added = sonarr_post("series", payload)
        sonarr_id = added.get("id")
        logger.info(f"  ✅ Added: {lookup['title']} (TVDB:{tvdb_id}) — all episodes marked missing")
        if sonarr_id:
            time.sleep(2)
            rescan_series(sonarr_id, lookup["title"])
        return True
    except requests.HTTPError as e:
        if e.response.status_code == 400:
            logger.debug(f"  Already in Sonarr: {title}")
        else:
            logger.error(f"  ❌ Failed to add '{title}': {e}")
        return False

# ── Debrid symlinks ───────────────────────────────────────────────────────────
def normalize(s):
    """Lowercase, strip punctuation and extra spaces for fuzzy matching."""
    s = s.lower()
    s = re.sub(r'[:\-"!&.,]', " ", s)   # common punctuation to space
    s = re.sub(r"\s+", " ", s).strip()
    return s

def find_debrid_files_for_series(title):
    """
    Multi-strategy debrid folder search:
    1. Exact grep on full title
    2. Grep on each significant word individually
    3. Fuzzy normalize + compare all folder names
    Falls through each strategy until files are found.
    """
    debrid_path = CONFIG.get("DEBRID_PATH", "").strip()
    if not debrid_path or not os.path.isdir(debrid_path):
        logger.warning(f"  ⚠ Debrid path not found: {debrid_path}")
        return []

    # Strip year like "Show Name (2020)" -> "Show Name"
    clean_title = re.sub(r"\s*\(\d{4}\)\s*$", "", title).strip()
    norm_title  = normalize(clean_title)

    logger.info(f"  🔍 Searching debrid for '{clean_title}'")

    # Get full folder list once
    try:
        all_folders = os.listdir(debrid_path)
    except Exception as e:
        logger.warning(f"  ⚠ Could not list debrid path: {e}")
        return []

    matched = []

    # Strategy 1: exact grep (case insensitive)
    try:
        result = subprocess.run(
            f"ls {repr(debrid_path)} | grep -i {repr(clean_title)}",
            shell=True, capture_output=True, text=True
        )
        matched = [f.strip() for f in result.stdout.strip().splitlines() if f.strip()]
        if matched:
            logger.info(f"  ✅ Strategy 1 (exact grep) matched: {', '.join(matched)}")
    except Exception as e:
        logger.debug(f"  Strategy 1 failed: {e}")

    # Strategy 2: grep each significant word (3+ chars)
    if not matched:
        words = [w for w in clean_title.split() if len(w) >= 3
                 and w.lower() not in {"the","and","for","with","from","this","that","are","was","has","not"}]
        word_matches = set()
        for word in words:
            try:
                result = subprocess.run(
                    f"ls {repr(debrid_path)} | grep -i {repr(word)}",
                    shell=True, capture_output=True, text=True
                )
                hits = [f.strip() for f in result.stdout.strip().splitlines() if f.strip()]
                word_matches.update(hits)
            except Exception:
                pass
        if word_matches:
            # Filter to folders where most words match
            scored = []
            for folder in word_matches:
                norm_f = normalize(folder)
                score  = sum(1 for w in words if w.lower() in norm_f)
                scored.append((score, folder))
            scored.sort(reverse=True)
            best_score = scored[0][0] if scored else 0
            matched = [f for s, f in scored if s >= max(1, best_score - 1)]
            if matched:
                logger.info(f"  ✅ Strategy 2 (word grep) matched: {', '.join(matched)}")

    # Strategy 3: fuzzy normalize compare
    if not matched:
        norm_words = norm_title.split()
        scored = []
        for folder in all_folders:
            norm_f = normalize(folder)
            score  = sum(1 for w in norm_words if w in norm_f)
            if score > 0:
                scored.append((score, folder))
        scored.sort(reverse=True)
        if scored:
            best = scored[0][0]
            matched = [f for s, f in scored if s >= max(1, best - 1)]
            logger.info(f"  ✅ Strategy 3 (fuzzy) matched: {', '.join(matched)}")

    if not matched:
        logger.info(f"  ❌ No debrid folder found for '{clean_title}'")
        return []

    # Walk matched folders and collect video files
    files = []
    for folder in matched:
        folder_path = os.path.join(debrid_path, folder)
        if not os.path.isdir(folder_path):
            continue
        count = 0
        for root, dirs, fnames in os.walk(folder_path):
            for fname in fnames:
                if os.path.splitext(fname)[1].lower() in VIDEO_EXTS:
                    files.append(os.path.join(root, fname))
                    count += 1
        logger.info(f"    📼 {folder} — {count} video files")

    logger.info(f"  📦 Total files found for '{clean_title}': {len(files)}")
    return files

def symlink_series(sonarr_series):
    title       = sonarr_series["title"]
    series_path = sonarr_series.get("path", "")
    if not series_path:
        logger.warning(f"  ⚠ No Sonarr path for '{title}'")
        return 0

    debrid_files = find_debrid_files_for_series(title)
    if not debrid_files:
        return 0

    if not os.path.exists(series_path):
        logger.info(f"  📁 Creating series directory: {series_path}")
    os.makedirs(series_path, exist_ok=True)
    os.chmod(series_path, 0o777)

    created = repaired = skipped = 0

    for src in debrid_files:
        filename = os.path.basename(src)
        season_num = None
        for part in src.replace("\\", "/").split("/"):
            if part.lower().startswith("season "):
                try:
                    season_num = int(part.lower().replace("season ", ""))
                except ValueError:
                    pass
        if season_num is None:
            m = re.search(r"[Ss](\d{1,2})[Ee]\d{1,2}", filename)
            if m:
                season_num = int(m.group(1))

        season_dir = os.path.join(series_path, f"Season {season_num:02}") if season_num else series_path
        if not os.path.exists(season_dir):
            logger.info(f"    📁 Creating season directory: {season_dir}")
        os.makedirs(season_dir, exist_ok=True)
        os.chmod(season_dir, 0o777)

        dst = os.path.join(season_dir, filename)

        if os.path.islink(dst) and os.path.exists(dst):
            if os.readlink(dst) == src:
                skipped += 1
                continue
            logger.info(f"    🔄 Updating symlink: {filename}")
            os.remove(dst)
            repaired += 1
        elif os.path.islink(dst):
            logger.info(f"    🔧 Recreating broken symlink: {filename}")
            os.remove(dst)
            repaired += 1
        elif os.path.exists(dst):
            skipped += 1
            continue

        try:
            os.symlink(src, dst)
            created += 1
            logger.info(f"    🔗 {'Recreated' if repaired else 'Symlinked'}: {filename}")
        except Exception as e:
            logger.warning(f"    ⚠ Symlink failed '{filename}': {e}")

    total = created + repaired
    if total:
        logger.info(f"  ✅ '{title}' — {created} new, {repaired} repaired, {skipped} unchanged")
        refresh_and_rescan_series(sonarr_series["id"], title)
    else:
        logger.debug(f"  ✓ '{title}' — {skipped} up to date")
    return created

def symlink_all(sonarr_map):
    logger.info("🔗 Starting symlink pass for all series...")
    total = 0
    for tvdb_id, series in sonarr_map.items():
        total += symlink_series(series)
        time.sleep(0.1)
    logger.info(f"✅ Symlink pass done — {total} new symlinks")

def check_and_repair_symlinks(sonarr_map):
    debrid_path = CONFIG.get("DEBRID_PATH", "").strip()
    if not debrid_path or not os.path.isdir(debrid_path):
        return 0
    logger.info(f"🔧 Checking symlinks...")
    # Build debrid file index
    file_map = {}
    try:
        for folder in os.listdir(debrid_path):
            fp = os.path.join(debrid_path, folder)
            if os.path.isdir(fp):
                for root, dirs, files in os.walk(fp):
                    for fname in files:
                        if os.path.splitext(fname)[1].lower() in VIDEO_EXTS:
                            file_map[fname] = os.path.join(root, fname)
        logger.info(f"  📦 Debrid index: {len(file_map)} files")
    except Exception as e:
        logger.warning(f"  ⚠ Could not build debrid index: {e}")
        return 0

    removed = repaired = 0
    for tvdb_id, series in sonarr_map.items():
        path = series.get("path", "")
        if not path or not os.path.isdir(path):
            continue
        for root, dirs, files in os.walk(path):
            for fname in files:
                fpath = os.path.join(root, fname)
                if not os.path.islink(fpath):
                    continue
                if os.path.exists(fpath):
                    continue
                old_target = os.readlink(fpath)
                try:
                    os.remove(fpath)
                    removed += 1
                except Exception:
                    continue
                new_target = file_map.get(fname)
                if new_target and os.path.exists(new_target):
                    try:
                        os.symlink(new_target, fpath)
                        repaired += 1
                        logger.info(f"  🔧 Repaired: {fname}")
                    except Exception as e:
                        logger.warning(f"  ⚠ Repair failed for {fname}: {e}")
                else:
                    logger.info(f"  🗑 Removed broken symlink (no match): {fname}")
    if removed:
        logger.info(f"🔧 Symlink repair — {repaired} repaired, {removed - repaired} removed")
    else:
        logger.debug("🔧 All symlinks OK")
    return removed

# ── Scans ─────────────────────────────────────────────────────────────────────
def do_initial_scan():
    global watchdog_running, watchdog_thread, scan_running
    if scan_running:
        return
    scan_running = True
    logger.info("🔍 Starting initial Plex scan...")
    try:
        plex   = PlexServer(CONFIG["PLEX_URL"], CONFIG["PLEX_TOKEN"])
        tv_lib = plex.library.section(CONFIG["PLEX_TV_LIBRARY"])
    except Exception as e:
        logger.error(f"❌ Plex connection failed: {e}")
        scan_running = False
        return
    try:
        sonarr_map         = get_sonarr_series_map()
        quality_profile_id = get_quality_profile_id()
    except Exception as e:
        logger.error(f"❌ Sonarr connection failed: {e}")
        scan_running = False
        return

    shows = tv_lib.all()
    logger.info(f"📺 {len(shows)} shows in Plex")
    added = skipped_ended = skipped_exists = 0
    for show in shows:
        tvdb_id = None
        for guid in show.guids:
            if "tvdb://" in guid.id:
                try: tvdb_id = int(guid.id.replace("tvdb://", ""))
                except ValueError: pass
        if not tvdb_id:
            continue
        if tvdb_id in sonarr_map:
            skipped_exists += 1
            continue
        is_ongoing, status = get_show_status(tvdb_id, show.title)
        if not is_ongoing:
            skipped_ended += 1
            continue
        if tvdb_id in CONFIG.get("IGNORED_SERIES", []):
            continue
        logger.info(f"  📡 Adding: {show.title} [{status}]")
        add_series_to_sonarr(tvdb_id, show.title, quality_profile_id)
        added += 1
        time.sleep(0.3)

    logger.info(f"✅ Scan done — {added} added, {skipped_exists} already in Sonarr, {skipped_ended} ended/skipped")
    sonarr_map = get_sonarr_series_map()
    symlink_all(sonarr_map)
    CONFIG["INITIAL_SCAN_DONE"] = True
    scan_running = False
    save_config()
    logger.info("🐕 Starting watchdog loop...")
    watchdog_running = True
    watchdog_thread  = threading.Thread(target=do_watchdog_loop, daemon=True)
    watchdog_thread.start()

def do_watchdog_loop():
    logger.info(f"🐕 Watchdog running every {CONFIG['POLL_INTERVAL']}s")
    while watchdog_running:
        try:
            plex               = PlexServer(CONFIG["PLEX_URL"], CONFIG["PLEX_TOKEN"])
            tv_lib             = plex.library.section(CONFIG["PLEX_TV_LIBRARY"])
            sonarr_map         = get_sonarr_series_map()
            quality_profile_id = get_quality_profile_id()
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
                if tvdb_id in CONFIG.get("IGNORED_SERIES", []):
                    already = any(p["tvdbId"] == tvdb_id for p in pending_readd)
                    if not already:
                        logger.info(f"⚠ '{show.title}' was deleted — awaiting confirmation")
                        pending_readd.append({"tvdbId": tvdb_id, "title": show.title, "status": status})
                    continue
                logger.info(f"🆕 New show: {show.title} [{status}]")
                add_series_to_sonarr(tvdb_id, show.title, quality_profile_id)
            sonarr_map = get_sonarr_series_map()
            check_and_repair_symlinks(sonarr_map)
            if CONFIG.get("AUTO_SYMLINK", False):
                symlink_all(sonarr_map)
            else:
                logger.debug("💤 Auto-symlink off — sync manually from Series page")
        except Exception as e:
            logger.error(f"Watchdog error: {e}")
        time.sleep(CONFIG["POLL_INTERVAL"])

def auto_start():
    global watchdog_running, watchdog_thread
    logger.info("🔁 Auto-starting watchdog...")
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
            if not is_ongoing or tvdb_id in CONFIG.get("IGNORED_SERIES", []):
                continue
            logger.info(f"  🆕 New show: {show.title} [{status}]")
            add_series_to_sonarr(tvdb_id, show.title, quality_profile_id)
            added += 1
            time.sleep(0.3)
        sonarr_map = get_sonarr_series_map()
        symlink_all(sonarr_map)
        logger.info(f"✅ Auto-start done — {added} new shows added")
    except Exception as e:
        logger.error(f"Auto-start error: {e}")
    logger.info("🐕 Starting watchdog loop...")
    watchdog_running = True
    watchdog_thread  = threading.Thread(target=do_watchdog_loop, daemon=True)
    watchdog_thread.start()

# ── Routes ────────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    with open(os.path.join(os.path.dirname(__file__), "templates", "index.html")) as f:
        tmpl = Template(f.read())
    return tmpl.render(config=CONFIG)

@app.route("/api/status")
def api_status():
    return jsonify({"setupComplete": CONFIG.get("SETUP_COMPLETE", False),
                    "initialScanDone": CONFIG.get("INITIAL_SCAN_DONE", False),
                    "watchdogRunning": watchdog_running,
                    "scanRunning": scan_running})

@app.route("/api/config", methods=["GET", "POST"])
def api_config():
    if request.method == "POST":
        for k, v in (request.json or {}).items():
            if k in CONFIG:
                CONFIG[k] = v
        save_config()
        return jsonify({"ok": True})
    return jsonify(CONFIG)

@app.route("/api/setup", methods=["POST"])
def api_setup():
    for k, v in (request.json or {}).items():
        if k in CONFIG:
            CONFIG[k] = v
    CONFIG["SETUP_COMPLETE"] = True
    save_config()
    logger.info("✅ Setup complete")
    return jsonify({"ok": True})

@app.route("/api/test-connections", methods=["POST"])
def api_test_connections():
    data = request.json or {}
    out  = {"plex": False, "sonarr": False, "tmdb": False, "errors": {}}
    try:
        plex = PlexServer(data.get("PLEX_URL"), data.get("PLEX_TOKEN"))
        plex.library.section(data.get("PLEX_TV_LIBRARY", "TV Shows"))
        out["plex"] = True
    except Exception as e:
        out["errors"]["plex"] = str(e)
    try:
        r = requests.get(f"{data.get('SONARR_URL')}/api/v3/system/status",
                         headers={"X-Api-Key": data.get("SONARR_API_KEY")}, timeout=8)
        r.raise_for_status()
        out["sonarr"] = True
    except Exception as e:
        out["errors"]["sonarr"] = str(e)
    if data.get("TMDB_API_KEY"):
        try:
            r = requests.get("https://api.themoviedb.org/3/configuration",
                             params={"api_key": data.get("TMDB_API_KEY")}, timeout=8)
            r.raise_for_status()
            out["tmdb"] = True
        except Exception as e:
            out["errors"]["tmdb"] = str(e)
    return jsonify(out)

@app.route("/api/start-initial-scan", methods=["POST"])
def api_start_initial_scan():
    if not scan_running:
        threading.Thread(target=do_initial_scan, daemon=True).start()
    return jsonify({"ok": True})

@app.route("/api/series/list")
def api_series_list():
    try:
        out = []
        for s in sonarr_get("series"):
            stats = s.get("statistics", {})
            out.append({
                "id":               s["id"],
                "title":            s["title"],
                "tvdbId":           s.get("tvdbId"),
                "status":           s.get("status", ""),
                "monitored":        s.get("monitored", False),
                "episodeCount":     stats.get("episodeCount", 0),
                "episodeFileCount": stats.get("episodeFileCount", 0),
                "seasonCount":      s.get("seasonCount", 0),
                "poster":           next((i["remoteUrl"] for i in s.get("images", []) if i.get("coverType") == "poster"), None),
            })
        out.sort(key=lambda x: x["title"])
        return jsonify(out)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/series/<int:sonarr_id>/link", methods=["POST"])
def api_series_link(sonarr_id):
    def run():
        try:
            series = sonarr_get(f"series/{sonarr_id}")
            logger.info(f"🔗 Symlinking: {series['title']}")
            symlink_series(series)
        except Exception as e:
            logger.error(f"Symlink failed for {sonarr_id}: {e}")
    threading.Thread(target=run, daemon=True).start()
    return jsonify({"ok": True})

@app.route("/api/series/link-all", methods=["POST"])
def api_series_link_all():
    def run():
        try:
            symlink_all(get_sonarr_series_map())
        except Exception as e:
            logger.error(f"Bulk symlink failed: {e}")
    threading.Thread(target=run, daemon=True).start()
    return jsonify({"ok": True})

@app.route("/api/series/<int:sonarr_id>/delete", methods=["POST"])
def api_series_delete(sonarr_id):
    try:
        series  = sonarr_get(f"series/{sonarr_id}")
        tvdb_id = series.get("tvdbId")
        title   = series["title"]
        requests.delete(f"{CONFIG['SONARR_URL']}/api/v3/series/{sonarr_id}",
                        headers={"X-Api-Key": CONFIG["SONARR_API_KEY"]},
                        params={"deleteFiles": "false", "addImportListExclusion": "false"}, timeout=10).raise_for_status()
        ignored = CONFIG.get("IGNORED_SERIES", [])
        if tvdb_id and tvdb_id not in ignored:
            ignored.append(tvdb_id)
            CONFIG["IGNORED_SERIES"] = ignored
            save_config()
        logger.info(f"🗑 Deleted '{title}' and added to ignore list")
        return jsonify({"ok": True, "title": title})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/pending-readd")
def api_pending_readd():
    return jsonify(pending_readd)

@app.route("/api/pending-readd/confirm", methods=["POST"])
def api_pending_readd_confirm():
    global pending_readd
    data    = request.json or {}
    tvdb_id = data.get("tvdbId")
    title   = data.get("title", "")
    ignored = CONFIG.get("IGNORED_SERIES", [])
    if tvdb_id in ignored:
        ignored.remove(tvdb_id)
        CONFIG["IGNORED_SERIES"] = ignored
        save_config()
    pending_readd = [p for p in pending_readd if p["tvdbId"] != tvdb_id]
    try:
        add_series_to_sonarr(tvdb_id, title, get_quality_profile_id())
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    return jsonify({"ok": True})

@app.route("/api/pending-readd/dismiss", methods=["POST"])
def api_pending_readd_dismiss():
    global pending_readd
    tvdb_id = (request.json or {}).get("tvdbId")
    pending_readd = [p for p in pending_readd if p["tvdbId"] != tvdb_id]
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
    return Response(stream_with_context(generate()), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

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
