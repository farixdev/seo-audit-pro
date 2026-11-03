# -*- coding: utf-8 -*-
"""Scrape Planner & Matrix API routes (Countries -> Areas -> Niche Targets)."""

from flask import Blueprint, jsonify, request

from backend import db, security
from backend.errors import ApiError
from backend.routes.auth_routes import require
from backend.util import now_iso

bp = Blueprint("scrape_planner", __name__)


@bp.get("/api/scrape-planner")
@require()
def get_scrape_planner():
    country_id = request.args.get("country_id", "").strip()
    area_id = request.args.get("area_id", "").strip()
    status = request.args.get("status", "").strip()
    search = request.args.get("search", "").strip()

    countries = db.query("SELECT * FROM scrape_countries ORDER BY name ASC")
    areas = db.query(
        "SELECT a.*, c.name AS country_name FROM scrape_areas a "
        "JOIN scrape_countries c ON a.country_id = c.id ORDER BY a.name ASC"
    )

    query = (
        "SELECT t.*, a.name AS area_name, c.name AS country_name, c.id AS country_id "
        "FROM scrape_targets t "
        "JOIN scrape_areas a ON t.area_id = a.id "
        "JOIN scrape_countries c ON a.country_id = c.id "
        "WHERE 1=1 "
    )
    params = []
    if country_id:
        query += "AND c.id = %s "
        params.append(country_id)
    if area_id:
        query += "AND a.id = %s "
        params.append(area_id)
    if status and status in ("planned", "scraping", "completed"):
        query += "AND t.status = %s "
        params.append(status)
    if search:
        query += "AND (LOWER(t.niche) LIKE %s OR LOWER(a.name) LIKE %s OR LOWER(c.name) LIKE %s) "
        s = f"%{search.lower()}%"
        params.extend([s, s, s])

    query += "ORDER BY t.created_at DESC"
    targets = db.query(query, params)

    # Compute overall stats
    total = len(targets)
    planned = sum(1 for t in targets if t["status"] == "planned")
    scraping = sum(1 for t in targets if t["status"] == "scraping")
    completed = sum(1 for t in targets if t["status"] == "completed")

    return jsonify({
        "countries": [dict(c) for c in countries],
        "areas": [dict(a) for a in areas],
        "targets": [dict(t) for t in targets],
        "stats": {
            "total": total,
            "planned": planned,
            "scraping": scraping,
            "completed": completed,
        }
    })


# ── Countries CRUD ────────────────────────────────────────────────────────────

@bp.post("/api/scrape-planner/countries")
@require()
def create_country():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    code = (data.get("code") or "").strip().upper()
    if not name:
        raise ApiError(422, "Country name is required.")
    cid = security.new_id("ctry_")
    now = now_iso()
    with db.transaction() as tx:
        tx.execute(
            "INSERT INTO scrape_countries (id, name, code, created_at) VALUES (%s, %s, %s, %s)",
            (cid, name, code, now)
        )
    row = db.query_one("SELECT * FROM scrape_countries WHERE id = %s", (cid,))
    return jsonify({"country": dict(row)}), 201


@bp.patch("/api/scrape-planner/countries/<cid>")
@require()
def update_country(cid):
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    code = (data.get("code") or "").strip().upper()
    row = db.query_one("SELECT * FROM scrape_countries WHERE id = %s", (cid,))
    if not row:
        raise ApiError(404, "Country not found.")
    updates = {}
    if name:
        updates["name"] = name
    if "code" in data:
        updates["code"] = code
    if not updates:
        return jsonify({"country": dict(row)})
    sets = ", ".join(f"{k} = %s" for k in updates)
    with db.transaction() as tx:
        tx.execute(f"UPDATE scrape_countries SET {sets} WHERE id = %s", list(updates.values()) + [cid])
    updated = db.query_one("SELECT * FROM scrape_countries WHERE id = %s", (cid,))
    return jsonify({"country": dict(updated)})


@bp.delete("/api/scrape-planner/countries/<cid>")
@require()
def delete_country(cid):
    with db.transaction() as tx:
        # Cascade delete targets and areas
        tx.execute("DELETE FROM scrape_targets WHERE area_id IN (SELECT id FROM scrape_areas WHERE country_id = %s)", (cid,))
        tx.execute("DELETE FROM scrape_areas WHERE country_id = %s", (cid,))
        tx.execute("DELETE FROM scrape_countries WHERE id = %s", (cid,))
    return jsonify({"deleted": True, "id": cid})


# ── Areas CRUD ────────────────────────────────────────────────────────────────

@bp.post("/api/scrape-planner/areas")
@require()
def create_area():
    data = request.get_json(silent=True) or {}
    country_id = (data.get("country_id") or "").strip()
    name = (data.get("name") or "").strip()
    if not country_id:
        raise ApiError(422, "Select a country for this area.")
    if not name:
        raise ApiError(422, "Area or city name is required.")
    aid = security.new_id("area_")
    now = now_iso()
    with db.transaction() as tx:
        tx.execute(
            "INSERT INTO scrape_areas (id, country_id, name, created_at) VALUES (%s, %s, %s, %s)",
            (aid, country_id, name, now)
        )
    row = db.query_one(
        "SELECT a.*, c.name AS country_name FROM scrape_areas a "
        "JOIN scrape_countries c ON a.country_id = c.id WHERE a.id = %s",
        (aid,)
    )
    return jsonify({"area": dict(row)}), 201


@bp.patch("/api/scrape-planner/areas/<aid>")
@require()
def update_area(aid):
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    country_id = (data.get("country_id") or "").strip()
    row = db.query_one("SELECT * FROM scrape_areas WHERE id = %s", (aid,))
    if not row:
        raise ApiError(404, "Area not found.")
    updates = {}
    if name:
        updates["name"] = name
    if country_id:
        updates["country_id"] = country_id
    if not updates:
        return jsonify({"area": dict(row)})
    sets = ", ".join(f"{k} = %s" for k in updates)
    with db.transaction() as tx:
        tx.execute(f"UPDATE scrape_areas SET {sets} WHERE id = %s", list(updates.values()) + [aid])
    updated = db.query_one(
        "SELECT a.*, c.name AS country_name FROM scrape_areas a "
        "JOIN scrape_countries c ON a.country_id = c.id WHERE a.id = %s",
        (aid,)
    )
    return jsonify({"area": dict(updated)})


@bp.delete("/api/scrape-planner/areas/<aid>")
@require()
def delete_area(aid):
    with db.transaction() as tx:
        tx.execute("DELETE FROM scrape_targets WHERE area_id = %s", (aid,))
        tx.execute("DELETE FROM scrape_areas WHERE id = %s", (aid,))
    return jsonify({"deleted": True, "id": aid})


# ── Niche Targets CRUD & Status ───────────────────────────────────────────────

@bp.post("/api/scrape-planner/targets")
@require()
def create_target():
    data = request.get_json(silent=True) or {}
    area_id = (data.get("area_id") or "").strip()
    niche = (data.get("niche") or "").strip()
    status = (data.get("status") or "planned").strip().lower()
    notes = (data.get("notes") or "").strip()

    if not area_id:
        raise ApiError(422, "Select an area or city.")
    if not niche:
        raise ApiError(422, "Enter a niche or business category to scrape.")
    if status not in ("planned", "scraping", "completed"):
        status = "planned"

    tid = security.new_id("tgt_")
    now = now_iso()
    with db.transaction() as tx:
        tx.execute(
            "INSERT INTO scrape_targets (id, area_id, niche, status, leads_harvested, notes, created_at, updated_at) "
            "VALUES (%s, %s, %s, %s, 0, %s, %s, %s)",
            (tid, area_id, niche, status, notes, now, now)
        )
    row = db.query_one(
        "SELECT t.*, a.name AS area_name, c.name AS country_name, c.id AS country_id "
        "FROM scrape_targets t "
        "JOIN scrape_areas a ON t.area_id = a.id "
        "JOIN scrape_countries c ON a.country_id = c.id "
        "WHERE t.id = %s",
        (tid,)
    )
    return jsonify({"target": dict(row)}), 201


@bp.patch("/api/scrape-planner/targets/<tid>")
@require()
def update_target(tid):
    data = request.get_json(silent=True) or {}
    row = db.query_one("SELECT * FROM scrape_targets WHERE id = %s", (tid,))
    if not row:
        raise ApiError(404, "Target not found.")

    updates = {}
    if "niche" in data and str(data["niche"]).strip():
        updates["niche"] = str(data["niche"]).strip()
    if "area_id" in data and str(data["area_id"]).strip():
        updates["area_id"] = str(data["area_id"]).strip()
    if "status" in data:
        st = str(data["status"]).strip().lower()
        if st in ("planned", "scraping", "completed"):
            updates["status"] = st
    if "notes" in data:
        updates["notes"] = str(data["notes"]).strip()
    if "leads_harvested" in data:
        try:
            updates["leads_harvested"] = int(data["leads_harvested"])
        except (ValueError, TypeError):
            pass
    if "last_scraped_at" in data:
        updates["last_scraped_at"] = str(data["last_scraped_at"]).strip()

    updates["updated_at"] = now_iso()
    sets = ", ".join(f"{k} = %s" for k in updates)
    with db.transaction() as tx:
        tx.execute(f"UPDATE scrape_targets SET {sets} WHERE id = %s", list(updates.values()) + [tid])

    updated = db.query_one(
        "SELECT t.*, a.name AS area_name, c.name AS country_name, c.id AS country_id "
        "FROM scrape_targets t "
        "JOIN scrape_areas a ON t.area_id = a.id "
        "JOIN scrape_countries c ON a.country_id = c.id "
        "WHERE t.id = %s",
        (tid,)
    )
    return jsonify({"target": dict(updated)})


@bp.delete("/api/scrape-planner/targets/<tid>")
@require()
def delete_target(tid):
    with db.transaction() as tx:
        tx.execute("DELETE FROM scrape_targets WHERE id = %s", (tid,))
    return jsonify({"deleted": True, "id": tid})
