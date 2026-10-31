"""GOV-07: ``audit_log`` safety net for every successful write (10 §9).

Endpoints that know the business meaning write their own row (``decision.approve``,
``parameter.update``…, via :func:`app.governance.audit.write_audit` / ``deps.audit``). This
middleware covers the rest: after a ``POST | PUT | PATCH | DELETE`` under ``/api/v1`` that
returned < 400, if no ``audit_log`` row carries this request's id it writes one generic row
``action = "http.<method>"``, ``target_kind = "route"``, ``target_id = <route template>``,
``after = {"status": <code>, "path": <path>}``. No body is logged (no PII, no keys).

Install with :func:`install_audit_middleware` **after** the ``request_context`` middleware in
``app/main.py`` (later = outer, so the response already has ``X-Request-ID``).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import anyio.to_thread
from fastapi import FastAPI, Request, Response

from app.core.logging import get_logger
from app.governance.audit import write_audit
from app.governance.roles import ROLE_RANK, Actor

log = get_logger(__name__)

WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
SKIP_PREFIXES: tuple[str, ...] = ()  # e.g. streaming endpoints, if ever needed


def _route_template(request: Request) -> str:
    route = request.scope.get("route")
    return str(getattr(route, "path", None) or request.url.path)


def _record(request: Request, response: Response) -> None:
    db = getattr(request.app.state, "db", None)
    if db is None:
        return
    rid = response.headers.get("X-Request-ID") or request.headers.get("X-Request-ID")
    uid = (request.headers.get("X-User") or "").strip() or "anonymous"
    role = (request.headers.get("X-Role") or "viewer").strip().lower()
    settings = getattr(request.app.state, "settings", None)
    with db.reader() as cur:
        if rid is not None:
            hit = cur.execute(
                "SELECT 1 FROM audit_log WHERE request_id = ? LIMIT 1", [rid]
            ).fetchone()
            if hit is not None:
                return
        actor = Actor(
            id=uid,
            role=role if role in ROLE_RANK else "viewer",  # type: ignore[arg-type]
            via="ui",
            request_id=rid,
        )
        write_audit(
            cur,
            actor,
            f"http.{request.method.lower()}",
            "route",
            _route_template(request),
            after={"status": response.status_code, "path": request.url.path},
            shop_id=getattr(settings, "shop_id", None),
        )


def install_audit_middleware(app: FastAPI, prefix: str = "/api/v1") -> None:
    @app.middleware("http")
    async def audit_writes(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        path = request.url.path
        if (
            request.method in WRITE_METHODS
            and response.status_code < 400
            and path.startswith(prefix)
            and not any(path.startswith(p) for p in SKIP_PREFIXES)
        ):
            try:
                await anyio.to_thread.run_sync(_record, request, response)
            except Exception as e:  # pragma: no cover - audit must not break the response
                log.warning("audit_middleware_failed", error=str(e), path=path)
        return response
