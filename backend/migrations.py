# -*- coding: utf-8 -*-
"""
Versioned, reproducible schema migrations.

Each migration is applied exactly once, inside a transaction, and recorded in
``schema_migrations``. Never edit an applied migration — add a new one.
Run with ``python -m backend.manage migrate`` (also applied on server start).
"""

from datetime import datetime, timezone

from backend import db

_SQLITE_AUDIT_LOG_GUARD = [
    """CREATE TRIGGER IF NOT EXISTS audit_logs_no_update BEFORE UPDATE ON audit_logs
       BEGIN SELECT RAISE(ABORT, 'audit_logs is append-only'); END""",
    """CREATE TRIGGER IF NOT EXISTS audit_logs_no_delete BEFORE DELETE ON audit_logs
       BEGIN SELECT RAISE(ABORT, 'audit_logs is append-only'); END""",
]

_PG_AUDIT_LOG_GUARD = [
    """CREATE OR REPLACE FUNCTION audit_logs_append_only() RETURNS trigger AS $$
       BEGIN RAISE EXCEPTION 'audit_logs is append-only'; END; $$ LANGUAGE plpgsql""",
    "DROP TRIGGER IF EXISTS audit_logs_no_modify ON audit_logs",
    """CREATE TRIGGER audit_logs_no_modify BEFORE UPDATE OR DELETE ON audit_logs
       FOR EACH ROW EXECUTE FUNCTION audit_logs_append_only()""",
]


def _m001_initial():
    return [
        """CREATE TABLE teams (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            name_key TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )""",
        """CREATE TABLE users (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            email TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL CHECK (role IN ('MANAGER', 'TEAM_LEAD', 'AGENT')),
            team_id TEXT REFERENCES teams(id),
            is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
            must_change_password INTEGER NOT NULL DEFAULT 0 CHECK (must_change_password IN (0, 1)),
            avatar_upload_id TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            last_login_at TEXT,
            last_seen_at TEXT,
            password_changed_at TEXT,
            CHECK (role = 'MANAGER' OR team_id IS NOT NULL),
            CHECK (role <> 'MANAGER' OR team_id IS NULL)
        )""",
        "CREATE INDEX ix_users_team ON users(team_id)",
        # One active Team Lead per team.
        "CREATE UNIQUE INDEX ux_users_team_lead ON users(team_id) WHERE role = 'TEAM_LEAD' AND is_active = 1",
        """CREATE TABLE sessions (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL REFERENCES users(id),
            refresh_hash TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            last_used_at TEXT NOT NULL,
            revoked_at TEXT,
            user_agent TEXT,
            ip TEXT
        )""",
        "CREATE INDEX ix_sessions_user ON sessions(user_id)",
        """CREATE TABLE password_resets (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL REFERENCES users(id),
            code_hash TEXT NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            used_at TEXT
        )""",
        "CREATE INDEX ix_password_resets_user ON password_resets(user_id)",
        """CREATE TABLE login_attempts (
            id TEXT PRIMARY KEY,
            email TEXT NOT NULL,
            ip TEXT,
            success INTEGER NOT NULL,
            created_at TEXT NOT NULL
        )""",
        "CREATE INDEX ix_login_attempts_email ON login_attempts(email, created_at)",
        """CREATE TABLE uploads (
            id TEXT PRIMARY KEY,
            owner_id TEXT NOT NULL REFERENCES users(id),
            purpose TEXT NOT NULL CHECK (purpose IN ('chat', 'email', 'cv', 'avatar')),
            original_name TEXT NOT NULL,
            stored_name TEXT NOT NULL UNIQUE,
            mime TEXT NOT NULL,
            size_bytes INTEGER NOT NULL,
            sha256 TEXT NOT NULL,
            created_at TEXT NOT NULL
        )""",
        """CREATE TABLE leads (
            id TEXT PRIMARY KEY,
            company TEXT NOT NULL,
            website TEXT NOT NULL,
            normalized_domain TEXT NOT NULL UNIQUE,
            contact_name TEXT,
            email TEXT,
            email_key TEXT,
            phone TEXT,
            phone_key TEXT,
            company_key TEXT NOT NULL,
            industry TEXT,
            country TEXT,
            location TEXT,
            source TEXT,
            notes TEXT,
            status TEXT NOT NULL DEFAULT 'new'
                CHECK (status IN ('new', 'assigned', 'audited', 'contacted', 'qualified', 'won', 'lost', 'archived')),
            assigned_team_id TEXT REFERENCES teams(id),
            assigned_agent_id TEXT REFERENCES users(id),
            created_by TEXT REFERENCES users(id),
            last_audit_id TEXT,
            last_audit_score INTEGER,
            last_audit_at TEXT,
            last_contact_at TEXT,
            version INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            CHECK (assigned_agent_id IS NULL OR assigned_team_id IS NOT NULL)
        )""",
        "CREATE INDEX ix_leads_agent ON leads(assigned_agent_id)",
        "CREATE INDEX ix_leads_team ON leads(assigned_team_id)",
        "CREATE INDEX ix_leads_status ON leads(status)",
        "CREATE INDEX ix_leads_email ON leads(email_key)",
        "CREATE INDEX ix_leads_phone ON leads(phone_key)",
        "CREATE INDEX ix_leads_company ON leads(company_key)",
        "CREATE INDEX ix_leads_created ON leads(created_at)",
        """CREATE TABLE lead_assignments (
            id TEXT PRIMARY KEY,
            lead_id TEXT NOT NULL REFERENCES leads(id),
            from_team_id TEXT,
            from_agent_id TEXT,
            to_team_id TEXT,
            to_agent_id TEXT,
            changed_by TEXT NOT NULL REFERENCES users(id),
            reason TEXT,
            created_at TEXT NOT NULL
        )""",
        "CREATE INDEX ix_lead_assignments_lead ON lead_assignments(lead_id)",
        """CREATE TABLE audits (
            id TEXT PRIMARY KEY,
            lead_id TEXT NOT NULL REFERENCES leads(id),
            requested_by TEXT NOT NULL REFERENCES users(id),
            team_id TEXT REFERENCES teams(id),
            url TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('queued', 'running', 'completed', 'failed', 'cancelled')),
            percent INTEGER NOT NULL DEFAULT 0,
            step TEXT,
            pages_discovered INTEGER NOT NULL DEFAULT 0,
            pages_processed INTEGER NOT NULL DEFAULT 0,
            issues_found INTEGER NOT NULL DEFAULT 0,
            score INTEGER,
            grade TEXT,
            critical_count INTEGER NOT NULL DEFAULT 0,
            high_count INTEGER NOT NULL DEFAULT 0,
            medium_count INTEGER NOT NULL DEFAULT 0,
            low_count INTEGER NOT NULL DEFAULT 0,
            error TEXT,
            created_at TEXT NOT NULL,
            started_at TEXT,
            completed_at TEXT
        )""",
        "CREATE INDEX ix_audits_lead ON audits(lead_id)",
        "CREATE INDEX ix_audits_status ON audits(status)",
        "CREATE INDEX ix_audits_created ON audits(created_at)",
        "CREATE INDEX ix_audits_requested_by ON audits(requested_by)",
        # At most one queued/running audit per lead (double-submit protection).
        "CREATE UNIQUE INDEX ux_audits_active_lead ON audits(lead_id) WHERE status IN ('queued', 'running')",
        """CREATE TABLE audit_issue_states (
            audit_id TEXT NOT NULL REFERENCES audits(id),
            check_id TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('open', 'resolved')),
            updated_by TEXT NOT NULL REFERENCES users(id),
            updated_at TEXT NOT NULL,
            PRIMARY KEY (audit_id, check_id)
        )""",
        """CREATE TABLE reports (
            id TEXT PRIMARY KEY,
            audit_id TEXT NOT NULL REFERENCES audits(id),
            kind TEXT NOT NULL CHECK (kind IN ('client', 'technical')),
            format TEXT NOT NULL CHECK (format IN ('docx', 'pdf')),
            stored_name TEXT NOT NULL,
            size_bytes INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE (audit_id, kind, format)
        )""",
        "CREATE INDEX ix_reports_audit ON reports(audit_id)",
        """CREATE TABLE smtp_settings (
            id TEXT PRIMARY KEY CHECK (id = 'default'),
            host TEXT NOT NULL,
            port INTEGER NOT NULL CHECK (port > 0 AND port < 65536),
            username TEXT NOT NULL,
            password_enc TEXT NOT NULL,
            encryption TEXT NOT NULL CHECK (encryption IN ('starttls', 'ssl', 'none')),
            from_name TEXT NOT NULL,
            from_email TEXT NOT NULL,
            updated_by TEXT REFERENCES users(id),
            updated_at TEXT NOT NULL
        )""",
        """CREATE TABLE app_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_by TEXT REFERENCES users(id),
            updated_at TEXT NOT NULL
        )""",
        """CREATE TABLE email_batches (
            id TEXT PRIMARY KEY,
            created_by TEXT NOT NULL REFERENCES users(id),
            total INTEGER NOT NULL,
            created_at TEXT NOT NULL
        )""",
        """CREATE TABLE emails (
            id TEXT PRIMARY KEY,
            lead_id TEXT NOT NULL REFERENCES leads(id),
            audit_id TEXT REFERENCES audits(id),
            sender_id TEXT NOT NULL REFERENCES users(id),
            team_id TEXT REFERENCES teams(id),
            batch_id TEXT REFERENCES email_batches(id),
            to_email TEXT NOT NULL,
            subject TEXT NOT NULL,
            body TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('draft', 'queued', 'sending', 'sent', 'failed')),
            error TEXT,
            attempts INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            queued_at TEXT,
            sent_at TEXT
        )""",
        "CREATE INDEX ix_emails_sender ON emails(sender_id)",
        "CREATE INDEX ix_emails_lead ON emails(lead_id)",
        "CREATE INDEX ix_emails_status ON emails(status)",
        "CREATE INDEX ix_emails_batch ON emails(batch_id)",
        """CREATE TABLE email_attachments (
            id TEXT PRIMARY KEY,
            email_id TEXT NOT NULL REFERENCES emails(id),
            report_id TEXT REFERENCES reports(id),
            upload_id TEXT REFERENCES uploads(id),
            CHECK ((report_id IS NULL) <> (upload_id IS NULL))
        )""",
        "CREATE INDEX ix_email_attachments_email ON email_attachments(email_id)",
        """CREATE TABLE conversations (
            id TEXT PRIMARY KEY,
            type TEXT NOT NULL CHECK (type IN ('direct', 'team')),
            team_id TEXT REFERENCES teams(id),
            user_a TEXT REFERENCES users(id),
            user_b TEXT REFERENCES users(id),
            created_at TEXT NOT NULL,
            last_message_at TEXT,
            CHECK ((type = 'team' AND team_id IS NOT NULL AND user_a IS NULL AND user_b IS NULL)
                OR (type = 'direct' AND team_id IS NULL AND user_a IS NOT NULL AND user_b IS NOT NULL AND user_a < user_b))
        )""",
        "CREATE UNIQUE INDEX ux_conversations_team ON conversations(team_id) WHERE type = 'team'",
        "CREATE UNIQUE INDEX ux_conversations_direct ON conversations(user_a, user_b) WHERE type = 'direct'",
        """CREATE TABLE conversation_reads (
            conversation_id TEXT NOT NULL REFERENCES conversations(id),
            user_id TEXT NOT NULL REFERENCES users(id),
            last_read_at TEXT NOT NULL,
            PRIMARY KEY (conversation_id, user_id)
        )""",
        """CREATE TABLE messages (
            id TEXT PRIMARY KEY,
            conversation_id TEXT NOT NULL REFERENCES conversations(id),
            sender_id TEXT NOT NULL REFERENCES users(id),
            body TEXT NOT NULL,
            reply_to_id TEXT REFERENCES messages(id),
            created_at TEXT NOT NULL,
            edited_at TEXT,
            deleted_at TEXT
        )""",
        "CREATE INDEX ix_messages_conversation ON messages(conversation_id, created_at)",
        """CREATE TABLE message_attachments (
            message_id TEXT NOT NULL REFERENCES messages(id),
            upload_id TEXT NOT NULL REFERENCES uploads(id),
            PRIMARY KEY (message_id, upload_id)
        )""",
        """CREATE TABLE message_reactions (
            message_id TEXT NOT NULL REFERENCES messages(id),
            user_id TEXT NOT NULL REFERENCES users(id),
            reaction TEXT NOT NULL CHECK (reaction IN ('ack', 'like', 'seen', 'question')),
            created_at TEXT NOT NULL,
            PRIMARY KEY (message_id, user_id, reaction)
        )""",
        """CREATE TABLE notifications (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL REFERENCES users(id),
            type TEXT NOT NULL,
            title TEXT NOT NULL,
            body TEXT NOT NULL,
            entity_type TEXT,
            entity_id TEXT,
            is_read INTEGER NOT NULL DEFAULT 0 CHECK (is_read IN (0, 1)),
            created_at TEXT NOT NULL
        )""",
        "CREATE INDEX ix_notifications_user ON notifications(user_id, is_read, created_at)",
        """CREATE TABLE candidates (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            email TEXT,
            phone TEXT,
            status TEXT NOT NULL DEFAULT 'new'
                CHECK (status IN ('new', 'reviewed', 'shortlisted', 'rejected', 'hired')),
            notes TEXT,
            cv_upload_id TEXT REFERENCES uploads(id),
            created_by TEXT NOT NULL REFERENCES users(id),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )""",
        "CREATE INDEX ix_candidates_status ON candidates(status)",
        """CREATE TABLE audit_logs (
            id TEXT PRIMARY KEY,
            user_id TEXT,
            user_email TEXT,
            action TEXT NOT NULL,
            entity_type TEXT,
            entity_id TEXT,
            metadata TEXT,
            ip TEXT,
            created_at TEXT NOT NULL
        )""",
        "CREATE INDEX ix_audit_logs_user ON audit_logs(user_id)",
        "CREATE INDEX ix_audit_logs_created ON audit_logs(created_at)",
        "CREATE INDEX ix_audit_logs_action ON audit_logs(action)",
        "CREATE INDEX ix_audit_logs_entity ON audit_logs(entity_type, entity_id)",
        """CREATE TABLE idempotency_keys (
            user_id TEXT NOT NULL REFERENCES users(id),
            key TEXT NOT NULL,
            endpoint TEXT NOT NULL,
            status_code INTEGER NOT NULL,
            response TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (user_id, key)
        )""",
    ] + (_PG_AUDIT_LOG_GUARD if db.DIALECT == "postgres" else _SQLITE_AUDIT_LOG_GUARD)


def _m002_lead_bank():
    """Lead bank: niche/area taxonomy, Google Maps fields, import batches with
    rejected-row reports, indexes for large banks. Removes the CV bank."""
    return [
        """CREATE TABLE lead_batches (
            id TEXT PRIMARY KEY,
            file_name TEXT NOT NULL,
            stored_name TEXT,
            file_size INTEGER NOT NULL DEFAULT 0,
            niche TEXT,
            area TEXT,
            niche_key TEXT,
            area_key TEXT,
            source TEXT,
            status TEXT NOT NULL CHECK (status IN ('pending', 'queued', 'processing', 'completed', 'failed', 'cancelled')),
            headers TEXT,
            mapping TEXT,
            total_rows INTEGER NOT NULL DEFAULT 0,
            processed_rows INTEGER NOT NULL DEFAULT 0,
            imported INTEGER NOT NULL DEFAULT 0,
            duplicates INTEGER NOT NULL DEFAULT 0,
            no_website INTEGER NOT NULL DEFAULT 0,
            invalid INTEGER NOT NULL DEFAULT 0,
            team_id TEXT REFERENCES teams(id),
            agent_id TEXT REFERENCES users(id),
            created_by TEXT NOT NULL REFERENCES users(id),
            created_at TEXT NOT NULL,
            started_at TEXT,
            completed_at TEXT,
            error TEXT
        )""",
        "CREATE INDEX ix_lead_batches_created ON lead_batches(created_at)",
        "CREATE INDEX ix_lead_batches_status ON lead_batches(status)",
        """CREATE TABLE lead_batch_rejects (
            id TEXT PRIMARY KEY,
            batch_id TEXT NOT NULL REFERENCES lead_batches(id),
            row_number INTEGER NOT NULL,
            company TEXT,
            website TEXT,
            phone TEXT,
            reason TEXT NOT NULL CHECK (reason IN ('no_website', 'invalid_website', 'duplicate_in_file',
                                                   'duplicate_existing')),
            existing_lead_id TEXT
        )""",
        "CREATE INDEX ix_lead_batch_rejects_batch ON lead_batch_rejects(batch_id, row_number)",
        "ALTER TABLE leads RENAME COLUMN industry TO niche",
        "ALTER TABLE leads RENAME COLUMN location TO area",
        "ALTER TABLE leads ADD COLUMN niche_key TEXT",
        "ALTER TABLE leads ADD COLUMN area_key TEXT",
        "ALTER TABLE leads ADD COLUMN address TEXT",
        "ALTER TABLE leads ADD COLUMN category TEXT",
        "ALTER TABLE leads ADD COLUMN rating REAL",
        "ALTER TABLE leads ADD COLUMN review_count INTEGER",
        "ALTER TABLE leads ADD COLUMN maps_url TEXT",
        "ALTER TABLE leads ADD COLUMN extra TEXT",
        "ALTER TABLE leads ADD COLUMN batch_id TEXT REFERENCES lead_batches(id)",
        "UPDATE leads SET niche_key = LOWER(TRIM(niche)) WHERE niche IS NOT NULL AND TRIM(niche) <> ''",
        "UPDATE leads SET area_key = LOWER(TRIM(area)) WHERE area IS NOT NULL AND TRIM(area) <> ''",
        "CREATE INDEX ix_leads_niche_area ON leads(niche_key, area_key)",
        "CREATE INDEX ix_leads_area ON leads(area_key)",
        "CREATE INDEX ix_leads_batch ON leads(batch_id)",
        "CREATE INDEX ix_leads_team_status ON leads(assigned_team_id, status)",
        "CREATE INDEX ix_leads_last_contact ON leads(last_contact_at)",
        "DROP TABLE candidates",
    ]


def _m003_audit_queue():
    """Saved audit queue (survives restarts) with bulk batches, compressed audit
    results, and report rows whose PDF is rendered on first use."""
    blob = "BYTEA" if db.DIALECT == "postgres" else "BLOB"
    return [
        """CREATE TABLE audit_batches (
            id TEXT PRIMARY KEY,
            label TEXT,
            created_by TEXT NOT NULL REFERENCES users(id),
            team_id TEXT REFERENCES teams(id),
            status TEXT NOT NULL CHECK (status IN ('active', 'completed', 'cancelled')),
            total INTEGER NOT NULL DEFAULT 0,
            skipped TEXT,
            created_at TEXT NOT NULL,
            completed_at TEXT
        )""",
        "CREATE INDEX ix_audit_batches_created ON audit_batches(created_at)",
        "CREATE INDEX ix_audit_batches_status ON audit_batches(status)",
        "ALTER TABLE audits ADD COLUMN batch_id TEXT REFERENCES audit_batches(id)",
        "ALTER TABLE audits ADD COLUMN priority INTEGER NOT NULL DEFAULT 1",
        "ALTER TABLE audits ADD COLUMN params TEXT",
        "ALTER TABLE audits ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0",
        "CREATE INDEX ix_audits_queue ON audits(status, priority, created_at)",
        "CREATE INDEX ix_audits_batch ON audits(batch_id, status)",
        f"""CREATE TABLE audit_results (
            audit_id TEXT PRIMARY KEY REFERENCES audits(id),
            encoding TEXT NOT NULL CHECK (encoding IN ('json+gzip')),
            data {blob} NOT NULL,
            raw_size INTEGER NOT NULL,
            stored_size INTEGER NOT NULL,
            created_at TEXT NOT NULL
        )""",
        "ALTER TABLE reports ADD COLUMN ready INTEGER NOT NULL DEFAULT 1",
    ]


MIGRATIONS = [
    (1, "initial schema", _m001_initial),
    (2, "lead bank", _m002_lead_bank),
    (3, "audit queue", _m003_audit_queue),
]

# Best-effort performance objects (run outside migrations; failures are logged,
# e.g. when the database role may not create extensions).
_PG_OPTIONAL = [
    "CREATE EXTENSION IF NOT EXISTS pg_trgm",
    "CREATE INDEX IF NOT EXISTS ix_leads_company_trgm ON leads USING gin (LOWER(company) gin_trgm_ops)",
    "CREATE INDEX IF NOT EXISTS ix_leads_domain_trgm ON leads USING gin (normalized_domain gin_trgm_ops)",
    "CREATE INDEX IF NOT EXISTS ix_leads_contact_trgm ON leads USING gin (LOWER(COALESCE(contact_name, '')) gin_trgm_ops)",
]


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def migrate():
    """Apply all pending migrations. Safe to call on every start."""
    with db.transaction() as tx:
        tx.execute("""CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            applied_at TEXT NOT NULL
        )""")
        applied = {r["version"] for r in tx.query("SELECT version FROM schema_migrations")}
    for version, name, build in MIGRATIONS:
        if version in applied:
            continue
        with db.transaction() as tx:
            for stmt in build():
                tx.execute(stmt)
            tx.execute("INSERT INTO schema_migrations (version, name, applied_at) VALUES (%s, %s, %s)",
                       (version, name, _now()))
    if db.DIALECT == "postgres":
        import logging
        for stmt in _PG_OPTIONAL:
            try:
                db.execute(stmt)
            except Exception as e:  # noqa: BLE001
                logging.getLogger("seo_audit_pro").warning("Optional index not created (%s): %s", stmt[:60], e)
