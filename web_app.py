# -*- coding: utf-8 -*-
"""
SEO Audit Pro — Web Application Server.
Modern, interactive web interface for laymen and technical SEO experts.
"""

import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import webbrowser
from datetime import datetime
from urllib.parse import urlparse

from flask import (
    Flask,
    Response,
    jsonify,
    render_template,
    request,
    send_file,
    send_from_directory,
)

import audit
import config
import history_manager
import llm
import pagespeed
import prefs
import report_docx
import report_html
import seo_checks

# Load and synchronize environment keys
config.load_env()
prefs.apply_saved_env()

if getattr(sys, "frozen", False):
    BUNDLE_DIR = sys._MEIPASS
    EXE_DIR = os.path.dirname(sys.executable)
else:
    BUNDLE_DIR = os.path.dirname(os.path.abspath(__file__))
    EXE_DIR = BUNDLE_DIR

APP_DIR = BUNDLE_DIR
REPORTS_DIR = os.path.join(EXE_DIR, "reports")
os.makedirs(REPORTS_DIR, exist_ok=True)

# Ensure stdout and stderr are valid streams in windowed (noconsole) mode
if sys.stdout is None:
    try:
        sys.stdout = open(os.path.join(REPORTS_DIR, "server.log"), "a", encoding="utf-8")
    except Exception:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    try:
        sys.stderr = open(os.path.join(REPORTS_DIR, "server_err.log"), "a", encoding="utf-8")
    except Exception:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")

app = Flask(
    __name__,
    template_folder=os.path.join(BUNDLE_DIR, "templates"),
    static_folder=os.path.join(BUNDLE_DIR, "static"),
)
app.config["JSON_AS_ASCII"] = False

# In-memory store for active/recent audits
# audit_id -> { id, status, percent, step, logs, result, error, cancel_event, docx_path, pdf_path }
ACTIVE_AUDITS = {}
AUDITS_LOCK = threading.RLock()


def _docx_to_pdf(docx_path, pdf_path):
    """Render a .docx to PDF via Microsoft Word (Windows) COM automation if available."""
    if sys.platform != "win32":
        return False, "PDF COM automation is only available on Windows."
    try:
        docx_path, pdf_path = os.path.abspath(docx_path), os.path.abspath(pdf_path)
        ps = (
            "param($in,$out)\n"
            "try {\n"
            "  $w = New-Object -ComObject Word.Application\n"
            "  $w.Visible = $false\n"
            "  $d = $w.Documents.Open($in)\n"
            "  try { $d.Fields.Update() | Out-Null } catch {}\n"
            "  try { foreach($t in $d.TablesOfContents){ $t.Update() } } catch {}\n"
            "  $d.SaveAs([ref]$out,[ref]17)\n"
            "  $d.Close($false); $w.Quit()\n"
            "  Write-Output 'OK'\n"
            "} catch { Write-Output ('ERR:' + $_.Exception.Message) }\n"
        )
        fh = tempfile.NamedTemporaryFile("w", suffix=".ps1", delete=False, encoding="utf-8")
        fh.write(ps)
        fh.close()
        proc = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                fh.name,
                docx_path,
                pdf_path,
            ],
            capture_output=True,
            text=True,
            timeout=180,
        )
        out = ((proc.stdout or "") + (proc.stderr or "")).strip()
        if os.path.exists(pdf_path) and "ERR:" not in out:
            return True, ""
        return False, (out[:200] or "Microsoft Word not available")
    except Exception as e:
        return False, str(e)
    finally:
        try:
            os.remove(fh.name)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Background Audit Execution
# ---------------------------------------------------------------------------
def _execute_audit_thread(audit_id, params):
    state = ACTIVE_AUDITS[audit_id]
    cancel_event = state["cancel_event"]
    state["status"] = "running"
    state["percent"] = 5
    state["step"] = "Initializing audit pipeline…"

    def log_progress(msg):
        with AUDITS_LOCK:
            timestamp = datetime.now().strftime("%H:%M:%S")
            state["logs"].append(f"[{timestamp}] {msg}")
            state["step"] = msg

            # Heuristic percentage updates based on stage messages
            low_msg = msg.lower()
            if "homepage" in low_msg:
                state["percent"] = max(state["percent"], 10)
            elif "robots" in low_msg or "sitemap" in low_msg:
                state["percent"] = max(state["percent"], 20)
            elif "crawling" in low_msg:
                # Crawl progress
                m = re.search(r"(\d+)/(\d+)", low_msg)
                if m:
                    curr, total = int(m.group(1)), int(m.group(2))
                    p = 25 + int((curr / max(1, total)) * 25)
                    state["percent"] = max(state["percent"], min(p, 50))
                else:
                    state["percent"] = max(state["percent"], 30)
            elif "analyzing pages" in low_msg:
                state["percent"] = max(state["percent"], 55)
            elif "links" in low_msg:
                state["percent"] = max(state["percent"], 65)
            elif "pagespeed" in low_msg:
                state["percent"] = max(state["percent"], 75)
            elif "structured" in low_msg:
                state["percent"] = max(state["percent"], 85)
            elif "content" in low_msg or "scoring" in low_msg:
                state["percent"] = max(state["percent"], 92)
            elif "competitor" in low_msg:
                state["percent"] = max(state["percent"], 95)
            elif "executive summary" in low_msg:
                state["percent"] = max(state["percent"], 97)
            elif "done" in low_msg:
                state["percent"] = 100

    try:
        log_progress(f"Starting audit for {params.get('url')}…")
        params["cancel"] = cancel_event

        result = audit.run_audit(params, progress=log_progress)

        # Generate DOCX report automatically
        safe_domain = re.sub(r"[^a-zA-Z0-9_\-\.]", "_", result.get("domain", "site"))
        date_str = result.get("date") or datetime.now().strftime("%Y-%m-%d")
        docx_filename = f"SEO-Audit-{safe_domain}-{date_str}-{audit_id[:6]}.docx"
        docx_path = os.path.join(REPORTS_DIR, docx_filename)
        try:
            report_docx.generate_docx_report(result, docx_path)
            state["docx_path"] = docx_path
            log_progress("Generated Word (.docx) report deliverable.")
        except Exception as e:
            print(f"[audit] Error generating docx: {e}")
            state["docx_path"] = None

        # Attempt PDF conversion via MS Word (Windows only, non-blocking)
        try:
            if sys.platform == "win32" and state.get("docx_path") and os.path.isfile(state["docx_path"]):
                pdf_filename = f"SEO-Audit-{safe_domain}-{date_str}-{audit_id[:6]}.pdf"
                pdf_path = os.path.join(REPORTS_DIR, pdf_filename)
                ok_pdf, _ = _docx_to_pdf(state["docx_path"], pdf_path)
                if ok_pdf and os.path.isfile(pdf_path):
                    state["pdf_path"] = pdf_path
                    log_progress("Generated PDF report deliverable.")
        except Exception as pe:
            print(f"[audit] Optional PDF conversion skipped: {pe}")

        # Persist audit into database / history store (safely wrapped)
        try:
            history_summary = history_manager.save_audit_result(audit_id, result)
            result["history_summary"] = history_summary
        except Exception as he:
            print(f"[audit] History save warning: {he}")

        log_progress("Audit finished and saved.")

        with AUDITS_LOCK:
            state["status"] = "completed"
            state["percent"] = 100
            state["step"] = "Audit completed successfully!"
            state["result"] = result

    except RuntimeError as re_err:
        with AUDITS_LOCK:
            state["status"] = "cancelled" if "cancelled" in str(re_err).lower() else "failed"
            state["error"] = str(re_err)
            state["step"] = f"Audit stopped: {re_err}"
            state["logs"].append(f"[{datetime.now().strftime('%H:%M:%S')}] {re_err}")
    except Exception as e:
        with AUDITS_LOCK:
            state["status"] = "failed"
            state["error"] = str(e)
            state["step"] = f"Error: {e}"
            state["logs"].append(f"[{datetime.now().strftime('%H:%M:%S')}] Exception: {e}")


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/favicon.ico")
def favicon():
    return send_from_directory(
        os.path.join(APP_DIR, "static", "assets"),
        "favicon.ico",
        mimetype="image/vnd.microsoft.icon",
    )


@app.route("/api/audit/start", methods=["POST"])
def start_audit():
    data = request.get_json() or {}
    url = (data.get("url") or "").strip()
    if not url:
        return jsonify({"error": "Website URL is required"}), 400

    audit_id = f"audit_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"

    # Read user preferences & overrides
    prefs_data = prefs.load_settings()

    # Enabled categories
    enabled_keys = data.get("enabled")
    if enabled_keys and isinstance(enabled_keys, list):
        enabled_set = set(enabled_keys)
    else:
        enabled_set = {k for k, _l, _w in seo_checks.CATEGORIES}

    # API keys from env or overrides
    api_key = (
        data.get("api_key")
        or os.environ.get("GROQ_API_KEY")
        or os.environ.get("OPENROUTER_API_KEY")
        or os.environ.get("ANTHROPIC_API_KEY")
        or os.environ.get("GEMINI_API_KEY")
        or ""
    )
    psi_key = (
        data.get("psi_key")
        or os.environ.get("PAGESPEED_API_KEY")
        or ""
    )

    params = {
        "url": url,
        "max_pages": int(data.get("max_pages") or prefs_data.get("max_pages") or 25),
        "single_page": bool(data.get("single_page", prefs_data.get("single_page", False))),
        "psi_strategy": data.get("psi_strategy") or prefs_data.get("psi_strategy") or "both",
        "proxy": data.get("proxy") or prefs_data.get("proxy") or None,
        "enabled": enabled_set,
        "api_key": api_key,
        "model": data.get("model") or prefs_data.get("model") or "",
        "psi_key": psi_key,
        "competitors": [c.strip() for c in (data.get("competitors") or []) if c.strip()],
    }

    cancel_event = threading.Event()
    with AUDITS_LOCK:
        ACTIVE_AUDITS[audit_id] = {
            "id": audit_id,
            "url": url,
            "status": "pending",
            "percent": 0,
            "step": "Queued…",
            "logs": [f"[{datetime.now().strftime('%H:%M:%S')}] Audit queued for {url}"],
            "result": None,
            "error": None,
            "cancel_event": cancel_event,
            "docx_path": None,
            "pdf_path": None,
        }

    t = threading.Thread(
        target=_execute_audit_thread, args=(audit_id, params), daemon=True
    )
    t.start()

    return jsonify({"audit_id": audit_id, "url": url, "status": "running"})


@app.route("/api/audit/status/<audit_id>", methods=["GET"])
def audit_status(audit_id):
    with AUDITS_LOCK:
        state = ACTIVE_AUDITS.get(audit_id)
        if not state:
            # Check if saved on disk in history
            hist = history_manager.get_audit(audit_id)
            if hist:
                return jsonify({
                    "id": audit_id,
                    "status": "completed",
                    "percent": 100,
                    "step": "Loaded from history",
                    "logs": ["Loaded completed audit from history archive."],
                    "result": hist,
                    "error": None,
                    "has_docx": bool(hist.get("domain")),
                    "has_pdf": False,
                })
            return jsonify({"error": "Audit not found"}), 404

        # Ensure result is present if status is completed
        res_payload = state.get("result")
        if not res_payload and state["status"] == "completed":
            res_payload = history_manager.get_audit(audit_id)
            state["result"] = res_payload

        # Return state
        return jsonify({
            "id": state["id"],
            "url": state.get("url", ""),
            "status": state["status"],
            "percent": state["percent"],
            "step": state["step"],
            "logs": state["logs"][-60:],  # Return latest 60 logs
            "result": res_payload,
            "error": state["error"],
            "has_docx": bool(state.get("docx_path") and os.path.isfile(state["docx_path"])),
            "has_pdf": bool(state.get("pdf_path") and os.path.isfile(state["pdf_path"])),
        })


@app.route("/api/audit/stream/<audit_id>", methods=["GET"])
def audit_stream(audit_id):
    """Server-Sent Events (SSE) stream for real-time progress updates."""
    def generate():
        last_log_count = 0
        while True:
            with AUDITS_LOCK:
                state = ACTIVE_AUDITS.get(audit_id)
            if not state:
                yield f"data: {json.dumps({'error': 'Audit not found'})}\n\n"
                break

            logs = state["logs"]
            new_logs = logs[last_log_count:]
            last_log_count = len(logs)

            data = {
                "id": state["id"],
                "status": state["status"],
                "percent": state["percent"],
                "step": state["step"],
                "new_logs": new_logs,
                "error": state["error"],
            }

            if state["status"] in ("completed", "failed", "cancelled"):
                if state["status"] == "completed":
                    data["result"] = state.get("result") or history_manager.get_audit(audit_id)
                yield f"data: {json.dumps(data)}\n\n"
                break

            yield f"data: {json.dumps(data)}\n\n"
            time.sleep(0.5)

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.route("/api/audit/cancel/<audit_id>", methods=["POST"])
def cancel_audit(audit_id):
    with AUDITS_LOCK:
        state = ACTIVE_AUDITS.get(audit_id)
        if not state:
            return jsonify({"error": "Audit not found"}), 404
        state["cancel_event"].set()
        state["status"] = "cancelling"
        state["step"] = "Cancelling audit…"
        state["logs"].append(f"[{datetime.now().strftime('%H:%M:%S')}] User requested cancellation.")
    return jsonify({"status": "cancelling"})


@app.route("/api/audit/<audit_id>/docx", methods=["GET"])
def download_docx(audit_id):
    # Check active state
    state = ACTIVE_AUDITS.get(audit_id)
    docx_path = state.get("docx_path") if state else None

    # Check if exists on disk
    if not docx_path or not os.path.isfile(docx_path):
        # Look for matching file in reports dir
        for fname in os.listdir(REPORTS_DIR):
            if fname.endswith(".docx") and audit_id[:6] in fname:
                docx_path = os.path.join(REPORTS_DIR, fname)
                break

    # If still not found, generate on the fly from stored result
    if not docx_path or not os.path.isfile(docx_path):
        res = (state.get("result") if state else None) or history_manager.get_audit(audit_id)
        if res:
            safe_domain = re.sub(r"[^a-zA-Z0-9_\-\.]", "_", res.get("domain", "site"))
            date_str = res.get("date") or datetime.now().strftime("%Y-%m-%d")
            docx_filename = f"SEO-Audit-{safe_domain}-{date_str}-{audit_id[:6]}.docx"
            docx_path = os.path.join(REPORTS_DIR, docx_filename)
            report_docx.generate_docx_report(res, docx_path)

    if docx_path and os.path.isfile(docx_path):
        fname = os.path.basename(docx_path)
        return send_file(
            docx_path,
            as_attachment=True,
            download_name=fname,
            mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
    return jsonify({"error": "DOCX report could not be found or generated."}), 404


@app.route("/api/audit/<audit_id>/pdf", methods=["GET"])
def download_pdf(audit_id):
    state = ACTIVE_AUDITS.get(audit_id)
    pdf_path = state.get("pdf_path") if state else None

    if not pdf_path or not os.path.isfile(pdf_path):
        for fname in os.listdir(REPORTS_DIR):
            if fname.endswith(".pdf") and audit_id[:6] in fname:
                pdf_path = os.path.join(REPORTS_DIR, fname)
                break

    # If docx exists, try converting to PDF
    if not pdf_path or not os.path.isfile(pdf_path):
        docx_path = state.get("docx_path") if state else None
        if not docx_path or not os.path.isfile(docx_path):
            for fname in os.listdir(REPORTS_DIR):
                if fname.endswith(".docx") and audit_id[:6] in fname:
                    docx_path = os.path.join(REPORTS_DIR, fname)
                    break
        if docx_path and os.path.isfile(docx_path):
            cand_pdf = docx_path.replace(".docx", ".pdf")
            ok, _ = _docx_to_pdf(docx_path, cand_pdf)
            if ok and os.path.isfile(cand_pdf):
                pdf_path = cand_pdf

    if pdf_path and os.path.isfile(pdf_path):
        fname = os.path.basename(pdf_path)
        return send_file(
            pdf_path,
            as_attachment=True,
            download_name=fname,
            mimetype="application/pdf",
        )
    return jsonify({
        "error": "Direct PDF export requires Microsoft Word. You can print to PDF directly from your browser using the Print / PDF option in the report dashboard."
    }), 404


@app.route("/api/audit/<audit_id>/json", methods=["GET"])
def download_json(audit_id):
    state = ACTIVE_AUDITS.get(audit_id)
    res = (state.get("result") if state else None) or history_manager.get_audit(audit_id)
    if not res:
        return jsonify({"error": "Audit result not found"}), 404

    safe_domain = re.sub(r"[^a-zA-Z0-9_\-\.]", "_", res.get("domain", "site"))
    date_str = res.get("date") or datetime.now().strftime("%Y-%m-%d")
    filename = f"SEO-Audit-{safe_domain}-{date_str}.json"

    return Response(
        json.dumps(res, indent=2, ensure_ascii=False),
        mimetype="application/json",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


# ---------------------------------------------------------------------------
# History & Progress Tracking APIs
# ---------------------------------------------------------------------------
@app.route("/api/history", methods=["GET"])
def get_history():
    history = history_manager.load_history()
    return jsonify({"history": history})


@app.route("/api/history/<audit_id>", methods=["GET"])
def get_history_item(audit_id):
    res = history_manager.get_audit(audit_id)
    if not res:
        return jsonify({"error": "Historical audit not found"}), 404
    return jsonify({"audit": res})


@app.route("/api/history/<audit_id>", methods=["DELETE"])
def delete_history_item(audit_id):
    history_manager.delete_audit(audit_id)
    return jsonify({"success": True})


@app.route("/api/progress/timeline", methods=["GET"])
def get_progress_timeline():
    domain = request.args.get("domain", "").strip()
    if not domain:
        return jsonify({"error": "Domain parameter is required"}), 400
    timeline = history_manager.get_domain_progress(domain)
    return jsonify({"domain": domain, "timeline": timeline})


@app.route("/api/progress/compare", methods=["POST"])
def compare_progress():
    data = request.get_json() or {}
    audit1 = data.get("audit1")
    audit2 = data.get("audit2")
    if not audit1 or not audit2:
        return jsonify({"error": "Two audit IDs (audit1, audit2) are required for comparison"}), 400
    comparison = history_manager.compare_audits(audit1, audit2)
    return jsonify(comparison)


@app.route("/api/progress/targets", methods=["GET", "POST"])
def domain_targets():
    if request.method == "GET":
        domain = request.args.get("domain", "").strip()
        if not domain:
            return jsonify({"error": "Domain parameter is required"}), 400
        targets = history_manager.get_targets(domain)
        return jsonify(targets)
    data = request.get_json() or {}
    domain = data.get("domain", "").strip()
    if not domain:
        return jsonify({"error": "Domain is required"}), 400
    saved = history_manager.save_targets(domain, data.get("targets", {}))
    return jsonify({"success": True, "targets": saved})


@app.route("/api/progress/simulate", methods=["POST"])
def simulate_fixes_endpoint():
    data = request.get_json() or {}
    audit_id = data.get("audit_id")
    fixed_ids = data.get("fixed_check_ids", [])
    if not audit_id:
        return jsonify({"error": "audit_id is required"}), 400
    res = history_manager.simulate_fixes(audit_id, fixed_ids)
    return jsonify(res)


# ---------------------------------------------------------------------------
# Settings & Preferences APIs
# ---------------------------------------------------------------------------
@app.route("/api/settings", methods=["GET"])
def get_settings():
    saved = prefs.load_settings()
    # Check env keys presence
    keys_status = {
        "groq": bool(os.environ.get("GROQ_API_KEY")),
        "openrouter": bool(os.environ.get("OPENROUTER_API_KEY")),
        "anthropic": bool(os.environ.get("ANTHROPIC_API_KEY")),
        "gemini": bool(os.environ.get("GEMINI_API_KEY")),
        "pagespeed": bool(os.environ.get("PAGESPEED_API_KEY")),
        "moz_access": os.environ.get("MOZ_ACCESS_ID", ""),
        "moz_secret": bool(os.environ.get("MOZ_SECRET_KEY")),
    }
    return jsonify({
        "settings": saved,
        "keys": keys_status,
        "categories": [{"key": k, "label": l, "weight": w} for k, l, w in seo_checks.CATEGORIES],
    })


@app.route("/api/settings", methods=["POST"])
def save_settings():
    data = request.get_json() or {}
    settings = data.get("settings", {})
    env_keys = data.get("keys", {})

    # Save non-secret preferences
    if settings:
        prefs.save_settings(settings)

    # Save API keys to .env
    env_map = {}
    if "groq_api_key" in env_keys:
        env_map["GROQ_API_KEY"] = env_keys["groq_api_key"].strip()
    if "openrouter_api_key" in env_keys:
        env_map["OPENROUTER_API_KEY"] = env_keys["openrouter_api_key"].strip()
    if "anthropic_api_key" in env_keys:
        env_map["ANTHROPIC_API_KEY"] = env_keys["anthropic_api_key"].strip()
    if "gemini_api_key" in env_keys:
        env_map["GEMINI_API_KEY"] = env_keys["gemini_api_key"].strip()
    if "pagespeed_api_key" in env_keys:
        env_map["PAGESPEED_API_KEY"] = env_keys["pagespeed_api_key"].strip()
    if "moz_access_id" in env_keys:
        env_map["MOZ_ACCESS_ID"] = env_keys["moz_access_id"].strip()
    if "moz_secret_key" in env_keys:
        env_map["MOZ_SECRET_KEY"] = env_keys["moz_secret_key"].strip()

    if env_map:
        prefs.save_env(env_map)

    return jsonify({"success": True, "message": "Settings saved successfully."})


@app.route("/api/open-reports", methods=["POST"])
def open_reports_folder():
    try:
        if sys.platform == "win32":
            os.startfile(REPORTS_DIR)
        elif sys.platform == "darwin":
            subprocess.run(["open", REPORTS_DIR])
        else:
            subprocess.run(["xdg-open", REPORTS_DIR])
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ---------------------------------------------------------------------------
# Server startup & Browser launch helper
# ---------------------------------------------------------------------------
def find_available_port(start_port=5055, max_attempts=50):
    for port in range(start_port, start_port + max_attempts):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return port
    return start_port


def open_browser_after_delay(url, delay=0.7):
    def _open():
        time.sleep(delay)
        print(f"\n[SEO Audit Pro] Opening web UI in your browser: {url}")
        try:
            webbrowser.open(url)
        except Exception as e:
            print(f"[SEO Audit Pro] Could not auto-open browser: {e}")

    threading.Thread(target=_open, daemon=True).start()


def start_server(port=None, host=None, auto_open=None):
    env_port = os.environ.get("PORT")
    if env_port:
        port = int(env_port)
        host = host or "0.0.0.0"
        if auto_open is None:
            auto_open = False
    else:
        if port is None:
            port = find_available_port(5055)
        host = host or "127.0.0.1"
        if auto_open is None:
            auto_open = True

    url = f"http://{host}:{port}"
    print("=" * 60)
    print("  [+] SEO Audit Pro — Web Server")
    print(f"  URL: {url}")
    print("  All features running: Web UI, Live Stream, Exports & Tracker")
    print("=" * 60)

    if auto_open:
        open_browser_after_delay(url)

    # Disable Flask banner reloader to avoid double-spawning
    app.run(host=host, port=port, debug=False, threaded=True)


if __name__ == "__main__":
    start_server()
