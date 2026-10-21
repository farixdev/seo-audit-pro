# -*- coding: utf-8 -*-
"""Uniform JSON error responses. Internal details are logged, never returned."""

import logging
import traceback

from flask import jsonify

log = logging.getLogger("seo_audit_pro")


class ApiError(Exception):
    def __init__(self, status, message, code=None, **extra):
        super().__init__(message)
        self.status = status
        self.message = message
        self.code = code or {400: "bad_request", 401: "unauthorized", 403: "forbidden",
                             404: "not_found", 409: "conflict", 413: "too_large",
                             422: "invalid", 429: "rate_limited"}.get(status, "error")
        self.extra = extra


def bad_request(msg, **extra):
    return ApiError(400, msg, **extra)


def forbidden(msg="You do not have permission to perform this action."):
    return ApiError(403, msg)


def not_found(what="Resource"):
    # Identical response for "missing" and "exists but not yours" (IDOR-safe).
    return ApiError(404, f"{what} not found.")


def conflict(msg, **extra):
    return ApiError(409, msg, **extra)


def register(app):
    from backend.db import IntegrityError

    @app.errorhandler(ApiError)
    def _api_error(e):
        body = {"error": e.message, "code": e.code}
        body.update(e.extra)
        return jsonify(body), e.status

    @app.errorhandler(IntegrityError)
    def _integrity(e):
        log.warning("Integrity error: %s", e)
        return jsonify({"error": "This change conflicts with existing data.", "code": "conflict"}), 409

    @app.errorhandler(404)
    def _nf(_e):
        return jsonify({"error": "Not found.", "code": "not_found"}), 404

    @app.errorhandler(405)
    def _na(_e):
        return jsonify({"error": "Method not allowed.", "code": "method_not_allowed"}), 405

    @app.errorhandler(413)
    def _too_large(_e):
        return jsonify({"error": "The uploaded file is too large.", "code": "too_large"}), 413

    @app.errorhandler(Exception)
    def _unhandled(e):
        from werkzeug.exceptions import HTTPException
        if isinstance(e, HTTPException):
            return jsonify({"error": e.description or e.name, "code": e.name.lower().replace(" ", "_")}), e.code
        log.error("Unhandled error: %s\n%s", e, traceback.format_exc())
        return jsonify({"error": "Something went wrong on the server. Please try again.",
                        "code": "server_error"}), 500
