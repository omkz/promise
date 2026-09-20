from __future__ import annotations

from functools import lru_cache

from fastapi import Depends, Header
from promise_app.bootstrap import AppContext, build_context


@lru_cache
def _default_context() -> AppContext:
    return build_context()


def get_context() -> AppContext:
    """One AppContext per process by default. Routed through FastAPI's DI (not
    called directly) so tests can swap it via `app.dependency_overrides`."""
    return _default_context()


def workspace_id(x_workspace_id: str | None = Header(default=None), ctx: AppContext = Depends(get_context)) -> str:
    """Resolves the caller's workspace. A real deployment derives this from an
    authenticated session/JWT; local dev and the demo channel fall back to the
    seeded default workspace so a single dev environment works out of the box.
    """
    return x_workspace_id or ctx.default_workspace_id


def user_id(x_user_id: str | None = Header(default=None), ctx: AppContext = Depends(get_context)) -> str:
    return x_user_id or ctx.default_user_id
