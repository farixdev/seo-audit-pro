# -*- coding: utf-8 -*-
"""My Leads: a lead bank for people who use Scrape & Audit on their own (no
team server, no account). One SQLite file on this computer:
%LOCALAPPDATA%\\SEO Audit Pro\\solo\\leads.db

Leads come from Google Maps scraping, CSV/Excel files or are typed in; audits
update them (status, score, report), so leads, audits and reports stay linked.
A lead is unique by its website domain (or, without a website, by name + phone).
"""

import csv
import os
import re
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
from urllib.parse import urlparse

_LOCK = threading.Lock()
STATUSES = ("new", "queued", "running", "audited", "failed")

SCHEMA = """
CREATE TABLE IF NOT EXISTS leads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company TEXT NOT NULL DEFAULT '',
    website TEXT NOT NULL DEFAULT '',
    domain TEXT NOT NULL DEFAULT '',
    phone TEXT NOT NULL DEFAULT '',
    email TEXT NOT NULL DEFAULT '',
    address TEXT NOT NULL DEFAULT '',
    area TEXT NOT NULL DEFAULT '',
    niche TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL DEFAULT '',
    rating REAL,
    reviews INTEGER,
    maps_url TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT 'manual',
    status TEXT NOT NULL DEFAULT 'new',
    score INTEGER,
    grade TEXT NOT NULL DEFAULT '',
    report TEXT NOT NULL DEFAULT '',
    error TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    audited_at TEXT NOT NULL DEFAULT ''
);
CREATE UNIQUE INDEX IF NOT EXISTS leads_domain ON leads(domain) WHERE domain <> '';
CREATE INDEX IF NOT EXISTS leads_status ON leads(status);
CREATE INDEX IF NOT EXISTS leads_niche_area ON leads(niche, area);
"""


def db_path():
    from seo_audit_pro.local_server import app_dir
    folder = os.path.join(app_dir(), "solo")
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, "leads.db")


@contextmanager
def _connect():
    """A connection that commits on success, rolls back on error and is always
    closed (an open handle would keep the file locked on Windows)."""
    con = sqlite3.connect(db_path(), timeout=15)
    try:
        con.row_factory = sqlite3.Row
        con.executescript(SCHEMA)
        yield con
        con.commit()
    except BaseException:
        con.rollback()
        raise
    finally:
        con.close()


def domain_of(url):
    raw = (url or "").strip().lower()
    if not raw:
        return ""
    if "://" not in raw:
        raw = "http://" + raw
    host = urlparse(raw).netloc.split("@")[-1].split(":")[0]
    host = host[4:] if host.startswith("www.") else host
    # Social pages and Google links are not the business's own website.
    if not host or "." not in host or any(host == s or host.endswith("." + s) for s in (
            "facebook.com", "instagram.com", "google.com", "goo.gl", "linkedin.com", "twitter.com", "x.com",
            "youtube.com", "tiktok.com", "yelp.com", "wa.me", "linktr.ee")):
        return ""
    return host


def _clean(value, limit=300):
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _number(value, cast):
    text = re.sub(r"[^0-9.]", "", str(value or "").replace(",", ""))
    try:
        return cast(float(text)) if text else None
    except ValueError:
        return None


def add_many(rows, source="manual", niche="", area=""):
    """Add leads (dicts with company/website/phone/... keys). Existing websites are
    filled in, not duplicated. Returns {"added", "updated", "skipped"}."""
    now = datetime.now().isoformat(timespec="seconds")
    added = updated = skipped = 0
    with _LOCK, _connect() as con:
        for r in rows:
            company = _clean(r.get("company") or r.get("name"), 200)
            website = _clean(r.get("website"), 500)
            domain = domain_of(website)
            phone = _clean(r.get("phone"), 60)
            if not company and not domain:
                skipped += 1
                continue
            values = {
                "company": company or domain, "website": website if domain else "", "domain": domain,
                "phone": phone, "email": _clean(r.get("email"), 200), "address": _clean(r.get("address"), 300),
                "area": _clean(r.get("area") or r.get("city") or area, 120),
                "niche": _clean(r.get("niche") or niche, 120), "category": _clean(r.get("category"), 120),
                "rating": _number(r.get("rating"), float), "reviews": _number(r.get("review_count") or r.get("reviews"), int),
                "maps_url": _clean(r.get("maps_url") or r.get("maps_link") or r.get("_href"), 800),
            }
            existing = None
            if domain:
                existing = con.execute("SELECT id FROM leads WHERE domain = ?", (domain,)).fetchone()
            elif phone:
                existing = con.execute("SELECT id FROM leads WHERE domain = '' AND company = ? AND phone = ?",
                                       (values["company"], phone)).fetchone()
            if existing:
                # Fill empty fields only; never overwrite what is already known.
                sets = [f"{k} = CASE WHEN {k} IS NULL OR {k} = '' THEN ? ELSE {k} END"
                        for k in values if k not in ("domain",)]
                con.execute(f"UPDATE leads SET {', '.join(sets)} WHERE id = ?",
                            [values[k] for k in values if k != "domain"] + [existing["id"]])
                updated += 1
                continue
            con.execute(f"INSERT INTO leads ({', '.join(values)}, source, created_at) "
                        f"VALUES ({', '.join('?' * len(values))}, ?, ?)", list(values.values()) + [source, now])
            added += 1
    return {"added": added, "updated": updated, "skipped": skipped}


def list_leads(search="", niche="", area="", status="", limit=5000):
    where, args = [], []
    if search:
        where.append("(company LIKE ? OR domain LIKE ? OR phone LIKE ? OR address LIKE ?)")
        args += [f"%{search}%"] * 4
    if niche:
        where.append("niche = ?")
        args.append(niche)
    if area:
        where.append("area = ?")
        args.append(area)
    if status == "not_audited":
        where.append("status IN ('new', 'failed')")
    elif status == "no_website":
        where.append("domain = ''")
    elif status:
        where.append("status = ?")
        args.append(status)
    sql = "SELECT * FROM leads" + (f" WHERE {' AND '.join(where)}" if where else "") + \
          " ORDER BY created_at DESC, id DESC LIMIT ?"
    with _connect() as con:
        return [dict(r) for r in con.execute(sql, args + [limit])]


def facets():
    with _connect() as con:
        niches = [dict(r) for r in con.execute(
            "SELECT niche AS value, COUNT(*) AS n FROM leads WHERE niche <> '' GROUP BY niche ORDER BY n DESC")]
        areas = [dict(r) for r in con.execute(
            "SELECT area AS value, COUNT(*) AS n FROM leads WHERE area <> '' GROUP BY area ORDER BY n DESC")]
    return {"niches": niches, "areas": areas}


def stats():
    """Numbers for the overview page."""
    with _connect() as con:
        one = con.execute("""SELECT COUNT(*) AS total,
                                    SUM(domain <> '') AS with_site,
                                    SUM(status = 'audited') AS audited,
                                    SUM(status = 'failed') AS failed,
                                    SUM(status IN ('queued', 'running')) AS running,
                                    AVG(CASE WHEN status = 'audited' THEN score END) AS avg_score,
                                    SUM(status = 'audited' AND score >= 80) AS good,
                                    SUM(status = 'audited' AND score >= 50 AND score < 80) AS fair,
                                    SUM(status = 'audited' AND score < 50) AS poor
                             FROM leads""").fetchone()
        niches = [dict(r) for r in con.execute(
            "SELECT niche AS value, COUNT(*) AS n FROM leads WHERE niche <> '' GROUP BY niche ORDER BY n DESC LIMIT 6")]
        recent = [dict(r) for r in con.execute(
            "SELECT * FROM leads WHERE status = 'audited' ORDER BY audited_at DESC LIMIT 6")]
        weekly = [dict(r) for r in con.execute(
            "SELECT substr(created_at, 1, 10) AS day, COUNT(*) AS n FROM leads "
            "WHERE created_at >= date('now', '-13 days') GROUP BY day ORDER BY day")]
    out = {k: (one[k] or 0) for k in one.keys()}
    out["avg_score"] = round(one["avg_score"]) if one["avg_score"] is not None else None
    out["niches"], out["recent"], out["daily"] = niches, recent, weekly
    return out


def set_status(domains, status):
    domains = [d for d in domains if d]
    if not domains:
        return
    with _LOCK, _connect() as con:
        con.executemany("UPDATE leads SET status = ? WHERE domain = ?", [(status, d) for d in domains])


def record_audit(website, ok, score=None, grade="", report="", error="", business=""):
    """An audit finished: update its lead (adding one for a pasted website)."""
    domain = domain_of(website)
    if not domain:
        return
    now = datetime.now().isoformat(timespec="seconds")
    with _LOCK, _connect() as con:
        row = con.execute("SELECT id FROM leads WHERE domain = ?", (domain,)).fetchone()
        if not row:
            con.execute("INSERT INTO leads (company, website, domain, source, created_at) VALUES (?, ?, ?, 'audit', ?)",
                        (business or domain, website if "://" in website else "https://" + website, domain, now))
        if ok:
            con.execute("UPDATE leads SET status = 'audited', score = ?, grade = ?, report = ?, error = '', "
                        "audited_at = ? WHERE domain = ?", (score, grade or "", report or "", now, domain))
        else:
            con.execute("UPDATE leads SET status = 'failed', error = ? WHERE domain = ?", ((error or "")[:500], domain))


def update(lead_id, **fields):
    allowed = {"company", "website", "phone", "email", "address", "area", "niche", "notes"}
    fields = {k: _clean(v, 500) for k, v in fields.items() if k in allowed}
    if "website" in fields:
        fields["domain"] = domain_of(fields["website"])
    if not fields:
        return
    with _LOCK, _connect() as con:
        con.execute(f"UPDATE leads SET {', '.join(f'{k} = ?' for k in fields)} WHERE id = ?",
                    list(fields.values()) + [lead_id])


def delete(ids):
    with _LOCK, _connect() as con:
        con.executemany("DELETE FROM leads WHERE id = ?", [(i,) for i in ids])


def export_csv(path, rows):
    cols = ["company", "website", "phone", "email", "address", "area", "niche", "category", "rating", "reviews",
            "status", "score", "grade", "maps_url", "report", "audited_at"]
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({c: ("" if r.get(c) is None else r.get(c)) for c in cols})
    return path


def read_file(path):
    """Read a CSV/TSV/XLSX lead file: (rows mapped to lead fields, mapping, niche guess, area guess)."""
    from backend.lead_import import detect_columns, guess_niche_area, iter_rows
    headers, rows = iter_rows(path)
    mapping = detect_columns(headers)
    raw = []
    for i, row in enumerate(rows):
        if i >= 200000:
            raise ValueError("The file has more than 200,000 rows. Split it into smaller files.")
        raw.append(row)
    niche, area = guess_niche_area(path, raw[:500], mapping)
    out = []
    for row in raw:
        lead = {field: row.get(col, "") for field, col in mapping.items() if col}
        out.append(lead)
    return out, mapping, niche, area
