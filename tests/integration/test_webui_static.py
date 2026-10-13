from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from verter.core.config import ConfigError
from verter.dashboard import app as app_module
from verter.dashboard.app import ENV_WEBUI_DIST, create_app, resolve_webui_dist
from verter.dashboard.context import DashboardContext

INDEX = "<!doctype html><title>Verter</title>" + "<!-- padding -->" * 200


@pytest.fixture
def dist(tmp_path: Path) -> Path:
    root = tmp_path / "dist"
    (root / "assets").mkdir(parents=True)
    (root / "index.html").write_text(INDEX, encoding="utf-8")
    (root / "assets" / "app-3f9a.js").write_text("console.log(1)", encoding="utf-8")
    (root / "theme-init.js").write_text("void 0", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("top secret", encoding="utf-8")
    return root


@pytest.fixture
async def web(ctx: DashboardContext, dist: Path) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=create_app(ctx, webui_dist=dist))
    async with AsyncClient(transport=transport, base_url="http://testserver") as http:
        yield http


@pytest.mark.parametrize("path", ["/", "/login", "/settings/deep/link"])
async def test_client_routes_serve_index(web: AsyncClient, path: str) -> None:
    response = await web.get(path)
    assert response.status_code == 200
    assert response.text == INDEX
    assert response.headers["cache-control"] == "no-cache"


async def test_index_is_gzip_compressed(web: AsyncClient) -> None:
    response = await web.get("/", headers={"accept-encoding": "gzip"})
    assert response.headers["content-encoding"] == "gzip"


async def test_hashed_assets_are_cached_forever(web: AsyncClient) -> None:
    response = await web.get("/assets/app-3f9a.js")
    assert response.text == "console.log(1)"
    assert response.headers["cache-control"] == "public, max-age=31536000, immutable"
    other = await web.get("/theme-init.js")
    assert other.headers["cache-control"] == "no-cache"


@pytest.mark.parametrize("path", ["/%2e%2e/secret.txt", "/assets/%2e%2e/%2e%2e/secret.txt"])
async def test_paths_outside_dist_are_not_served(web: AsyncClient, path: str) -> None:
    response = await web.get(path)
    assert "top secret" not in response.text


async def test_unknown_api_paths_stay_json_404(web: AsyncClient) -> None:
    response = await web.get("/api/does-not-exist")
    assert response.status_code == 404
    assert response.json() == {"detail": "not_found"}


def test_resolve_webui_dist(dist: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ENV_WEBUI_DIST, raising=False)
    monkeypatch.setattr(app_module, "PACKAGED_WEBUI", tmp_path / "not-built")
    assert resolve_webui_dist() is None
    assert resolve_webui_dist(dist) == dist.resolve()
    monkeypatch.setenv(ENV_WEBUI_DIST, str(dist))
    assert resolve_webui_dist() == dist.resolve()
    with pytest.raises(ConfigError, match=r"index\.html"):
        resolve_webui_dist(tmp_path / "missing")
    monkeypatch.delenv(ENV_WEBUI_DIST)
    monkeypatch.setattr(app_module, "PACKAGED_WEBUI", dist)
    assert resolve_webui_dist() == dist
