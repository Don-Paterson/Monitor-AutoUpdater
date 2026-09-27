"""Create fake host state for tests/fake_gaia.py. Usage: make_state.py <dir> <name> [updated]"""
import os, sys
d, name = sys.argv[1], sys.argv[2]
upd = len(sys.argv) > 3
os.makedirs(f"{d}/CPshrd/conf", exist_ok=True); os.makedirs(f"{d}/CPshrd/tmp", exist_ok=True)
os.makedirs(f"{d}/fw1/log", exist_ok=True); os.makedirs(f"{d}/DA/bin", exist_ok=True)
wsc = "219" if upd else "212"
w = lambda f, t: open(f"{d}/{f}", "w").write(t)
w("au_take.txt", f"AutoUpdater Take: {'91' if upd else '90'}\nBuild: 441\n")
w("CPshrd/tmp/umis_objects.C", "(\n :DownloadAccess (\n  :allow_download_content (true)\n"
  f"  :allow_download_non_security_content ({'false' if upd else 'true'})\n  :allow_upload_content (true)\n"
  "  :allow_upload_sensitive_content (false)\n )\n)\n")
w("consent_db.txt", "AllowReceivingDataFromCheckPoint 1\nAllowReceivingDataFromCheckPointNonSecurity 1\n"
  "AllowSendingDataToCheckPoint 1\nAllowSendingSensitiveDataToCheckPoint 0\n")
os.makedirs(f"{d}/AutoUpdater/productsConfig", exist_ok=True)
w("AutoUpdater/productsConfig/products_config.xml", f"""<?xml version="1.0"?>
<Products>
  <Component name="auto_updater" version="90" lastCheck="{'2026-09-27 19:00' if upd else '2026-09-27 18:00'}"/>
  <Component name="web_console" version="{wsc}" />
  <Component name="CPquid" version="{'45' if upd else '44'}" />
  <Component name="logExporter" version="12" />
  {'<Component name="cpm_doctor" version="7" />' if upd else ''}
</Products>
""")
w("cpinfo.txt", f"""This is Check Point CPinfo Build 914000250 for GAIA
[CPFC]
\tHOTFIX_R82_10_JUMBO_HF_MAIN\tTake:  33
\tHOTFIX_INFRA_CONFIG_AUTOUPDATE\tTake:  {'91' if upd else '90'}
[MGMT]
\tHOTFIX_WEBCONSOLE_AUTOUPDATE\tTake:  {wsc}
\tHOTFIX_QUID_AUTOUPDATE\tTake:  {'45' if upd else '44'}
[IDA]
\tNo hotfixes..
""")
w("da.txt", f"Deployment Agent build: {'2667' if upd else '2654'}\n")
w("cpuse_installed.txt", "Display name Type\n------------------\n1 R82.10 Jumbo Hotfix Accumulator Take 33 Hotfix\n")
w("cpuse_available.txt", "1 R82.10 Jumbo Hotfix Accumulator Take 33 Hotfix\n" +
  ("2 R82.10 Jumbo Hotfix Accumulator Take 41 Hotfix\n" if upd else ""))
w("cplic.txt", "Host Expiration Features\n10.1.1.101 27Oct2026 CPSB-FW CPSB-VPN\n")
w("CPshrd/conf/cp_contracts.xml", "contract v2\n" if upd else "contract v1\n")
w("fw1/log/AutoUpdater.elg", f"[{name}] check done\n" + ("[web_console] installed take 219\n" if upd else ""))
print("state written", d, "updated" if upd else "initial")
