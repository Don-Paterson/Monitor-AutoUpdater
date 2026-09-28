"""
Parsers for the command output collected from Gaia hosts.
Kept separate from the SSH code so they can be unit-tested with sample output.
"""
import re
import xml.etree.ElementTree as ET


def _compile_all(patterns):
    return [re.compile(p) for p in (patterns or [])]


def parse_system(raw: str) -> dict:
    lines = [l.strip() for l in raw.split("\n") if l.strip()]
    out = {"hostname": None, "release": None, "utc_time": None}
    for l in lines:
        if l.startswith("HOST="):
            out["hostname"] = l[5:]
        elif l.startswith("REL="):
            out["release"] = l[4:]
        elif l.startswith("UTC="):
            out["utc_time"] = l[4:]
    return out


def parse_au_take(raw: str) -> dict:
    """AutoUpdater take from 'autoupdatercli show auto_updater'.

    R81.20/R82 print one block per package (package-version, package-name,
    package-installed: true|false, ...); the take is the package-version of the
    block marked installed (CCTE lab, 28 Sep 2026: A-SMS T84, gateways T90).
    'build' holds the installed package file name so a re-spin of the same take
    still shows as a change. Older formats ('Take: 90 / Build: 441') still work."""
    blocks, cur = [], {}
    for line in raw.splitlines():
        m = re.match(r"\s*(package-[\w-]+|installation-date)\s*:\s*(.*?)\s*$", line)
        if m:
            if m.group(1) in cur:            # a repeated key starts the next package block
                blocks.append(cur)
                cur = {}
            cur[m.group(1)] = m.group(2)
        elif not line.strip() and cur:
            blocks.append(cur)
            cur = {}
    if cur:
        blocks.append(cur)
    installed = [b for b in blocks if b.get("package-installed", "").lower() == "true"]
    if installed:
        b = installed[-1]
        return {"take": b.get("package-version"), "build": b.get("package-name"),
                "installed_at": b.get("installation-date")}
    take = re.search(r"(?i)\btake\b\D{0,5}(\d+)", raw)
    build = re.search(r"(?i)\bbuild\b\D{0,5}(\d+)", raw)
    return {
        "take": take.group(1) if take else None,
        "build": build.group(1) if build else None,
    }


def parse_components(raw: str, ignore_attr_re: str = None) -> dict:
    """products_config.xml -> {component_name: {attr: value, ...}}"""
    ignore = re.compile(ignore_attr_re) if ignore_attr_re else None
    comps = {}
    start = raw.find("<")
    xml_text = raw[start:] if start >= 0 else ""

    def keep(k):
        return not (ignore and ignore.search(k))

    try:
        root = ET.fromstring(xml_text)
        for el in root.iter():
            if el.tag.lower() != "component":
                continue
            name = el.attrib.get("name") or el.attrib.get("Name")
            if not name:
                continue
            data = {k: v for k, v in el.attrib.items() if k.lower() != "name" and keep(k)}
            for child in el:
                text = (child.text or "").strip()
                if text and keep(child.tag):
                    data[child.tag] = text
                for k, v in child.attrib.items():
                    key = f"{child.tag}.{k}"
                    if keep(key):
                        data[key] = v
            comps[name] = data
        return comps
    except ET.ParseError:
        pass

    # Fallback: grep-style parse of <Component name="x" a="b" ...> tags
    for m in re.finditer(r"<Component\s+([^>]*?)/?>", xml_text, re.I):
        attrs = dict(re.findall(r'(\w[\w.-]*)="([^"]*)"', m.group(1)))
        name = attrs.pop("name", None) or attrs.pop("Name", None)
        if name:
            comps[name] = {k: v for k, v in attrs.items() if keep(k)}
    return comps


def parse_cpinfo(raw: str) -> dict:
    """cpinfo -y all -> {bundle_name: {"take": "N", "products": [..]}}"""
    bundles = {}
    product = None
    for line in raw.split("\n"):
        s = line.strip()
        m = re.match(r"^\[(.+?)\]$", s)
        if m:
            product = m.group(1)
            continue
        m = re.match(r"^(\S+)\s+Take:\s*(\S+)", s)
        if m:
            name, take = m.group(1), m.group(2)
            b = bundles.setdefault(name, {"take": take, "products": []})
            b["take"] = take
            if product and product not in b["products"]:
                b["products"].append(product)
    return bundles


def parse_lines(raw: str, ignore_patterns=None) -> list:
    """Normalise table-ish output into a de-duplicated list of meaningful lines."""
    ignores = _compile_all(ignore_patterns)
    seen, out = set(), []
    for line in raw.split("\n"):
        s = re.sub(r"\s+", " ", line).strip()
        if not s or re.fullmatch(r"[-=+|_ ]+", s):
            continue
        s = re.sub(r"^\[?\d+\]?[.)]?\s+", "", s)  # drop list indices like "1 " or "[3]"
        if any(p.search(s) for p in ignores):
            continue
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


def parse_da_build(raw: str):
    m = re.search(r"(?i)build\D{0,30}?(\d{3,})", raw)
    return m.group(1) if m else None


def parse_hashes(raw: str) -> dict:
    """md5sum output -> {path: hash}"""
    out = {}
    for line in raw.split("\n"):
        m = re.match(r"^([0-9a-f]{32})\s+\*?(\S.*)$", line.strip())
        if m:
            out[m.group(2)] = m.group(1)
    return out


def parse_file_list(raw: str) -> list:
    """'<mtime> <size> <path>' lines -> [{mtime, size, path}] newest first"""
    files = []
    for line in raw.split("\n"):
        parts = line.strip().split(" ", 2)
        if len(parts) == 3 and parts[1].isdigit():
            files.append({"mtime": parts[0].split(".")[0], "size": int(parts[1]), "path": parts[2]})
    files.sort(key=lambda f: f["mtime"], reverse=True)
    return files


CONSENT_NAMES = {
    "AllowReceivingDataFromCheckPoint": "Download Security",
    "AllowReceivingDataFromCheckPointNonSecurity": "Download Non-Security",
    "AllowSendingDataToCheckPoint": "Upload Information",
    "AllowSendingSensitiveDataToCheckPoint": "Upload Crash Data",
    "allow_download_content": "Download Security",
    "allow_download_non_security_content": "Download Non-Security",
    "allow_upload_content": "Upload Information",
    "allow_upload_sensitive_content": "Upload Crash Data",
}


def parse_consent(raw: str) -> dict:
    """Consent flags from the Gaia DB (dbget) and the effective values in
    $CPDIR/tmp/umis_objects.C (DownloadAccess), per sk175504 section 4."""
    out = {"gaia_db": {}, "effective": {}}
    for line in raw.split("\n"):
        s = line.strip()
        m = re.match(r"^GAIA (\w+)=(.*)$", s)
        if m:
            val = m.group(2).strip()
            out["gaia_db"][CONSENT_NAMES.get(m.group(1), m.group(1))] = (
                {"1": "true", "0": "false"}.get(val, val or "unset"))
            continue
        m = re.match(r"^EFFECTIVE :(\w+) \((\w+)\)", s)
        if m:
            out["effective"][CONSENT_NAMES.get(m.group(1), m.group(1))] = m.group(2)
    return out
