"""
Collects an update-state snapshot from one Gaia host (management or gateway).

Snapshot layout:
{
  "host": "A-GW-01", "ip": "10.1.1.2", "role": "Gateway",
  "collected_at": "2026-09-27T19:00:00Z", "ok": true, "error": null,
  "login_shell": "clish",
  "sections": {
     "<section>": {"ok": bool, "rc": int, "data": <parsed>, "raw": "<text>", "error": str|None}
  }
}
"""
import time
import logging
from datetime import datetime, timezone

from mau.gaia_shell import GaiaShell, GaiaShellError
from mau import parsers

logger = logging.getLogger("mau.collector")

# Section -> (command, description). Commands run in expert mode.
COMMANDS = {
    "system": (
        'echo "HOST=$(hostname)"; echo "REL=$(cat /etc/cp-release 2>/dev/null)"; '
        'echo "UTC=$(date -u +%Y-%m-%dT%H:%M:%SZ)"',
        "Hostname / release",
    ),
    "autoupdater": (
        "autoupdatercli show auto_updater",
        "AutoUpdater take (sk165653)",
    ),
    "au_components": (
        "cat ${AU_DIR:-/opt/AutoUpdater}/productsConfig/products_config.xml",
        "AutoUpdater components (products_config.xml)",
    ),
    "bundles": (
        "cpinfo -y all",
        "Installed bundles & hotfix takes (cpinfo -y all)",
    ),
    "da": (
        "clish -c 'show installer status build'; "
        "cpvinfo $DADIR/bin/DAService 2>/dev/null | egrep -i 'build|version'",
        "CPUSE Deployment Agent build",
    ),
    "cpuse_installed": (
        "clish -c 'show installer packages installed'",
        "CPUSE installed packages",
    ),
    "cpuse_available": (
        "clish -c 'show installer packages available-for-download'",
        "CPUSE catalogue (available for download)",
    ),
    "contracts": (
        "find $CPDIR/conf $FWDIR/conf $CPDIR/database -maxdepth 2 -type f -iname '*contract*' "
        "-exec md5sum {} \\; 2>/dev/null | sort -k2",
        "Contract files (md5)",
    ),
    "licences": (
        "cplic print",
        "Licences (cplic print)",
    ),
    "consent": (
        "for p in AllowReceivingDataFromCheckPoint AllowReceivingDataFromCheckPointNonSecurity "
        "AllowSendingDataToCheckPoint AllowSendingSensitiveDataToCheckPoint; do "
        "echo \"GAIA $p=$(dbget $p 2>/dev/null)\"; done; "
        "grep -A 10 DownloadAccess $CPDIR/tmp/umis_objects.C 2>/dev/null | grep ':allow' | "
        "sed 's/^[[:space:]]*/EFFECTIVE /'",
        "Consent flags (sk175504)",
    ),
    "au_logs": (
        "find /var/log ${AU_DIR:-/opt/AutoUpdater} $CPDIR/log $FWDIR/log -maxdepth 3 -type f "
        "\\( -iname '*autoupdat*' -o -path '/opt/AutoUpdater/*log*' \\) "
        "-printf '%TY-%Tm-%TdT%TH:%TM:%TS %s %p\\n' 2>/dev/null | sort -r | head -25",
        "AutoUpdater log files",
    ),
}

SECTION_TITLES = {k: v[1] for k, v in COMMANDS.items()}
SECTION_TITLES["au_log_tail"] = "Newest AutoUpdater log (tail)"
SECTION_TITLES["da_log_tail"] = "CPUSE DeploymentAgent.log (download/install lines)"


def _utc_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse(section, raw, settings):
    ign = settings.get("ignore", {})
    lines_ign = ign.get("line_patterns", [])
    if section == "system":
        return parsers.parse_system(raw)
    if section == "autoupdater":
        return parsers.parse_au_take(raw)
    if section == "au_components":
        return parsers.parse_components(raw, ign.get("component_attributes"))
    if section == "bundles":
        return parsers.parse_cpinfo(raw)
    if section == "da":
        return {"build": parsers.parse_da_build(raw)}
    if section in ("cpuse_installed", "cpuse_available", "licences"):
        return parsers.parse_lines(raw, lines_ign)
    if section == "contracts":
        return parsers.parse_hashes(raw)
    if section == "consent":
        return parsers.parse_consent(raw)
    if section == "au_logs":
        return parsers.parse_file_list(raw)
    return None


def collect_host(host_cfg: dict, creds: dict, settings: dict) -> dict:
    col = settings.get("collection", {})
    timeout = int(col.get("command_timeout_seconds", 240))
    skip = set(col.get("skip_sections", []))
    ssh = dict(creds.get("ssh", {}))
    ssh.update(host_cfg.get("ssh", {}))  # optional per-host override

    snap = {
        "host": host_cfg["name"],
        "ip": host_cfg["ip"],
        "role": host_cfg.get("role", ""),
        "collected_at": _utc_now(),
        "ok": False,
        "error": None,
        "login_shell": None,
        "duration_s": None,
        "sections": {},
    }
    t0 = time.time()
    shell = GaiaShell(host_cfg["ip"], ssh.get("user", "admin"), ssh.get("password", ""),
                      ssh.get("expert_password"), port=host_cfg.get("ssh_port", 22),
                      login_timeout=int(col.get("login_timeout_seconds", 90)))
    try:
        shell.open()
        snap["login_shell"] = shell.login_shell
        for section, (cmd, _title) in COMMANDS.items():
            if section in skip:
                continue
            try:
                r = shell.run(cmd, timeout=timeout)
                raw = r["output"]
                sec = {"ok": not r["timed_out"], "rc": r["rc"], "raw": raw,
                       "error": "timed out" if r["timed_out"] else None, "data": None}
                if not r["timed_out"]:
                    sec["data"] = _parse(section, raw, settings)
                    # A missing tool or file shouldn't be diffed as "everything removed"
                    if r["rc"] not in (0, None) and not sec["data"]:
                        sec["ok"] = False
                        sec["error"] = f"exit code {r['rc']}"
                snap["sections"][section] = sec
            except GaiaShellError as e:
                snap["sections"][section] = {"ok": False, "rc": None, "raw": "", "data": None, "error": str(e)}
                raise

        # Tail of the newest AutoUpdater log, for context in the dashboard
        logs = (snap["sections"].get("au_logs") or {}).get("data") or []
        if logs:
            n = int(col.get("log_tail_lines", 60))
            path = logs[0]["path"].replace("'", "")
            r = shell.run(f"tail -n {n} '{path}'", timeout=30)
            snap["sections"]["au_log_tail"] = {"ok": not r["timed_out"], "rc": r["rc"],
                                               "raw": r["output"], "data": {"path": path},
                                               "error": None}
        # CPUSE Deployment Agent activity (sk175504 troubleshooting names this log)
        if "da_log_tail" not in skip:
            n = int(col.get("log_tail_lines", 60))
            r = shell.run("grep -iE 'download|install|self.?update|MSG_|package' "
                          f"/opt/CPInstLog/DeploymentAgent.log 2>/dev/null | tail -n {n}", timeout=30)
            snap["sections"]["da_log_tail"] = {"ok": not r["timed_out"], "rc": r["rc"],
                                               "raw": r["output"],
                                               "data": {"path": "/opt/CPInstLog/DeploymentAgent.log"},
                                               "error": None}
        snap["ok"] = True
    except GaiaShellError as e:
        snap["error"] = str(e)
        logger.error(f"{host_cfg['name']}: {e}")
    except Exception as e:  # never let one host kill the poller
        snap["error"] = f"{type(e).__name__}: {e}"
        logger.exception(f"{host_cfg['name']}: unexpected error")
    finally:
        shell.close()
        snap["duration_s"] = round(time.time() - t0, 1)
    return snap
