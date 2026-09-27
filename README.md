# Monitor-AutoUpdater

Watches what Check Point's automatic update channels change on your lab Gaia hosts. It runs from A-GUI, polls A-SMS and the gateways over SSH (read-only), keeps a baseline per host, and shows every change on a live local dashboard with a change log.

Why: Skillable labs have internet access by default, so AutoUpdater (sk165653), CPUSE/Deployment Agent self-updates and contract downloads can change the environment between one class and the next. The biggest visible example is Web SmartConsole, which can drift away from the courseware screenshots. The announcements in sk184735 tell you what's coming, and this tool tells you what actually landed and when.

## Quick start

On A-GUI, open PowerShell as Administrator:

```powershell
irm https://raw.githubusercontent.com/Don-Paterson/Monitor-AutoUpdater/main/bootstrap.ps1 | iex
```

The bootstrap:

1. Installs Python 3.12 silently if no real Python 3.9+ is present (it ignores the Store alias).
2. Installs `flask` and `paramiko`.
3. Downloads the tool to `C:\Monitor-AutoUpdater`. Any existing `data\`, `logs\`, `config.json` and `credentials.json` are kept.
4. Writes `credentials.json` (default `admin` / `Chkp!234`, with the same password for expert).
5. Checks TCP 22 to each host.
6. Creates a **Monitor-AutoUpdater** desktop shortcut and starts the dashboard at **http://localhost:8090**, which opens automatically.

Nothing is configured on the Check Point hosts. The admin user's Clish shell is left alone, because each SSH session enters `expert` itself.

Credential overrides go in environment variables set before the `irm` line:

```powershell
$env:MAU_USER='admin'; $env:MAU_PASSWORD='Chkp!234'; $env:MAU_EXPERT='Chkp!234'
$env:MAU_RESET='1'   # replace existing config.json / credentials.json
```

## What is tracked

| Area | Source on the Gaia host | Change events |
|---|---|---|
| AutoUpdater take/build | `autoupdatercli show auto_updater` | take/build changed |
| AutoUpdater components | `/opt/AutoUpdater/productsConfig/products_config.xml` | component added / removed / attribute changed |
| Installed bundles | `cpinfo -y all` | bundle added / removed / take changed (e.g. `HOTFIX_WEBCONSOLE_AUTOUPDATE`) |
| CPUSE Deployment Agent | `show installer status build` + `cpvinfo $DADIR/bin/DAService` | DA build changed |
| CPUSE installed packages | `show installer packages installed` | package lines added / removed |
| CPUSE catalogue | `show installer packages available-for-download` | new packages offered (filter-able on the page) |
| Consent flags (sk175504) | `dbget Allow*` + `DownloadAccess` in `$CPDIR/tmp/umis_objects.C` | Gaia DB or effective value changed |
| Contracts | md5 of `*contract*` files under `$CPDIR/conf`, `$FWDIR/conf`, `$CPDIR/database` | file added / removed / content changed |
| Licences | `cplic print` | lines added / removed |
| Context only (no events) | AutoUpdater log files + tail; `/opt/CPInstLog/DeploymentAgent.log` download/install lines | shown in the host's **Logs** tab |

The first successful poll of each host becomes its **baseline**. Each later poll is compared with the previous good poll and writes change events. The card for each host also shows the total **drift since baseline**. Use **Re-baseline** in the host panel to start counting afresh, for example at the start of a class.

A section that fails to collect (timeout, missing command) is marked on the card and skipped for that poll. It is never reported as "everything removed".

## Dashboard

- Host cards show release, AutoUpdater take, DA build, component and bundle counts, consent flags, drift and last poll.
- **Collect now** triggers an immediate poll. Otherwise hosts are polled every `interval_minutes` (default 15).
- The page refreshes itself every few seconds. New change rows flash and the tab title shows `(N new)`, so there's no need to reload.
- **Details** opens a panel per host with these tabs: drift since baseline, host change log, AU components, bundles, CPUSE/DA, consent flags, contracts & licences, logs and raw output.
- **Export CSV** downloads the full change log.

## Files and logs

```
C:\Monitor-AutoUpdater\
├── server.py              Flask app + poller  (python server.py --once for a single CLI run)
├── config.json            hosts, poll interval, ignore rules
├── credentials.json       SSH creds (created by bootstrap, gitignored)
├── mau\                   gaia_shell (Paramiko), collector, parsers, differ, store, engine
├── static\dashboard.html
├── data\<host>\baseline.json | last_good.json | latest.json | history\*.json
├── data\changes.jsonl     all change events (machine readable)
└── logs\changes.log       all change events (human readable)   logs\runs.log  one line per host per poll
```

## Config

Hosts default to the standard lab topology (A-SMS 10.1.1.101, A-GW-01 10.1.1.2, A-GW-02 10.1.1.3). Edit `config.json` for other labs. Add `"ssh_port"` or a per-host `"ssh": {...}` credential override if needed.

`ignore.component_attributes` is a regex of `products_config.xml` attribute names to skip, such as last-check timestamps. `ignore.line_patterns` drops noisy lines from the CPUSE and licence output. If a real lab shows a change on every poll, the fix is usually to add a pattern here. `collection.skip_sections` turns sections off; for example, add `"bundles"` if `cpinfo -y all` is too slow on a busy SMS.

## Testing without a lab

`tests/fake_gaia.py` is a Paramiko SSH server that behaves like a Gaia host: Clish prompt, `expert` + password, echo, and fake `autoupdatercli`/`cpinfo`/`clish`/`dbget`/`cplic` backed by files. `tests/make_state.py <dir> <name> [updated]` writes initial or "updated" state so you can see change events end to end.

## Notes / caveats

- The command set was written against the SK documentation. The parsing is deliberately tolerant, but the `autoupdatercli` and CPUSE table formats on R82.10 should be sanity-checked on the first real run. **Raw output** in the host panel shows exactly what was returned.
- Security content (IPS, AV/AB, AppC/URLF signatures) is **not** tracked. It changes several times a day and would bury the AutoUpdater and CPUSE events.
