"""Resolve authorization context at every store data-access boundary.

The local desktop/CLI is a trusted single-store client, fixed to legacy store 1.
Web callers MUST obtain a store through verified membership or the demo loader.
"""

from flask import current_app, g, has_request_context

LEGACY_STORE_ID = 1


def current_store_id():
    if has_request_context():
        store = getattr(g, "store", None)
        if store is None:
            raise PermissionError("An authorized store context is required.")
        return store["id"]
    return LEGACY_STORE_ID


def require_store_write():
    store_id = current_store_id()
    if has_request_context() and (getattr(g, "is_demo", False) or current_app.config.get("DEMO_READ_ONLY", False)):
        raise PermissionError("The public demonstration is read-only.")
    return store_id
