"""
Monitor-AutoUpdater - watches Check Point AutoUpdater, CPUSE/DA, bundle,
licence and contract changes on lab Gaia hosts and shows them on a local page.

  python server.py                     menu (lab + machines), then poller + dashboard
  python server.py --last              skip the menu, reuse the last lab/machines
  python server.py --lab ccte --hosts A-SMS,A-GW-01
  python server.py --once [...]        one collection, print changes, exit
"""
import io
import os
import sys
import csv
import json
import time
import logging
import argparse
import threading
import webbrowser

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

from mau.engine import Monitor          # noqa: E402
from mau import menu                    # noqa: E402
from mau.differ import diff_snapshots   # noqa: E402
from mau.collector import SECTION_TITLES  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
                    datefmt="%H:%M:%S")
logging.getLogger("paramiko").setLevel(logging.WARNING)
logger = logging.getLogger("mau")


def load_json(name, required=True):
    path = os.path.join(BASE_DIR, name)
    if not os.path.exists(path):
        if required:
            logger.error(f"{name} not found in {BASE_DIR}. Run bootstrap.ps1 or copy {name}.example")
            sys.exit(1)
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


CONFIG = load_json("config.json")
CREDS = load_json("credentials.json")
monitor = None  # created in main() after the lab / machine menu


def create_app():
    from flask import Flask, jsonify, send_from_directory, Response, abort

    app = Flask(__name__, static_folder="static")

    @app.route("/")
    def index():
        return send_from_directory(os.path.join(BASE_DIR, "static"), "dashboard.html")

    @app.route("/api/state")
    def api_state():
        with monitor.lock:
            status = json.loads(json.dumps(monitor.status))
            gen, collecting = monitor.generation, monitor.collecting
        hosts = []
        for h in monitor.hosts:
            name = h["name"]
            good = monitor.store.get(name, "last_good") or {}
            base = monitor.store.get(name, "baseline") or {}
            secs = good.get("sections", {})

            def d(sec, default=None):
                s = secs.get(sec) or {}
                return s.get("data") if s.get("data") is not None else default

            drift = diff_snapshots(base, good) if base and good else []
            failed = [k for k, v in secs.items() if not v.get("ok")]
            hosts.append({
                "name": name, "ip": h["ip"], "role": h.get("role", ""),
                **status.get(name, {}),
                "hostname": (d("system", {}) or {}).get("hostname"),
                "release": (d("system", {}) or {}).get("release"),
                "au_take": (d("autoupdater", {}) or {}).get("take"),
                "da_build": (d("da", {}) or {}).get("build"),
                "components": len(d("au_components", {}) or {}),
                "bundles": len(d("bundles", {}) or {}),
                "cpuse_installed": len(d("cpuse_installed", []) or []),
                "consent": (d("consent", {}) or {}).get("effective") or (d("consent", {}) or {}).get("gaia_db") or {},
                "last_good": good.get("collected_at"),
                "baseline": base.get("collected_at"),
                "drift": len(drift),
                "failed_sections": failed,
            })
        return jsonify({
            "generation": gen,
            "collecting": collecting,
            "last_cycle": monitor.last_cycle,
            "next_run": monitor.next_run,
            "server_time": time.time(),
            "lab": {"id": monitor.lab["id"], "name": monitor.lab["name"]},
            "interval_minutes": monitor.interval // 60,
            "ui_refresh_seconds": CONFIG.get("dashboard", {}).get("ui_refresh_seconds", 5),
            "hosts": hosts,
            "changes": [e for e in monitor.store.read_events(limit=300)],
        })

    @app.route("/api/host/<name>")
    def api_host(name):
        if name not in monitor.status:
            abort(404)
        good = monitor.store.get(name, "last_good")
        base = monitor.store.get(name, "baseline")
        latest = monitor.store.get(name, "latest")
        return jsonify({
            "latest": latest,
            "last_good": good,
            "baseline_at": (base or {}).get("collected_at"),
            "drift": diff_snapshots(base, good) if base and good else [],
            "events": monitor.store.read_events(host=name, limit=500),
            "titles": SECTION_TITLES,
        })

    @app.route("/api/collect", methods=["POST"])
    def api_collect():
        monitor.trigger()
        return jsonify({"triggered": True})

    @app.route("/api/rebaseline/<name>", methods=["POST"])
    def api_rebaseline(name):
        if name not in monitor.status:
            abort(404)
        snap = monitor.store.rebaseline(name)
        with monitor.lock:
            monitor.generation += 1
        return jsonify({"ok": bool(snap), "baseline": (snap or {}).get("collected_at")})

    @app.route("/api/changes.csv")
    def api_changes_csv():
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["time", "host", "category", "change", "item", "old", "new"])
        for e in reversed(monitor.store.read_events()):
            w.writerow([e.get("ts"), e.get("host"), e.get("category"), e.get("change"),
                        e.get("item"), e.get("old") or "", e.get("new") or ""])
        return Response(buf.getvalue(), mimetype="text/csv",
                        headers={"Content-Disposition": "attachment; filename=autoupdater-changes.csv"})

    return app


def run_once():
    results = monitor.run_once()
    print()
    for snap, events in results:
        state = "OK" if snap.get("ok") else f"ERROR: {snap.get('error')}"
        print(f"== {snap['host']} ({snap['ip']}) {state}  [{snap.get('duration_s')}s]")
        for e in events:
            print(f"   {e['category']:<17} {e['change']:<8} {e['item']}  "
                  f"[{e.get('old') or '-'} -> {e.get('new') or '-'}]")
        if snap.get("ok") and not events:
            print("   no changes")
    print(f"\nLogs: {monitor.store.log_dir}")


def main():
    ap = argparse.ArgumentParser(description="Monitor-AutoUpdater")
    ap.add_argument("--once", action="store_true", help="collect once, print changes and exit")
    ap.add_argument("--no-browser", action="store_true", help="don't open the dashboard in a browser")
    ap.add_argument("--lab", help="lab id from labs.json (skips the menu), e.g. ccte, ctps, ccse-elasticxl")
    ap.add_argument("--hosts", help="comma-separated machine names to monitor (with --lab)")
    ap.add_argument("--last", action="store_true", help="skip the menu and reuse the last selection")
    ap.add_argument("--list-labs", action="store_true", help="list the known labs and exit")
    args = ap.parse_args()

    if args.list_labs:
        from mau.labs import load_labs
        for lab in load_labs(BASE_DIR):
            print(f"{lab['id']:<18} {lab['name']:<20} " + ", ".join(
                f"{h['name']}={'/'.join(h['ips'])}" for h in lab["hosts"]))
        return

    global monitor
    lab, hosts = menu.select(BASE_DIR, lab_arg=args.lab, hosts_arg=args.hosts,
                             use_last=args.last, interactive=sys.stdin.isatty())
    monitor = Monitor(CONFIG, CREDS, BASE_DIR, hosts, lab)

    if args.once:
        run_once()
        return

    dash = CONFIG.get("dashboard", {})
    host, port = dash.get("host", "127.0.0.1"), int(dash.get("port", 8090))
    url = f"http://{'localhost' if host in ('0.0.0.0', '127.0.0.1') else host}:{port}"

    logger.info("=" * 60)
    logger.info(f"Monitor-AutoUpdater starting - lab: {lab['name']}")
    for h in monitor.hosts:
        logger.info(f"  {h['name']:<10} {h['ip']}  ({h.get('role', '')})")
    logger.info(f"  Poll interval: {monitor.interval // 60} min")
    logger.info(f"  Dashboard: {url}")
    logger.info("=" * 60)

    monitor.start()
    if dash.get("open_browser", True) and not args.no_browser:
        threading.Timer(2.0, lambda: webbrowser.open(url)).start()

    app = create_app()
    app.run(host=host, port=port, debug=False, use_reloader=False, threaded=True)


if __name__ == "__main__":
    main()
