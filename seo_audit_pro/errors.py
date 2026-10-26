# -*- coding: utf-8 -*-
"""Plain-language explanations for audit failures: what happened, why, and what
to do. Used by audits run in the background (team) and in Scrape & Audit."""

import errno
import re

# (words in the engine's "Could not fetch the site: ..." text, explanation)
FETCH_PROBLEMS = [
    (("RemoteDisconnected", "Connection aborted", "ConnectionResetError", "reset by peer"),
     "The website closed the connection without answering. Its firewall may block automated visits. Try again later."),
    (("timed out", "Timeout", "ReadTimeout", "ConnectTimeout"),
     "The website took too long to answer (over 25 seconds). It may be down or very slow. Try again later."),
    (("getaddrinfo", "NameResolutionError", "Failed to resolve", "Name or service not known", "nodename nor servname"),
     "The website address could not be found (no such domain). Check the spelling of the website, "
     "or check this computer's internet connection."),
    (("SSLError", "CERTIFICATE_VERIFY_FAILED", "certificate", "SSL:"),
     "The website's security certificate is not valid (expired or wrong), so it could not be checked safely. "
     "That itself is a serious problem for the site."),
    (("TooManyRedirects", "Exceeded 30 redirects"),
     "The website redirects in a loop and never shows a page. Its redirect settings are broken."),
    (("ProxyError",), "A proxy on this computer's network blocked the connection. Check the network settings."),
    (("Max retries exceeded", "ConnectionError", "refused", "Failed to establish a new connection"),
     "The website could not be reached. It may be down, or this computer has no internet connection."),
]

HTTP_STATUS = {
    401: "The website asks for a password, so it cannot be audited.",
    403: "The website refused our automated check (status 403). Its firewall or bot protection (for example "
         "Cloudflare) blocks non-browser visits. Ask the site owner to allow it, or try again later.",
    404: "The website address returns \"page not found\" (status 404). Check the website address of this lead.",
    410: "The website says this page is gone for good (status 410). Check the website address.",
    429: "The website is limiting visits right now (status 429, too many requests). Wait a few minutes and try again.",
    451: "The website is not available in this country (status 451).",
}


def _http_status_message(status):
    if status in HTTP_STATUS:
        return HTTP_STATUS[status]
    if 500 <= status < 600:
        return (f"The website's server has an error right now (status {status}). The site may be down or "
                "being updated. Try again later.")
    return f"The website answered with status {status} instead of a page."


def explain(exc):
    """(message for people, short code) for an exception raised by an audit."""
    if getattr(exc, "message", None):  # errors that already explain themselves (lead files, API)
        return str(exc.message)[:400], getattr(exc, "code", "error") or "error"
    text = str(exc) or ""
    if isinstance(exc, ValueError):  # the engine raises ValueError with text meant for people
        if text.startswith("Could not fetch the site"):
            for words, plain in FETCH_PROBLEMS:
                if any(w in text for w in words):
                    return plain, "site_unreachable"
            return "The website could not be opened: " + text.split(":", 1)[-1].strip()[:200], "site_unreachable"
        m = re.search(r"did not return an HTML page \(status (\d+)\)", text)
        if m:
            status = int(m.group(1))
            if 200 <= status < 300:
                return ("The website address does not show a web page (it returned a file or an empty answer). "
                        "Check that the lead's website is the home page."), "not_html"
            return _http_status_message(status), f"http_{status}"
        if "Please enter a website URL" in text:
            return "This lead has no website address to audit. Add the website first.", "no_url"
        if "cancel" in text.lower():
            return "The audit was stopped.", "cancelled"
        return text[:400], "site_problem"
    if type(exc).__name__ in ("Cancelled", "CancelledError") or "cancelled" in text.lower():
        return "The audit was stopped.", "cancelled"
    try:
        import requests
        if isinstance(exc, requests.RequestException):
            for words, plain in FETCH_PROBLEMS:
                if any(w in f"{type(exc).__name__} {text}" for w in words):
                    return plain, "network"
            return "A network problem stopped the audit. Check the internet connection and try again.", "network"
    except ImportError:
        pass
    if isinstance(exc, MemoryError):
        return ("This computer ran out of memory during the audit. Close other programs, lower \"Pages per site\" "
                "or \"At the same time\", and try again."), "memory"
    if isinstance(exc, PermissionError):
        return ("Windows refused access to a file the audit needs (often antivirus or a protected folder). "
                f"Allow Rankly in the antivirus, or save reports to another folder. ({text[:120]})"), "permission"
    if isinstance(exc, OSError) and getattr(exc, "errno", None) == errno.ENOSPC:
        return "The disk is full, so the reports could not be saved. Free some space and try again.", "disk_full"
    if isinstance(exc, (ImportError, ModuleNotFoundError)):
        return ("Part of the program is missing or damaged. Extract the whole Rankly zip again (do not run it from "
                f"inside the zip) and try again. (Missing: {getattr(exc, 'name', '') or text[:80]})"), "install_damaged"
    if isinstance(exc, OSError):
        return f"A file or folder problem stopped the audit: {text[:200]}", "file_error"
    return (f"An unexpected error stopped the audit ({type(exc).__name__}: {text[:160]}). The details were saved "
            "in the log; please send it to support."), "unexpected"
