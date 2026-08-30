import httpx

from app.core.config import get_settings
from app.main import create_app


def _build_app(origins: str):
    import os

    os.environ["CORS_ORIGINS"] = origins
    get_settings.cache_clear()
    return create_app()


def _client(app):
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


async def test_allowed_origin_receives_header():
    app = _build_app("https://app.example.com")
    try:
        async with _client(app) as client:
            resp = await client.get(
                "/api/v1/health", headers={"Origin": "https://app.example.com"}
            )
        assert (
            resp.headers.get("access-control-allow-origin")
            == "https://app.example.com"
        )
    finally:
        get_settings.cache_clear()


async def test_disallowed_origin_receives_no_header():
    app = _build_app("https://app.example.com")
    try:
        async with _client(app) as client:
            resp = await client.get(
                "/api/v1/health", headers={"Origin": "https://evil.com"}
            )
        assert "access-control-allow-origin" not in resp.headers
    finally:
        get_settings.cache_clear()


async def test_preflight_allowed_origin():
    app = _build_app("https://app.example.com")
    try:
        async with _client(app) as client:
            resp = await client.options(
                "/api/v1/health",
                headers={
                    "Origin": "https://app.example.com",
                    "Access-Control-Request-Method": "GET",
                },
            )
        assert (
            resp.headers.get("access-control-allow-origin")
            == "https://app.example.com"
        )
    finally:
        get_settings.cache_clear()


async def test_empty_allowlist_blocks_all_origins():
    app = _build_app("")
    try:
        async with _client(app) as client:
            resp = await client.get(
                "/api/v1/health", headers={"Origin": "https://anything.com"}
            )
        assert "access-control-allow-origin" not in resp.headers
    finally:
        get_settings.cache_clear()
