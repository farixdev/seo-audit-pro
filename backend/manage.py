# -*- coding: utf-8 -*-
"""
Operator commands.

  python -m backend.manage migrate
  python -m backend.manage create-manager --email you@agency.com --name "Your Name"
  python -m backend.manage import-legacy     (one-time import from the old agency_* tables)
"""

import argparse
import getpass
import sys

from backend import db, migrations, security
from backend.util import EMAIL_RE, normalize_company, normalize_domain, normalize_phone, now_iso


def cmd_migrate(_args):
    migrations.migrate()
    print("Database schema is up to date.")


def cmd_create_manager(args):
    migrations.migrate()
    email = (args.email or input("Manager email: ")).strip().lower()
    if not EMAIL_RE.match(email):
        sys.exit("Invalid email address.")
    if db.query_one("SELECT 1 AS x FROM users WHERE email = %s", (email,)):
        sys.exit("A user with this email already exists.")
    name = (args.name or input("Full name: ")).strip() or "Agency Manager"
    password = getpass.getpass("Password: ")
    problems = security.password_policy_errors(password, email)
    if problems:
        sys.exit("Password rejected: " + " ".join(problems))
    if getpass.getpass("Repeat password: ") != password:
        sys.exit("Passwords do not match.")
    now = now_iso()
    db.execute("""INSERT INTO users (id, name, email, password_hash, role, is_active, must_change_password, created_at, updated_at)
                  VALUES (%s, %s, %s, %s, 'MANAGER', 1, 0, %s, %s)""",
               (security.new_id("usr_"), name, email, security.hash_password(password), now, now))
    print(f"Manager {email} created.")


def cmd_import_legacy(_args):
    """Copy teams, users and leads from the previous agency_* tables.

    Every imported account receives a NEW temporary password (printed once)
    and must change it at first sign-in: the old seeded passwords were
    published in documentation and must never remain valid. Legacy audit
    scores are not imported because no stored audit backs them.
    """
    migrations.migrate()
    try:
        legacy_users = db.query("SELECT * FROM agency_users")
    except Exception:  # noqa: BLE001
        sys.exit("No legacy agency_users table found.")
    teams = db.query("SELECT * FROM agency_teams")
    leads = db.query("SELECT * FROM agency_leads")
    now = now_iso()
    team_map, user_map, issued = {}, {}, []
    with db.transaction() as tx:
        for t in teams:
            existing = tx.query_one("SELECT id FROM teams WHERE name_key = %s", (t["name"].strip().lower(),))
            if existing:
                team_map[t["id"]] = existing["id"]
                continue
            tid = security.new_id("team_")
            tx.execute("INSERT INTO teams (id, name, name_key, created_at, updated_at) VALUES (%s, %s, %s, %s, %s)",
                       (tid, t["name"].strip(), t["name"].strip().lower(), now, now))
            team_map[t["id"]] = tid
        for u in legacy_users:
            email = (u["email"] or "").strip().lower()
            if not EMAIL_RE.match(email) or tx.query_one("SELECT 1 AS x FROM users WHERE email = %s", (email,)):
                continue
            role = u["role"] if u["role"] in ("MANAGER", "TEAM_LEAD", "AGENT") else "AGENT"
            team_id = team_map.get(u.get("team_id")) if role != "MANAGER" else None
            if role != "MANAGER" and not team_id:
                continue
            if role == "TEAM_LEAD" and tx.query_one(
                    "SELECT 1 AS x FROM users WHERE team_id = %s AND role = 'TEAM_LEAD' AND is_active = 1", (team_id,)):
                role = "AGENT"
            temp = security.generate_temporary_password()
            uid = security.new_id("usr_")
            tx.execute("""INSERT INTO users (id, name, email, password_hash, role, team_id, is_active, must_change_password,
                          created_at, updated_at) VALUES (%s, %s, %s, %s, %s, %s, %s, 1, %s, %s)""",
                       (uid, u["name"], email, security.hash_password(temp), role, team_id,
                        0 if u.get("status") == "INACTIVE" else 1, now, now))
            user_map[u["id"]] = (uid, team_id)
            issued.append((email, temp))
        imported = 0
        for l in leads:
            domain = normalize_domain(l.get("website") or l.get("domain") or "")
            if not domain or tx.query_one("SELECT 1 AS x FROM leads WHERE normalized_domain = %s", (domain,)):
                continue
            agent = user_map.get(l.get("assigned_agent_id"))
            team_id = team_map.get(l.get("assigned_team_id"))
            agent_id = agent[0] if agent and agent[1] == team_id else None
            email = (l.get("contact_email") or "").strip().lower() or None
            tx.execute("""INSERT INTO leads (id, company, website, normalized_domain, contact_name, email, email_key, phone,
                          phone_key, company_key, niche, niche_key, area, area_key, source, status, assigned_team_id, assigned_agent_id,
                          created_at, updated_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                       (security.new_id("lead_"), l["company"], f"https://{domain}", domain, l.get("contact_name"), email,
                        email, l.get("phone"), normalize_phone(l.get("phone")) or None, normalize_company(l["company"]),
                        l.get("niche"), (l.get("niche") or "").strip().lower() or None, l.get("area"),
                        (l.get("area") or "").strip().lower() or None, l.get("source") or "Legacy import",
                        "assigned" if agent_id else "new", team_id, agent_id, now, now))
            imported += 1
    print(f"Imported {len(team_map)} teams, {len(issued)} users, {imported} leads.")
    print("Temporary passwords (share securely; each user must change it at first sign-in):")
    for email, temp in issued:
        print(f"  {email}: {temp}")


def main():
    parser = argparse.ArgumentParser(prog="python -m backend.manage")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("migrate")
    cm = sub.add_parser("create-manager")
    cm.add_argument("--email")
    cm.add_argument("--name")
    sub.add_parser("import-legacy")
    args = parser.parse_args()
    {"migrate": cmd_migrate, "create-manager": cmd_create_manager, "import-legacy": cmd_import_legacy}[args.cmd](args)


if __name__ == "__main__":
    main()
