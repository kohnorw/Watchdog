import os
import time
import threading
import logging
import json
import queue
import requests
from datetime import datetime
from flask import Flask, jsonify, request, Response, stream_with_context
from plexapi.server import PlexServer

app = Flask(__name__)

# ── Embedded template (no templates/ folder needed) ───────────────────────────
INDEX_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Watchdog</title>
<link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600;700&family=Syne:wght@400;700;800&display=swap" rel="stylesheet">
<style>
:root{--bg:#0a0b0d;--surface:#111318;--border:#1e2129;--border2:#2a2f3a;--accent:#e8ff47;--accent2:#47b8ff;--danger:#ff4757;--success:#2ed573;--muted:#4a5165;--text:#c8cdd8;--hi:#f0f2f7}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--text);font-family:'Syne',sans-serif;min-height:100vh;display:flex;flex-direction:column}

/* ── SETUP ── */
#setup-overlay{display:none;position:fixed;inset:0;background:var(--bg);z-index:500;overflow-y:auto;padding:40px 20px;align-items:flex-start;justify-content:center}
#setup-overlay.show{display:flex}
.setup-card{width:100%;max-width:640px;margin:auto}
.setup-logo{text-align:center;margin-bottom:40px}
.setup-logo .icon{font-size:56px;display:block;margin-bottom:12px}
.setup-logo h1{font-size:36px;font-weight:800;color:var(--hi);letter-spacing:-1px}
.setup-logo p{color:var(--muted);font-family:'JetBrains Mono',monospace;font-size:12px;margin-top:6px;letter-spacing:1px}
.setup-steps{display:flex;margin-bottom:32px;border-bottom:1px solid var(--border)}
.sst{flex:1;padding:12px;text-align:center;font-family:'JetBrains Mono',monospace;font-size:11px;color:var(--muted);border-bottom:2px solid transparent;transition:all .2s;letter-spacing:1px}
.sst.active{color:var(--accent);border-bottom-color:var(--accent)}
.sst.done{color:var(--success)}
.spage{display:none}.spage.active{display:block}
.stitle{font-size:18px;font-weight:800;color:var(--hi);margin-bottom:6px}
.ssub{font-size:13px;color:var(--muted);margin-bottom:24px;line-height:1.5}
.ssub a{color:var(--accent2);text-decoration:none}
.fg{margin-bottom:16px}
.fg label{display:block;font-family:'JetBrains Mono',monospace;font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:1.5px;margin-bottom:6px}
.fg input{width:100%;background:var(--surface);border:1px solid var(--border2);border-radius:8px;color:var(--hi);font-family:'JetBrains Mono',monospace;font-size:13px;padding:11px 14px;outline:none;transition:border-color .15s}
.fg input:focus{border-color:var(--accent)}
.fhint{font-family:'JetBrains Mono',monospace;font-size:10px;color:var(--muted);margin-top:5px}
.frow{display:grid;grid-template-columns:1fr 1fr;gap:12px}
.test-btn{font-family:'Syne',sans-serif;font-weight:700;font-size:12px;padding:10px 18px;border-radius:8px;border:1px solid var(--border2);background:transparent;color:var(--text);cursor:pointer;transition:all .15s;margin-top:8px}
.test-btn:hover{border-color:var(--accent2);color:var(--accent2)}
.test-result{font-family:'JetBrains Mono',monospace;font-size:11px;margin-top:10px;padding:10px 14px;border-radius:6px;display:none}
.test-result.show{display:block}
.test-result.ok{background:rgba(46,213,115,.1);border:1px solid var(--success);color:var(--success)}
.test-result.fail{background:rgba(255,71,87,.1);border:1px solid var(--danger);color:var(--danger)}
.snav{display:flex;justify-content:space-between;margin-top:32px;padding-top:24px;border-top:1px solid var(--border);gap:12px}

/* ── BUTTONS ── */
.btn{font-family:'Syne',sans-serif;font-weight:700;font-size:13px;padding:10px 22px;border-radius:8px;border:none;cursor:pointer;transition:all .15s}
.btn-primary{background:var(--accent);color:#000}.btn-primary:hover{background:#f0ff60;transform:translateY(-1px)}
.btn-secondary{background:transparent;color:var(--text);border:1px solid var(--border2)}.btn-secondary:hover{border-color:var(--accent);color:var(--accent)}
.btn-success{background:var(--success);color:#000}.btn-success:hover{background:#3dff87}
.btn-danger{background:var(--danger);color:#fff}
.btn-sm{font-size:11px;padding:6px 14px}
.btn:disabled{opacity:.4;cursor:not-allowed;transform:none!important}

/* ── LOADING ── */
#loading{display:none;position:fixed;inset:0;background:rgba(10,11,13,.88);z-index:300;align-items:center;justify-content:center;flex-direction:column;gap:16px;backdrop-filter:blur(6px)}
#loading.show{display:flex}
.spinner{width:48px;height:48px;border:3px solid var(--border2);border-top-color:var(--accent);border-radius:50%;animation:spin .8s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}
.loading-title{font-family:'JetBrains Mono',monospace;font-size:13px;color:var(--accent);letter-spacing:1px}
.loading-sub{font-family:'JetBrains Mono',monospace;font-size:11px;color:var(--muted)}

/* ── APP SHELL ── */
#app{display:none;flex-direction:column;flex:1}

/* header */
header{display:flex;align-items:center;justify-content:space-between;padding:0 28px;height:58px;border-bottom:1px solid var(--border);position:sticky;top:0;background:rgba(10,11,13,.96);backdrop-filter:blur(12px);z-index:100;flex-shrink:0}
.logo{display:flex;align-items:center;gap:10px}
.logo-icon{width:34px;height:34px;background:var(--accent);border-radius:7px;display:flex;align-items:center;justify-content:center;font-size:18px;flex-shrink:0}
.logo-text{font-size:20px;font-weight:800;color:var(--hi);letter-spacing:-.5px}
.logo-sub{font-family:'JetBrains Mono',monospace;font-size:9px;color:var(--muted);letter-spacing:2px}
.header-mid{display:flex;gap:2px}
.nav-tab{font-family:'JetBrains Mono',monospace;font-size:11px;padding:7px 16px;border-radius:6px;border:none;background:transparent;color:var(--muted);cursor:pointer;transition:all .15s;letter-spacing:.5px}
.nav-tab:hover{color:var(--text)}
.nav-tab.active{background:rgba(232,255,71,.08);color:var(--accent)}
.header-right{display:flex;align-items:center;gap:10px}
.status-pill{font-family:'JetBrains Mono',monospace;font-size:11px;padding:5px 12px;border-radius:20px;border:1px solid var(--border2);color:var(--muted);display:flex;align-items:center;gap:6px}
.status-pill.running{border-color:var(--success);color:var(--success)}
.status-pill.scanning{border-color:var(--accent);color:var(--accent)}
.dot{width:6px;height:6px;border-radius:50%;background:currentColor}
.dot.pulse{animation:pulse 1.2s infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.2}}

/* ── PAGES ── */
.page{display:none;flex:1;flex-direction:column;min-height:0}
.page.active{display:flex}

/* ── PAGE 1: DASHBOARD ── */
#page-dashboard{overflow-y:auto}

/* scanning progress bar shown before watchdog starts */
#scan-progress{padding:20px 28px;border-bottom:1px solid var(--border);background:var(--surface);display:none}
.scan-status-row{display:flex;align-items:center;gap:16px;margin-bottom:12px}
.scan-status-text{font-family:'JetBrains Mono',monospace;font-size:12px;color:var(--accent);flex:1}
.progress-track{height:4px;background:var(--border2);border-radius:2px;overflow:hidden}
.progress-fill{height:100%;background:var(--accent);border-radius:2px;transition:width .4s;width:0%}

/* console */
.console-wrap{display:flex;flex-direction:column;margin:20px 28px;border:1px solid var(--border2);border-radius:10px;overflow:hidden;background:#080a0c}
.console-bar{display:flex;align-items:center;gap:10px;padding:10px 18px;background:var(--surface);border-bottom:1px solid var(--border);flex-shrink:0}
.cdots{display:flex;gap:5px}
.cdot{width:10px;height:10px;border-radius:50%}
.cdot.r{background:#ff5f57}.cdot.y{background:#febc2e}.cdot.g{background:#28c840}
.ctitle{font-family:'JetBrains Mono',monospace;font-size:11px;color:var(--muted);flex:1;text-align:center;letter-spacing:1px}
.cclear{font-family:'JetBrains Mono',monospace;font-size:10px;color:var(--muted);background:none;border:none;cursor:pointer;padding:2px 8px}
.cclear:hover{color:var(--accent)}
#console-out{padding:10px 18px;font-family:'JetBrains Mono',monospace;font-size:12px;line-height:1.7}
#console-out::-webkit-scrollbar{width:4px}
#console-out::-webkit-scrollbar-thumb{background:var(--border2);border-radius:2px}

.ll{display:flex;gap:10px}
.lt{color:var(--muted);flex-shrink:0}
.lv{flex-shrink:0;width:52px;text-align:right}
.lv.INFO{color:var(--accent2)}.lv.WARNING{color:#ffa502}.lv.ERROR{color:var(--danger)}.lv.DEBUG{color:var(--muted)}
.lm{color:var(--text);word-break:break-all}

/* ── PAGE 2: SERIES ── */
#page-series{overflow:hidden}
.series-toolbar{display:flex;align-items:center;gap:10px;padding:16px 28px;border-bottom:1px solid var(--border);flex-shrink:0;flex-wrap:wrap}
.series-search{background:var(--surface);border:1px solid var(--border2);border-radius:6px;color:var(--hi);font-family:'JetBrains Mono',monospace;font-size:12px;padding:8px 14px;outline:none;flex:1;min-width:180px;transition:border-color .15s}
.series-search:focus{border-color:var(--accent)}
.sf-tabs{display:flex;gap:4px}
.sf-tab{font-family:'JetBrains Mono',monospace;font-size:10px;padding:6px 12px;border-radius:6px;border:1px solid var(--border2);background:transparent;color:var(--muted);cursor:pointer;transition:all .15s}
.sf-tab.active{border-color:var(--accent);color:var(--accent);background:rgba(232,255,71,.07)}

.series-list-wrap{flex:1;overflow-y:auto;padding:20px 28px}
#series-list{display:flex;flex-direction:column;gap:8px}
.sr{display:flex;align-items:center;gap:14px;padding:14px 16px;border-radius:10px;border:1px solid var(--border);background:var(--surface);transition:border-color .15s;cursor:pointer}
.sr:hover{border-color:var(--border2)}
.sr-poster{width:40px;height:58px;border-radius:5px;object-fit:cover;background:var(--border);flex-shrink:0}
.sr-poster-ph{width:40px;height:58px;border-radius:5px;background:var(--border);display:flex;align-items:center;justify-content:center;font-size:18px;flex-shrink:0}
.sr-info{flex:1;min-width:0}
.sr-title{font-weight:700;font-size:14px;color:var(--hi);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin-bottom:6px}
.sr-bar-wrap{display:flex;align-items:center;gap:8px}
.sr-bar{flex:1;height:3px;border-radius:2px;background:var(--border2);overflow:hidden}
.sr-bar-fill{height:100%;background:var(--success);border-radius:2px;transition:width .4s}
.sr-eps{font-family:'JetBrains Mono',monospace;font-size:10px;color:var(--muted);flex-shrink:0;min-width:60px;text-align:right}
.sr-pill{font-family:'JetBrains Mono',monospace;font-size:9px;padding:2px 8px;border-radius:4px;letter-spacing:1px;flex-shrink:0}
.sr-pill.ongoing{background:rgba(71,184,255,.12);border:1px solid var(--accent2);color:var(--accent2)}
.sr-pill.ended{background:rgba(74,81,101,.2);border:1px solid var(--muted);color:var(--muted)}
.link-btn{font-family:'Syne',sans-serif;font-weight:700;font-size:11px;padding:7px 16px;border-radius:6px;border:1px solid var(--accent);background:transparent;color:var(--accent);cursor:pointer;transition:all .15s;flex-shrink:0;white-space:nowrap}
.link-btn:hover:not(:disabled){background:var(--accent);color:#000}
.link-btn:disabled{opacity:.4;cursor:not-allowed}
.link-btn.done{border-color:var(--success);color:var(--success)}
.link-btn.done:hover:not(:disabled){background:var(--success);color:#000}

/* config panel */
#cfg-panel{padding:20px 28px;border-bottom:1px solid var(--border);background:var(--surface);display:none}
.cfg-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:12px;margin-top:14px}
.cff label{display:block;font-family:'JetBrains Mono',monospace;font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:1.5px;margin-bottom:5px}
.cff input{width:100%;background:var(--bg);border:1px solid var(--border2);border-radius:6px;color:var(--hi);font-family:'JetBrains Mono',monospace;font-size:12px;padding:8px 12px;outline:none;transition:border-color .15s}
.cff input:focus{border-color:var(--accent)}

/* ── MODAL ── */
#modal-overlay{display:none;position:fixed;inset:0;background:rgba(0,0,0,.7);z-index:400;align-items:center;justify-content:center;backdrop-filter:blur(4px)}
#modal-overlay.show{display:flex}
.modal{background:var(--surface);border:1px solid var(--border2);border-radius:12px;padding:28px;width:100%;max-width:420px;margin:20px}
.modal-title{font-size:17px;font-weight:800;color:var(--hi);margin-bottom:8px}
.modal-body{font-size:13px;color:var(--text);line-height:1.6;margin-bottom:24px}
.modal-body strong{color:var(--accent)}
.modal-actions{display:flex;gap:10px;justify-content:flex-end}

/* ── PENDING READD BANNER ── */
#readd-banner{display:none;background:rgba(255,71,87,.08);border-bottom:1px solid var(--danger);padding:10px 28px}
.readd-item{display:flex;align-items:center;gap:12px;padding:6px 0;border-bottom:1px solid rgba(255,71,87,.15)}
.readd-item:last-child{border-bottom:none}
.readd-title{flex:1;font-size:13px;font-weight:700;color:var(--hi)}
.readd-sub{font-family:'JetBrains Mono',monospace;font-size:10px;color:var(--danger);margin-top:2px}
</style>
</head>
<body>

<!-- SETUP -->
<div id="setup-overlay">
<div class="setup-card">
  <div class="setup-logo"><span class="icon">🐕</span><h1>Watchdog</h1><p>PLEX → SONARR BRIDGE · SETUP</p></div>
  <div class="setup-steps">
    <div class="sst active" id="t1">1 · PLEX</div>
    <div class="sst" id="t2">2 · SONARR</div>
    <div class="sst" id="t3">3 · TMDB</div>
    <div class="sst" id="t4">4 · DONE</div>
  </div>
  <div class="spage active" id="sp1">
    <div class="stitle">Plex Connection</div>
    <div class="ssub">Your Plex server URL and token. Find token at <a href="https://support.plex.tv/articles/204059436" target="_blank">support.plex.tv</a>.</div>
    <div class="fg"><label>Plex URL</label><input id="s-pu" placeholder="http://192.168.1.x:32400" value="{{ config.PLEX_URL }}"><div class="fhint">Include http:// and port 32400</div></div>
    <div class="fg"><label>Plex Token</label><input type="password" id="s-pt" value="{{ config.PLEX_TOKEN }}"></div>
    <div class="fg"><label>TV Library Name</label><input id="s-pl" placeholder="TV Shows" value="{{ config.PLEX_TV_LIBRARY }}"><div class="fhint">Must match exactly as it appears in Plex</div></div>
    <button class="test-btn" onclick="testConn('plex')">🔌 Test Plex Connection</button>
    <div class="test-result" id="tr-plex"></div>
    <div class="snav"><div></div><button class="btn btn-primary" onclick="goPage(2)">Next → Sonarr</button></div>
  </div>
  <div class="spage" id="sp2">
    <div class="stitle">Sonarr Connection</div>
    <div class="ssub">API key in Sonarr → Settings → General → Security.</div>
    <div class="fg"><label>Sonarr URL</label><input id="s-su" placeholder="http://192.168.1.x:8989" value="{{ config.SONARR_URL }}"></div>
    <div class="fg"><label>Sonarr API Key</label><input type="password" id="s-sk" value="{{ config.SONARR_API_KEY }}"></div>
    <div class="frow">
      <div class="fg"><label>Root Folder</label><input id="s-rf" placeholder="/tv" value="{{ config.SONARR_ROOT_FOLDER }}"></div>
      <div class="fg"><label>Quality Profile</label><input id="s-qp" placeholder="HD-1080p" value="{{ config.SONARR_QUALITY_PROFILE }}"></div>
    </div>
    <div class="fg"><label>Poll Interval (seconds)</label><input type="number" id="s-pi" value="{{ config.POLL_INTERVAL }}"></div>
    <button class="test-btn" onclick="testConn('sonarr')">🔌 Test Sonarr Connection</button>
    <div class="test-result" id="tr-sonarr"></div>
    <div class="snav"><button class="btn btn-secondary" onclick="goPage(1)">← Back</button><button class="btn btn-primary" onclick="goPage(3)">Next → TMDB</button></div>
  </div>
  <div class="spage" id="sp3">
    <div class="stitle">TMDB + Debrid</div>
    <div class="ssub">TMDB checks if shows are ongoing. Free key at <a href="https://www.themoviedb.org/settings/api" target="_blank">themoviedb.org</a>. Debrid path is your mount root for repairing broken symlinks.</div>
    <div class="fg"><label>TMDB API Key</label><input type="password" id="s-tm" value="{{ config.TMDB_API_KEY }}"></div>
    <button class="test-btn" onclick="testConn('tmdb')">🔌 Test TMDB Key</button>
    <div class="test-result" id="tr-tmdb"></div>
    <div class="fg" style="margin-top:20px"><label>Debrid Mount Path</label><input id="s-dp" placeholder="/docker/zurg/mnt/zurg/shows" value="{{ config.DEBRID_PATH }}"><div class="fhint">Root folder of your debrid shows e.g. /docker/zurg/mnt/zurg/shows</div></div>
    <div class="snav"><button class="btn btn-secondary" onclick="goPage(2)">← Back</button><button class="btn btn-primary" onclick="saveSetup()">Save & Start →</button></div>
  </div>
  <div class="spage" id="sp4">
    <div style="text-align:center;padding:40px 0">
      <div style="font-size:64px;margin-bottom:20px">✅</div>
      <div class="stitle" style="font-size:24px;margin-bottom:10px">All Set!</div>
      <div class="ssub">Watchdog will now scan your Plex library and add all continuing shows to Sonarr.</div>
      <button class="btn btn-success" style="margin-top:24px;padding:14px 36px;font-size:15px" onclick="launchApp()">🚀 Start Scanning</button>
    </div>
  </div>
</div>
</div>

<!-- LOADING -->
<div id="loading"><div class="spinner"></div><div class="loading-title" id="lt">PLEASE WAIT</div><div class="loading-sub" id="ls"></div></div>

<!-- APP -->
<div id="app">
  <header>
    <div class="logo">
      <div class="logo-icon">🐕</div>
      <div><div class="logo-text">Watchdog</div><div class="logo-sub">PLEX → SONARR</div></div>
    </div>
    <div class="header-mid">
      <button class="nav-tab active" onclick="showPage('dashboard',this)">📡 Dashboard</button>
      <button class="nav-tab" onclick="showPage('series',this);loadSeries()">📺 Series</button>
    </div>
    <div class="header-right">
      <div class="status-pill" id="status-pill"><div class="dot"></div> IDLE</div>
      <button class="btn btn-secondary btn-sm" onclick="toggleCfg()">⚙ Config</button>
      <button class="btn btn-secondary btn-sm" id="stop-btn" style="display:none" onclick="stopWatchdog()">■ Stop</button>
    </div>
  </header>

  <!-- config panel -->
  <div id="cfg-panel">
    <div style="font-size:11px;font-family:'JetBrains Mono',monospace;color:var(--muted);text-transform:uppercase;letter-spacing:2px">Configuration</div>
    <div class="cfg-grid">
      <div class="cff"><label>Plex URL</label><input id="c-pu"></div>
      <div class="cff"><label>Plex Token</label><input type="password" id="c-pt"></div>
      <div class="cff"><label>TV Library</label><input id="c-pl"></div>
      <div class="cff"><label>Sonarr URL</label><input id="c-su"></div>
      <div class="cff"><label>Sonarr API Key</label><input type="password" id="c-sk"></div>
      <div class="cff"><label>Root Folder</label><input id="c-rf"></div>
      <div class="cff"><label>Quality Profile</label><input id="c-qp"></div>
      <div class="cff"><label>TMDB Key</label><input type="password" id="c-tm"></div>
      <div class="cff"><label>Debrid Path</label><input id="c-dp"></div>
      <div class="cff"><label>Poll Interval (s)</label><input type="number" id="c-pi"></div>
      <div class="cff" style="display:flex;flex-direction:column;justify-content:flex-end">
        <label>Auto Link Episodes</label>
        <label style="display:flex;align-items:center;gap:10px;padding:8px 0;cursor:pointer">
          <input type="checkbox" id="c-al" style="width:16px;height:16px;accent-color:var(--accent);cursor:pointer">
          <span style="font-family:'JetBrains Mono',monospace;font-size:12px;color:var(--text)">Auto-sync all series symlinks on every scan (off = manual per series only)</span>
        </label>
      </div>
    </div>
    <div style="margin-top:14px;display:flex;gap:10px">
      <button class="btn btn-primary btn-sm" onclick="saveCfg()">Save</button>
      <button class="btn btn-secondary btn-sm" onclick="toggleCfg()">Cancel</button>
    </div>
  </div>

  <!-- PAGE 1: Dashboard -->
  <div class="page active" id="page-dashboard">
    <div id="scan-progress">
      <div class="scan-status-row">
        <div class="scan-status-text" id="scan-status-text">🔍 Scanning Plex library...</div>
      </div>
      <div class="progress-track"><div class="progress-fill" id="progress-fill"></div></div>
    </div>
    <div class="console-wrap">
      <div class="console-bar">
        <div class="cdots"><div class="cdot r"></div><div class="cdot y"></div><div class="cdot g"></div></div>
        <div class="ctitle">watchdog.log — live</div>
        <button class="cclear" onclick="clearLog()">clear</button>
      </div>
      <div id="console-out"></div>
    </div>
  </div>

  <!-- PAGE 2: Series -->
  <div class="page" id="page-series">
    <div class="series-toolbar">
      <input class="series-search" id="series-search" placeholder="Search series..." oninput="filterSeries()">
      <div class="sf-tabs">
        <button class="sf-tab active" onclick="setSF('all',this)">All</button>
        <button class="sf-tab" onclick="setSF('ongoing',this)">Ongoing</button>
        <button class="sf-tab" onclick="setSF('ended',this)">Ended</button>
        <button class="sf-tab" onclick="setSF('pending',this)">Pending</button>
      </div>
      <button class="btn btn-secondary btn-sm" onclick="loadSeries()">↻ Refresh</button>
      <button class="btn btn-primary btn-sm" onclick="linkAll()">🔍 Rescan All</button>
    </div>
    <div class="series-list-wrap"><div id="series-list"></div></div>
  </div>
</div>

<!-- RE-ADD BANNER -->
<div id="readd-banner"></div>

<!-- DELETE CONFIRM MODAL -->
<div id="modal-overlay">
  <div class="modal">
    <div class="modal-title">Delete Series?</div>
    <div class="modal-body">Remove <strong id="modal-series-title"></strong> from Sonarr?<br><br>
    If this show appears in Plex again, you'll be asked before it's re-added.</div>
    <div class="modal-actions">
      <button class="btn btn-secondary btn-sm" onclick="closeModal()">Cancel</button>
      <button class="btn btn-danger btn-sm" onclick="doDelete()">Delete</button>
    </div>
  </div>
</div>

<script>
let allSeries=[], seriesFilter='all', logStreamStarted=false;

// ── Boot ──────────────────────────────────────────────────────────────────────
(async()=>{
  try{
    const r=await fetch('/api/status');
    if(!r.ok){document.body.innerHTML='<div style="color:#ff4757;font-family:monospace;padding:40px">API error: '+r.status+'</div>';return}
    const d=await r.json();
    if(!d.setupComplete){
      document.getElementById('setup-overlay').classList.add('show');
      return;
    }
    showApp();
    await replayLogs();
    startLogStream();
    if(d.initialScanDone){
      setPill('running','🐕 RUNNING');
      document.getElementById('stop-btn').style.display='block';
    } else {
      startInitialScan();
    }
  }catch(e){
    document.body.innerHTML='<div style="color:#ff4757;font-family:monospace;padding:40px">Boot error: '+e.message+'<br><br>Check that the container is running:<br>docker logs watchdog</div>';
  }
})();

function showApp(){
  document.getElementById('app').style.display='flex';
  document.getElementById('app').style.flexDirection='column';
  document.getElementById('app').style.flex='1';
  loadCfgPanel();
}

// ── Page nav ──────────────────────────────────────────────────────────────────
function showPage(name,el){
  document.querySelectorAll('.page').forEach(p=>p.classList.remove('active'));
  document.querySelectorAll('.nav-tab').forEach(t=>t.classList.remove('active'));
  document.getElementById('page-'+name).classList.add('active');
  el.classList.add('active');
}

// ── Logs ──────────────────────────────────────────────────────────────────────
async function replayLogs(){
  try{
    const entries=await(await fetch('/api/logs/history')).json();
    if(entries.length){
      // Add oldest first so newest ends up at top after insertBefore
      entries.forEach(e=>addLog(e.time,e.level,e.msg));
    }
  }catch(e){}
}
function startLogStream(){
  if(logStreamStarted)return;
  logStreamStarted=true;
  const es=new EventSource('/api/logs/stream');
  es.onmessage=e=>{const d=JSON.parse(e.data);if(!d.ping)addLog(d.time,d.level,d.msg)};
}
function addLog(t,l,m){
  const out=document.getElementById('console-out');
  const line=document.createElement('div');
  line.className='ll';
  line.innerHTML=`<span class="lt">${t}</span><span class="lv ${l}">${l}</span><span class="lm">${esc(m)}</span>`;
  out.insertBefore(line, out.firstChild);
  // Keep only 10 newest
  while(out.children.length>10) out.removeChild(out.lastChild);
}
function clearLog(){document.getElementById('console-out').innerHTML=''}
function esc(s){return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')}

// ── Setup wizard ──────────────────────────────────────────────────────────────
let curPage=1;
function goPage(n){
  document.getElementById(`sp${curPage}`).classList.remove('active');
  document.getElementById(`t${curPage}`).classList.remove('active');
  if(n>curPage)document.getElementById(`t${curPage}`).classList.add('done');
  curPage=n;
  document.getElementById(`sp${n}`).classList.add('active');
  document.getElementById(`t${n}`).classList.add('active');
}
function setupData(){
  return{
    PLEX_URL:document.getElementById('s-pu').value.trim(),
    PLEX_TOKEN:document.getElementById('s-pt').value.trim(),
    PLEX_TV_LIBRARY:document.getElementById('s-pl').value.trim(),
    SONARR_URL:document.getElementById('s-su').value.trim(),
    SONARR_API_KEY:document.getElementById('s-sk').value.trim(),
    SONARR_ROOT_FOLDER:document.getElementById('s-rf').value.trim(),
    SONARR_QUALITY_PROFILE:document.getElementById('s-qp').value.trim(),
    TMDB_API_KEY:document.getElementById('s-tm').value.trim(),
    DEBRID_PATH:document.getElementById('s-dp').value.trim(),
    POLL_INTERVAL:parseInt(document.getElementById('s-pi').value)||30,
  };
}
async function testConn(svc){
  showTR(svc,null,'Testing...');
  const r=await fetch('/api/test-connections',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(setupData())});
  const d=await r.json();
  if(svc==='plex')  d.plex   ?showTR('plex',  true,'✅ Connected!')  :showTR('plex',  false,`❌ ${d.errors.plex  ||'Failed'}`);
  if(svc==='sonarr')d.sonarr ?showTR('sonarr',true,'✅ Connected!')  :showTR('sonarr',false,`❌ ${d.errors.sonarr||'Failed'}`);
  if(svc==='tmdb')  d.tmdb   ?showTR('tmdb',  true,'✅ Valid!')       :showTR('tmdb',  false,`❌ ${d.errors.tmdb  ||'Invalid'}`);
}
function showTR(svc,ok,msg){
  const el=document.getElementById(`tr-${svc}`);
  el.className='test-result show '+(ok===null?'':ok?'ok':'fail');
  el.textContent=msg;
}
async function saveSetup(){
  await fetch('/api/setup',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(setupData())});
  goPage(4);
}
function launchApp(){
  document.getElementById('setup-overlay').classList.remove('show');
  showApp();
  startLogStream();
  startInitialScan();
}

// ── Initial scan ──────────────────────────────────────────────────────────────
async function startInitialScan(){
  // Check if scan already running before triggering
  const check=await(await fetch('/api/status')).json();
  if(check.scanRunning||check.watchdogRunning){
    setPill('running','🐕 RUNNING');
    document.getElementById('stop-btn').style.display='block';
    return;
  }
  document.getElementById('scan-progress').style.display='block';
  setPill('scanning','🔍 SCANNING');
  addLog(now(),'INFO','🚀 Starting initial Plex scan — adding continuing shows to Sonarr...');
  await fetch('/api/start-initial-scan',{method:'POST'});
  // Poll status until watchdog is running
  const poll=setInterval(async()=>{
    const d=await(await fetch('/api/status')).json();
    if(d.watchdogRunning){
      clearInterval(poll);
      document.getElementById('scan-progress').style.display='none';
      setPill('running','🐕 RUNNING');
      document.getElementById('stop-btn').style.display='block';
    }
  },2000);
}

// ── Config panel ──────────────────────────────────────────────────────────────
async function loadCfgPanel(){
  const cfg=await(await fetch('/api/config')).json();
  document.getElementById('c-pu').value=cfg.PLEX_URL||'';
  document.getElementById('c-pt').value=cfg.PLEX_TOKEN||'';
  document.getElementById('c-pl').value=cfg.PLEX_TV_LIBRARY||'';
  document.getElementById('c-su').value=cfg.SONARR_URL||'';
  document.getElementById('c-sk').value=cfg.SONARR_API_KEY||'';
  document.getElementById('c-rf').value=cfg.SONARR_ROOT_FOLDER||'';
  document.getElementById('c-qp').value=cfg.SONARR_QUALITY_PROFILE||'';
  document.getElementById('c-tm').value=cfg.TMDB_API_KEY||'';
  document.getElementById('c-dp').value=cfg.DEBRID_PATH||'';
  document.getElementById('c-pi').value=cfg.POLL_INTERVAL||30;
  document.getElementById('c-al').checked=cfg.AUTO_SYMLINK===true;
}
function toggleCfg(){const p=document.getElementById('cfg-panel');p.style.display=p.style.display==='block'?'none':'block'}
async function saveCfg(){
  await fetch('/api/config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({
    PLEX_URL:document.getElementById('c-pu').value,
    PLEX_TOKEN:document.getElementById('c-pt').value,
    PLEX_TV_LIBRARY:document.getElementById('c-pl').value,
    SONARR_URL:document.getElementById('c-su').value,
    SONARR_API_KEY:document.getElementById('c-sk').value,
    SONARR_ROOT_FOLDER:document.getElementById('c-rf').value,
    SONARR_QUALITY_PROFILE:document.getElementById('c-qp').value,
    TMDB_API_KEY:document.getElementById('c-tm').value,
    DEBRID_PATH:document.getElementById('c-dp').value,
    POLL_INTERVAL:parseInt(document.getElementById('c-pi').value),
    AUTO_SYMLINK:document.getElementById('c-al').checked,
  })});
  toggleCfg();
}

// ── Watchdog ──────────────────────────────────────────────────────────────────
async function stopWatchdog(){
  await fetch('/api/watchdog/stop',{method:'POST'});
  setPill('idle','■ STOPPED');
  document.getElementById('stop-btn').style.display='none';
}

// ── Series page ───────────────────────────────────────────────────────────────
async function loadSeries(){
  document.getElementById('series-list').innerHTML='<div style="color:var(--muted);font-family:\\'JetBrains Mono\\',monospace;font-size:12px;padding:20px 0">Loading from Sonarr...</div>';
  try{
    allSeries=await(await fetch('/api/series/list')).json();
    renderSeries();
  }catch(e){
    document.getElementById('series-list').innerHTML='<div style="color:var(--danger);font-family:\\'JetBrains Mono\\',monospace;font-size:12px;padding:20px 0">Failed to load series.</div>';
  }
}
function setSF(f,el){
  seriesFilter=f;
  document.querySelectorAll('.sf-tab').forEach(t=>t.classList.remove('active'));
  el.classList.add('active');
  renderSeries();
}
function filterSeries(){renderSeries()}
function ongoing(s){return['continuing','returning series','in production','planned','pilot'].includes((s.status||'').toLowerCase())}
function renderSeries(){
  const q=(document.getElementById('series-search').value||'').toLowerCase();
  let list=allSeries.filter(s=>!q||s.title.toLowerCase().includes(q));
  if(seriesFilter==='ongoing')list=list.filter(s=>ongoing(s));
  if(seriesFilter==='ended')  list=list.filter(s=>!ongoing(s));
  if(seriesFilter==='pending')list=list.filter(s=>s.episodeFileCount<s.episodeCount);
  const el=document.getElementById('series-list');
  if(!list.length){el.innerHTML='<div style="color:var(--muted);font-family:\\'JetBrains Mono\\',monospace;font-size:12px;padding:20px 0">No series found.</div>';return}
  el.innerHTML=list.map(s=>{
    const pct=s.episodeCount>0?Math.round(s.episodeFileCount/s.episodeCount*100):0;
    const done=s.episodeCount>0&&s.episodeFileCount>=s.episodeCount;
    const poster=s.poster?`<img class="sr-poster" src="${s.poster}" loading="lazy">`:`<div class="sr-poster-ph">📺</div>`;
    return`<div class="sr" id="sr-${s.id}">
      ${poster}
      <div class="sr-info">
        <div class="sr-title">${esc(s.title)}</div>
        <div class="sr-bar-wrap">
          <div class="sr-bar"><div class="sr-bar-fill" style="width:${pct}%"></div></div>
          <div class="sr-eps">${s.episodeFileCount}/${s.episodeCount} eps</div>
        </div>
      </div>
      <div class="sr-pill ${ongoing(s)?'ongoing':'ended'}">${ongoing(s)?'ONGOING':'ENDED'}</div>
      <button class="link-btn ${done?'done':''}" id="lb-${s.id}" onclick="linkSeries(${s.id},'${esc(s.title).replace(/'/g,"\\\\'")}')">
        ${done?'✓ Done':'Rescan'}
        ${done?'✓ Done':'Rescan'}
      </button>
      <button class="link-btn" style="border-color:var(--danger);color:var(--danger)" onclick="event.stopPropagation();deleteTarget={id:${s.id},title:esc(s.title)};document.getElementById('modal-series-title').textContent=s.title;document.getElementById('modal-overlay').classList.add('show')">🗑</button>
    </div>`;
}
async function linkSeries(id,title){
  const btn=document.getElementById(`lb-${id}`);
  btn.disabled=true;btn.textContent='⏳ Rescanning...';
  showPage('dashboard',document.querySelector('.nav-tab'));
  addLog(now(),'INFO',`🔍 Rescanning: ${title}`);
  await fetch(`/api/series/${id}/link`,{method:'POST'});
  setTimeout(async()=>{allSeries=await(await fetch('/api/series/list')).json();renderSeries()},4000);
}
async function linkAll(){
  const btn=event.target;
  btn.disabled=true;btn.textContent='⏳ Linking all...';
  showPage('dashboard',document.querySelector('.nav-tab'));
  addLog(now(),'INFO','🔍 Triggering rescan for all series...');
  await fetch('/api/series/link-all',{method:'POST'});
  setTimeout(()=>{btn.disabled=false;btn.textContent='🔍 Rescan All'},2000);
  setTimeout(async()=>{allSeries=await(await fetch('/api/series/list')).json();renderSeries()},8000);
}

// ── Helpers ───────────────────────────────────────────────────────────────────
function setPill(state,label){
  const p=document.getElementById('status-pill');
  p.className='status-pill '+(state==='running'?'running':state==='scanning'?'scanning':'');
  p.innerHTML=`<div class="dot ${state!=='idle'?'pulse':''}"></div> ${label}`;
}
function now(){return new Date().toTimeString().slice(0,8)}

// ── Delete & re-add ───────────────────────────────────────────────────────────
let deleteTarget={id:null,title:''};

function confirmDelete(id,title,e){
  e.stopPropagation();
  deleteTarget={id,title};
  document.getElementById('modal-series-title').textContent=title;
  document.getElementById('modal-overlay').classList.add('show');
}
function closeModal(){document.getElementById('modal-overlay').classList.remove('show')}

async function doDelete(){
  closeModal();
  const {id,title}=deleteTarget;
  addLog(now(),'INFO',`🗑 Deleting '${title}' from Sonarr...`);
  const r=await fetch(`/api/series/${id}/delete`,{method:'POST'});
  const d=await r.json();
  if(d.ok){
    addLog(now(),'INFO',`✅ '${title}' deleted`);
    allSeries=allSeries.filter(s=>s.id!==id);
    renderSeries();
  } else {
    addLog(now(),'ERROR',`❌ Delete failed: ${d.error}`);
  }
}

async function checkPendingReadd(){
  const r=await fetch('/api/pending-readd');
  const items=await r.json();
  const banner=document.getElementById('readd-banner');
  if(!items.length){banner.style.display='none';return}
  banner.style.display='block';
  banner.innerHTML=`
    <div style="font-family:'JetBrains Mono',monospace;font-size:10px;color:var(--danger);text-transform:uppercase;letter-spacing:1px;margin-bottom:8px">⚠ Previously deleted shows detected in Plex</div>
    ${items.map(item=>`
      <div class="readd-item">
        <div>
          <div class="readd-title">${esc(item.title)}</div>
          <div class="readd-sub">Found in Plex — was previously deleted from Sonarr</div>
        </div>
        <button class="btn btn-success btn-sm" onclick="confirmReadd(${item.tvdbId},'${esc(item.title).replace(/'/g,"\\'")}')">Re-add</button>
        <button class="btn btn-secondary btn-sm" onclick="dismissReadd(${item.tvdbId})">Ignore</button>
      </div>`).join('')}`;
}

async function confirmReadd(tvdbId,title){
  addLog(now(),'INFO',`✅ Re-adding '${title}' to Sonarr...`);
  await fetch('/api/pending-readd/confirm',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({tvdbId,title})});
  checkPendingReadd();
}
async function dismissReadd(tvdbId){
  await fetch('/api/pending-readd/dismiss',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({tvdbId})});
  checkPendingReadd();
}

// Poll for pending re-adds every 30s
setInterval(checkPendingReadd, 30000);
checkPendingReadd();
</script>
</body>
</html>
"""


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
    "AUTO_SYMLINK":              False,
    "IGNORED_SERIES":         [],   # tvdbIds the user has deleted and chosen not to re-add
    "DEBRID_PATH":            "/docker/zurg/mnt/zurg/shows",   # root folder of debrid mount
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
log_queue        = queue.Queue()
log_history      = []
LOG_HISTORY_MAX  = 2000
watchdog_running = False
watchdog_thread  = None
scan_running     = False
pending_readd    = []   # shows deleted but found again in Plex, waiting user confirmation

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
    """
    Add a continuing show to Sonarr:
    1. Add with monitor=all so all existing episodes are marked as missing
    2. Symlinks will be created by the symlink pass
    3. Sonarr rescan will import the symlinked files, marking them as downloaded
    """
    try:
        results = sonarr_get(f"series/lookup?term=tvdb:{tvdb_id}")
        if not results:
            logger.warning(f"  ⚠ No Sonarr lookup result for '{title}' (TVDB:{tvdb_id})")
            return False
        lookup = results[0]

        # Monitor all seasons and episodes — marks everything missing so
        # Sonarr knows what to look for when we import the symlinks
        seasons = [dict(s, monitored=True) for s in lookup.get("seasons", [])]

        payload = {
            "title":            lookup["title"],
            "tvdbId":           tvdb_id,
            "qualityProfileId": quality_profile_id,
            "rootFolderPath":   CONFIG["SONARR_ROOT_FOLDER"],
            "seasonFolder":     True,
            "monitored":        True,
            "addOptions": {
                "searchForMissingEpisodes":     False,  # don't download — we'll symlink
                "searchForCutoffUnmetEpisodes": False,
                "monitor":                      "all",  # mark all episodes as missing
            },
            "seasons": seasons,
            "images":  lookup.get("images", []),
            "path":    f"{CONFIG['SONARR_ROOT_FOLDER']}/{lookup['title']}",
        }
        added = sonarr_post("series", payload)
        sonarr_id = added.get("id")
        logger.info(f"  ✅ Added to Sonarr: {lookup['title']} (TVDB:{tvdb_id}) — all episodes marked missing")

        # Give Sonarr a moment to settle then trigger a rescan so it picks up symlinks
        if sonarr_id:
            time.sleep(2)
            logger.info(f"  🔍 Triggering rescan to import any existing symlinks for '{lookup['title']}'")
            rescan_series(sonarr_id, lookup["title"])

        return True
    except requests.HTTPError as e:
        if e.response.status_code == 400:
            logger.debug(f"  Already in Sonarr: {title}")
        else:
            logger.error(f"  ❌ Failed to add '{title}': {e}")
        return False

# ── Symlink health check ─────────────────────────────────────────────────────
def check_and_repair_symlinks(sonarr_map):
    """
    Walk every Sonarr series folder, find broken symlinks, then search the
    debrid directory for the file and recreate the symlink pointing to it.
    """
    repaired = 0
    removed  = 0

    # Build a filename -> full path lookup by searching debrid directory
    debrid_path = CONFIG.get("DEBRID_PATH", "").strip()
    plex_file_map = {}  # basename -> full path
    if not debrid_path or not os.path.isdir(debrid_path):
        logger.warning(f"  ⚠ Debrid path not set or not found — cannot repair symlinks")
        return 0

    import subprocess

    logger.info(f"🔧 Building debrid file index from: {debrid_path}")
    try:
        show_folders = os.listdir(debrid_path)
        logger.info(f"  📁 Found {len(show_folders)} folders in debrid root")
        for show_folder in show_folders:
            show_path = os.path.join(debrid_path, show_folder)
            if not os.path.isdir(show_path):
                continue
            folder_files = 0
            for root, dirs, files in os.walk(show_path):
                for fname in files:
                    plex_file_map[fname] = os.path.join(root, fname)
                    folder_files += 1
            if folder_files:
                logger.debug(f"    📂 {show_folder} — {folder_files} files indexed")
        logger.info(f"  ✅ Debrid index built — {len(plex_file_map)} total files indexed")
    except Exception as e:
        logger.warning(f"  ⚠ Could not scan debrid path: {e}")
        return 0

    def find_in_debrid(filename, series_title):
        """
        First try exact filename match from pre-built index.
        If not found, use ls | grep on the debrid folder filtered by series name.
        """
        # Step 1: exact match from index
        if filename in plex_file_map:
            logger.debug(f"    ✓ Found in index: {filename}")
            return plex_file_map[filename]

        # Step 2: grep debrid folder for series name
        search_term = series_title.split('(')[0].strip()
        logger.info(f"    🔍 Not in index — searching debrid for '{search_term}'...")
        try:
            result = subprocess.run(
                f"ls {repr(debrid_path)} | grep -i {repr(search_term)}",
                shell=True, capture_output=True, text=True
            )
            matched_folders = [f for f in result.stdout.strip().splitlines() if f]
            if not matched_folders:
                logger.info(f"    ❌ No debrid folder matched '{search_term}'")
                return None
            logger.info(f"    📁 Matched {len(matched_folders)} folder(s): {', '.join(matched_folders)}")
            for folder in matched_folders:
                folder_path = os.path.join(debrid_path, folder)
                logger.info(f"    🔎 Searching in: {folder_path}")
                for root, dirs, files in os.walk(folder_path):
                    for fname in files:
                        if fname == filename:
                            full = os.path.join(root, fname)
                            logger.info(f"    ✅ Found: {full}")
                            return full
            logger.info(f"    ❌ '{filename}' not found in any matched folder")
        except Exception as e:
            logger.warning(f"    ⚠ grep search failed for '{series_title}': {e}")
        return None

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
                    continue  # symlink is healthy

                # Broken symlink — try to find replacement in Plex
                old_target = os.readlink(fpath)
                try:
                    os.remove(fpath)
                    removed += 1
                except Exception as e:
                    logger.warning(f"  ⚠ Could not remove broken symlink {fname}: {e}")
                    continue

                # Look up by filename in debrid, using series name for grep fallback
                new_target = find_in_debrid(fname, series.get("title", ""))
                if new_target and os.path.exists(new_target):
                    try:
                        os.symlink(new_target, fpath)
                        repaired += 1
                        logger.info(f"  🔧 Repaired symlink: {fname}")
                        logger.debug(f"      {old_target} → {new_target}")
                    except Exception as e:
                        logger.warning(f"  ⚠ Could not recreate symlink for {fname}: {e}")
                else:
                    logger.info(f"  🗑 Removed broken symlink (no Plex match): {fname}")

    if repaired or removed:
        logger.info(f"🔧 Symlink repair — {repaired} repaired, {removed - repaired} removed (no match found)")
    else:
        logger.debug("🔧 Symlink health check — all symlinks OK")
    return removed

# ── Symlink + Rescan ──────────────────────────────────────────────────────────
def find_debrid_files_for_series(series_title):
    """Search debrid folder for video files matching this series using ls | grep."""
    import subprocess, re
    debrid_path = CONFIG.get("DEBRID_PATH", "").strip()
    if not debrid_path or not os.path.isdir(debrid_path):
        logger.warning(f"  ⚠ Debrid path not found: {debrid_path}")
        return []
    VIDEO_EXTS = {".mkv", ".mp4", ".avi", ".m4v", ".ts", ".mov", ".wmv"}
    search_term = series_title.split("(")[0].strip()
    logger.info(f"  🔍 Searching debrid for '{search_term}' in {debrid_path}")
    try:
        result = subprocess.run(
            f"ls {repr(debrid_path)} | grep -i {repr(search_term)}",
            shell=True, capture_output=True, text=True
        )
        matched_folders = [f.strip() for f in result.stdout.strip().splitlines() if f.strip()]
    except Exception as e:
        logger.warning(f"  ⚠ Debrid search failed for '{series_title}': {e}")
        return []
    if not matched_folders:
        logger.debug(f"  ❌ No debrid folder matched '{search_term}'")
        return []
    logger.info(f"  📁 Matched: {', '.join(matched_folders)}")
    files = []
    for folder in matched_folders:
        folder_path = os.path.join(debrid_path, folder)
        count = 0
        for root, dirs, fnames in os.walk(folder_path):
            for fname in fnames:
                if os.path.splitext(fname)[1].lower() in VIDEO_EXTS:
                    files.append(os.path.join(root, fname))
                    count += 1
        logger.info(f"    📼 {folder} — {count} video files found")
    return files

def symlink_series(plex_show, sonarr_series):
    """
    Find video files in debrid for this series and symlink into Sonarr folder.
    Recreates missing directories and symlinks automatically.
    """
    import re
    title       = sonarr_series["title"]
    series_path = sonarr_series.get("path", "")
    if not series_path:
        logger.warning(f"  ⚠ No Sonarr path for '{title}' — skipping")
        return 0

    debrid_files = find_debrid_files_for_series(title)
    if not debrid_files:
        return 0

    # Recreate series root if missing
    if not os.path.exists(series_path):
        logger.info(f"  📁 Recreating missing series directory: {series_path}")
    os.makedirs(series_path, exist_ok=True)
    os.chmod(series_path, 0o777)

    created = 0
    skipped = 0
    repaired = 0

    for src in debrid_files:
        filename = os.path.basename(src)

        # Detect season number from folder name or filename
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

        # Recreate season dir if missing
        if not os.path.exists(season_dir):
            logger.info(f"    📁 Recreating missing season directory: {season_dir}")
        os.makedirs(season_dir, exist_ok=True)
        os.chmod(season_dir, 0o777)

        dst = os.path.join(season_dir, filename)

        # Valid symlink pointing to same src — skip
        if os.path.islink(dst) and os.path.exists(dst):
            if os.readlink(dst) == src:
                skipped += 1
                continue
            # Target changed — update
            logger.info(f"    🔄 Updating symlink (target changed): {filename}")
            os.remove(dst)
            repaired += 1

        # Broken symlink or missing — recreate
        elif os.path.islink(dst) and not os.path.exists(dst):
            logger.info(f"    🔧 Recreating broken symlink: {filename}")
            os.remove(dst)
            repaired += 1

        # Missing entirely — will be created below
        elif os.path.exists(dst):
            # Real file already exists — skip
            skipped += 1
            continue

        try:
            os.symlink(src, dst)
            created += 1
            if repaired:
                logger.info(f"    🔗 Recreated: {filename} -> {src}")
            else:
                logger.info(f"    🔗 Symlinked: {filename}")
            logger.debug(f"       -> {src}")
        except Exception as e:
            logger.warning(f"    ⚠ Symlink failed '{filename}': {e}")

    total_changed = created + repaired
    if total_changed:
        logger.info(f"  ✅ '{title}' — {created} new, {repaired} recreated, {skipped} unchanged")
        rescan_series(sonarr_series["id"], title)
    else:
        logger.debug(f"  ✓ '{title}' — {skipped} symlinks up to date")
    return created



def rescan_series(sonarr_series_id, title):
    """Tell Sonarr to rescan disk for a series."""
    try:
        sonarr_post("command", {"name": "RescanSeries", "seriesId": sonarr_series_id})
        logger.info(f"  🔍 Rescan triggered for '{title}'")
        return True
    except Exception as e:
        logger.warning(f"  ⚠ Rescan failed for '{title}': {e}")
        return False

def symlink_and_rescan_all(plex, sonarr_map):
    """
    For every series in Sonarr, find its files in the debrid folder and
    symlink them in. Debrid paths are used directly so changes are always fresh.
    """
    logger.info("🔗 Starting debrid symlink pass...")
    total_new = 0

    for tvdb_id, sonarr_series in sonarr_map.items():
        created = symlink_series(None, sonarr_series)
        total_new += created
        if created:
            time.sleep(0.3)

    if total_new:
        logger.info(f"✅ Symlink pass complete — {total_new} new symlinks created")
    else:
        logger.debug("✅ Symlink pass — no new symlinks needed")

def rescan_all_series(sonarr_map):
    """Trigger disk rescan for all series without symlinking."""
    logger.info("🔍 Triggering rescan for all series...")
    for tvdb_id, series in sonarr_map.items():
        rescan_series(series["id"], series["title"])
        time.sleep(0.2)
    logger.info(f"✅ Rescan triggered for {len(sonarr_map)} series")

# ── Initial Plex scan ─────────────────────────────────────────────────────────
def do_initial_scan():
    """Scan Plex, add only continuing shows to Sonarr, then start watchdog."""
    global watchdog_running, watchdog_thread, scan_running
    if scan_running:
        logger.warning("⚠ Scan already in progress — ignoring duplicate request")
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

        if tvdb_id in CONFIG.get("IGNORED_SERIES", []):
            logger.debug(f"  ⏭ '{show.title}' is in ignore list — skipping")
            continue

        logger.info(f"  📡 Adding continuing show: {show.title} [{status}]")
        add_series_to_sonarr(tvdb_id, show.title, quality_profile_id)
        added += 1
        time.sleep(0.3)

    logger.info(f"✅ Initial scan done — {added} added, {skipped_exists} already in Sonarr, {skipped_ended} ended/skipped")

    # Always symlink episodes; AUTO_SYMLINK controls whether rescan fires
    logger.info("🔗 Symlinking episodes for all series...")
    sonarr_map = get_sonarr_series_map()
    symlink_and_rescan_all(plex, sonarr_map)

    CONFIG["INITIAL_SCAN_DONE"] = True
    save_config_to_disk(CONFIG)
    scan_running = False
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
                # Check if user previously deleted this series
                if tvdb_id in CONFIG.get("IGNORED_SERIES", []):
                    already_pending = any(p["tvdbId"] == tvdb_id for p in pending_readd)
                    if not already_pending:
                        logger.info(f"⚠ '{show.title}' was previously deleted — awaiting user confirmation to re-add")
                        pending_readd.append({"tvdbId": tvdb_id, "title": show.title, "status": status})
                    continue
                logger.info(f"🆕 New continuing show in Plex: {show.title} [{status}]")
                add_series_to_sonarr(tvdb_id, show.title, quality_profile_id)

            # Step 2: always repair broken symlinks
            sonarr_map = get_sonarr_series_map()
            broken = check_and_repair_symlinks(sonarr_map)

            # Step 3: only auto-symlink all if user enabled the toggle
            if CONFIG.get("AUTO_SYMLINK", False):
                logger.info("🔗 Auto-symlink enabled — syncing all series...")
                symlink_and_rescan_all(plex, sonarr_map)
            elif broken:
                logger.info("🔍 Rescanning series with repaired symlinks...")
                for sid, series in sonarr_map.items():
                    path = series.get("path", "")
                    if path and os.path.isdir(path):
                        rescan_series(series["id"], series["title"])
                        time.sleep(0.2)
            else:
                logger.debug("💤 Auto-symlink off — sync series manually from the Series page")

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
        symlink_and_rescan_all(plex, sonarr_map)
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
    from jinja2 import Template
    tmpl = Template(INDEX_HTML)
    return tmpl.render(config=CONFIG)

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
        "scanRunning":      scan_running,
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
    """Symlink Plex files into Sonarr folder then trigger rescan."""
    def run():
        try:
            series  = sonarr_get(f"series/{sonarr_id}")
            title   = series["title"]
            tvdb_id = series.get("tvdbId")
            logger.info(f"🔗 Symlinking files for: {title}")
            plex   = PlexServer(CONFIG["PLEX_URL"], CONFIG["PLEX_TOKEN"])
            tv_lib = plex.library.section(CONFIG["PLEX_TV_LIBRARY"])
            plex_show = None
            for show in tv_lib.all():
                for guid in show.guids:
                    if "tvdb://" in guid.id:
                        try:
                            if int(guid.id.replace("tvdb://", "")) == tvdb_id:
                                plex_show = show; break
                        except ValueError: pass
                if plex_show: break
            if not plex_show:
                logger.warning(f"  ⚠ '{title}' not found in Plex")
                return
            symlink_series(plex_show, series)
            rescan_series(sonarr_id, title)
        except Exception as e:
            logger.error(f"Symlink/rescan failed for series {sonarr_id}: {e}")
    threading.Thread(target=run, daemon=True).start()
    return jsonify({"ok": True})

@app.route("/api/series/link-all", methods=["POST"])
def api_series_link_all():
    """Symlink all Plex files into Sonarr folders then rescan."""
    def run():
        try:
            plex       = PlexServer(CONFIG["PLEX_URL"], CONFIG["PLEX_TOKEN"])
            sonarr_map = get_sonarr_series_map()
            symlink_and_rescan_all(plex, sonarr_map)
        except Exception as e:
            logger.error(f"Bulk symlink failed: {e}")
    threading.Thread(target=run, daemon=True).start()
    return jsonify({"ok": True})

@app.route("/api/series/<int:sonarr_id>/delete", methods=["POST"])
def api_series_delete(sonarr_id):
    """Delete a series from Sonarr and add its tvdbId to the ignored list."""
    try:
        series  = sonarr_get(f"series/{sonarr_id}")
        tvdb_id = series.get("tvdbId")
        title   = series["title"]
        # Delete from Sonarr (deleteFiles=False — don't touch actual files/symlinks)
        requests.delete(
            f"{CONFIG['SONARR_URL']}/api/v3/series/{sonarr_id}",
            headers={"X-Api-Key": CONFIG["SONARR_API_KEY"]},
            params={"deleteFiles": "false", "addImportListExclusion": "false"},
            timeout=10
        ).raise_for_status()
        # Add to ignored list
        ignored = CONFIG.get("IGNORED_SERIES", [])
        if tvdb_id and tvdb_id not in ignored:
            ignored.append(tvdb_id)
            CONFIG["IGNORED_SERIES"] = ignored
            save_config_to_disk(CONFIG)
        logger.info(f"🗑 Deleted '{title}' from Sonarr and added to ignore list (TVDB:{tvdb_id})")
        return jsonify({"ok": True, "title": title})
    except Exception as e:
        logger.error(f"Delete failed for series {sonarr_id}: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/api/pending-readd", methods=["GET"])
def api_pending_readd():
    return jsonify(pending_readd)

@app.route("/api/pending-readd/confirm", methods=["POST"])
def api_pending_readd_confirm():
    """User confirms re-adding a previously deleted series."""
    global pending_readd
    data    = request.json or {}
    tvdb_id = data.get("tvdbId")
    title   = data.get("title", "")
    # Remove from ignored list
    ignored = CONFIG.get("IGNORED_SERIES", [])
    if tvdb_id in ignored:
        ignored.remove(tvdb_id)
        CONFIG["IGNORED_SERIES"] = ignored
        save_config_to_disk(CONFIG)
    # Remove from pending
    pending_readd = [p for p in pending_readd if p["tvdbId"] != tvdb_id]
    # Add to Sonarr
    try:
        qp_id = get_quality_profile_id()
        add_series_to_sonarr(tvdb_id, title, qp_id)
        logger.info(f"✅ Re-added '{title}' to Sonarr after user confirmation")
    except Exception as e:
        logger.error(f"Re-add failed for '{title}': {e}")
        return jsonify({"error": str(e)}), 500
    return jsonify({"ok": True})

@app.route("/api/pending-readd/dismiss", methods=["POST"])
def api_pending_readd_dismiss():
    """User dismisses re-add prompt — keeps series ignored."""
    global pending_readd
    data    = request.json or {}
    tvdb_id = data.get("tvdbId")
    pending_readd = [p for p in pending_readd if p["tvdbId"] != tvdb_id]
    logger.info(f"⏭ Dismissed re-add prompt for TVDB:{tvdb_id}")
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
