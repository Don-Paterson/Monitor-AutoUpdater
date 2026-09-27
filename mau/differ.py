"""
Compare two snapshots of the same host and describe what changed.

Only sections that collected successfully in BOTH snapshots are compared,
so a transient SSH/command failure never shows up as "everything removed".
"""

CATEGORY = {
    "system": "Version",
    "autoupdater": "AutoUpdater",
    "au_components": "AU component",
    "bundles": "Bundle",
    "da": "Deployment Agent",
    "cpuse_installed": "CPUSE installed",
    "cpuse_available": "CPUSE catalogue",
    "contracts": "Contracts",
    "licences": "Licences",
    "consent": "Consent flags",
}


def _sec(snap, name):
    s = (snap or {}).get("sections", {}).get(name)
    return s if s and s.get("ok") and s.get("data") is not None else None


def _ev(section, item, change, old=None, new=None, detail=None):
    return {"category": CATEGORY.get(section, section), "section": section,
            "item": item, "change": change, "old": old, "new": new, "detail": detail}


def _fmt_attrs(d):
    return ", ".join(f"{k}={v}" for k, v in sorted(d.items())) if d else ""


def diff_snapshots(old: dict, new: dict) -> list:
    events = []

    # Scalars
    for section, key, label in (
        ("system", "release", "Release"),
        ("autoupdater", "take", "AutoUpdater take"),
        ("autoupdater", "build", "AutoUpdater build"),
        ("da", "build", "Deployment Agent build"),
    ):
        a, b = _sec(old, section), _sec(new, section)
        if a and b:
            va, vb = a["data"].get(key), b["data"].get(key)
            if va != vb and (va or vb):
                events.append(_ev(section, label, "changed", va, vb))

    # Consent flags (Gaia DB + effective)
    a, b = _sec(old, "consent"), _sec(new, "consent")
    if a and b:
        for layer, label in (("effective", "effective"), ("gaia_db", "Gaia DB")):
            fa, fb = a["data"].get(layer, {}), b["data"].get(layer, {})
            for k in sorted(set(fa) | set(fb)):
                if fa.get(k) != fb.get(k):
                    events.append(_ev("consent", f"{k} ({label})", "changed", fa.get(k), fb.get(k)))

    # AutoUpdater components: dict of attribute dicts
    a, b = _sec(old, "au_components"), _sec(new, "au_components")
    if a and b:
        da, db = a["data"], b["data"]
        for name in sorted(set(db) - set(da)):
            events.append(_ev("au_components", name, "added", None, _fmt_attrs(db[name])))
        for name in sorted(set(da) - set(db)):
            events.append(_ev("au_components", name, "removed", _fmt_attrs(da[name]), None))
        for name in sorted(set(da) & set(db)):
            if da[name] != db[name]:
                keys = sorted(set(da[name]) | set(db[name]))
                ch = {k: [da[name].get(k), db[name].get(k)] for k in keys
                      if da[name].get(k) != db[name].get(k)}
                events.append(_ev("au_components", name, "changed",
                                  _fmt_attrs({k: v[0] for k, v in ch.items() if v[0] is not None}),
                                  _fmt_attrs({k: v[1] for k, v in ch.items() if v[1] is not None}),
                                  ch))

    # Bundles: {name: {take, products}}
    a, b = _sec(old, "bundles"), _sec(new, "bundles")
    if a and b:
        da, db = a["data"], b["data"]
        for name in sorted(set(db) - set(da)):
            events.append(_ev("bundles", name, "added", None, f"Take {db[name]['take']}"))
        for name in sorted(set(da) - set(db)):
            events.append(_ev("bundles", name, "removed", f"Take {da[name]['take']}", None))
        for name in sorted(set(da) & set(db)):
            if da[name]["take"] != db[name]["take"]:
                events.append(_ev("bundles", name, "changed",
                                  f"Take {da[name]['take']}", f"Take {db[name]['take']}"))

    # Line sets
    for section in ("cpuse_installed", "cpuse_available", "licences"):
        a, b = _sec(old, section), _sec(new, section)
        if a and b:
            sa, sb = set(a["data"]), set(b["data"])
            for line in sorted(sb - sa):
                events.append(_ev(section, line, "added"))
            for line in sorted(sa - sb):
                events.append(_ev(section, line, "removed"))

    # Contract file hashes
    a, b = _sec(old, "contracts"), _sec(new, "contracts")
    if a and b:
        da, db = a["data"], b["data"]
        for p in sorted(set(db) - set(da)):
            events.append(_ev("contracts", p, "added", None, db[p][:12]))
        for p in sorted(set(da) - set(db)):
            events.append(_ev("contracts", p, "removed", da[p][:12], None))
        for p in sorted(set(da) & set(db)):
            if da[p] != db[p]:
                events.append(_ev("contracts", p, "changed", da[p][:12], db[p][:12]))

    return events
