"""Client navigateur Playwright pour Vinted."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Generator, Sequence
from urllib.parse import urlencode

from playwright.sync_api import Browser, BrowserContext, Page, Playwright, sync_playwright

from vinted_bot.utils.logging import get_logger
from vinted_bot.utils.rate_limit import RateLimiter

log = get_logger(__name__)

DEFAULT_BASE_URL = "https://www.vinted.fr"


def build_catalog_params(
    query: str,
    *,
    page: int = 1,
    per_page: int = 24,
    order: str = "newest_first",
    brand_ids: Sequence[int] | None = None,
    catalog_ids: Sequence[int] | None = None,
) -> list[tuple[str, str]]:
    """Paramètres catalog Vinted (tri newest + filtres optionnels)."""
    params: list[tuple[str, str]] = [
        ("search_text", query),
        ("page", str(page)),
        ("per_page", str(per_page)),
        ("order", order or "newest_first"),
    ]
    for brand_id in brand_ids or []:
        params.append(("brand_ids[]", str(brand_id)))
    for catalog_id in catalog_ids or []:
        params.append(("catalog[]", str(catalog_id)))
    return params


class VintedBrowser:
    def __init__(
        self,
        *,
        base_url: str = DEFAULT_BASE_URL,
        headless: bool = True,
        delay_seconds: float = 3.0,
        timeout_ms: int = 45_000,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.headless = headless
        self.timeout_ms = timeout_ms
        self.rate_limiter = RateLimiter(delay_seconds)
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None

    def start(self) -> None:
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(headless=self.headless)
        self._context = self._browser.new_context(
            locale="fr-FR",
            viewport={"width": 1280, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            ),
        )
        self._page = self._context.new_page()
        self._page.set_default_timeout(self.timeout_ms)

    def stop(self) -> None:
        try:
            if self._context is not None:
                self._context.close()
        except Exception:
            pass
        try:
            if self._browser is not None:
                self._browser.close()
        except Exception:
            pass
        try:
            if self._playwright is not None:
                self._playwright.stop()
        except Exception:
            pass
        self._page = None
        self._context = None
        self._browser = None
        self._playwright = None

    def restart(self) -> None:
        log.info("browser_restart")
        self.stop()
        self.start()
        self.warm_up()

    @property
    def page(self) -> Page:
        if self._page is None:
            raise RuntimeError("VintedBrowser non démarré — appeler start()")
        return self._page

    def warm_up(self) -> None:
        """Ouvre la homepage pour obtenir cookies / session."""
        self.rate_limiter.wait()
        log.info("browser_warmup", url=self.base_url)
        self.page.goto(self.base_url, wait_until="domcontentloaded")
        self._dismiss_cookies_if_present()

    def _dismiss_cookies_if_present(self) -> None:
        selectors = [
            "button:has-text('Tout accepter')",
            "button:has-text('Accept all')",
            "[id*='onetrust-accept']",
            "button[data-testid='cookie-policy-dialog-accept']",
        ]
        for selector in selectors:
            try:
                btn = self.page.locator(selector).first
                if btn.is_visible(timeout=1500):
                    btn.click(timeout=2000)
                    log.info("cookies_accepted", selector=selector)
                    return
            except Exception:
                continue

    def search_catalog(
        self,
        query: str,
        *,
        page: int = 1,
        per_page: int = 24,
        order: str = "newest_first",
        brand_ids: Sequence[int] | None = None,
        catalog_ids: Sequence[int] | None = None,
    ) -> dict[str, Any]:
        """
        Récupère /api/v2/catalog/items.
        Préfère un fetch API (rapide) ; fallback navigation page si besoin.
        """
        self.rate_limiter.wait()
        params = build_catalog_params(
            query,
            page=page,
            per_page=per_page,
            order=order,
            brand_ids=brand_ids,
            catalog_ids=catalog_ids,
        )
        log.info(
            "catalog_search",
            query=query,
            page=page,
            order=order,
            brand_ids=list(brand_ids or []),
            catalog_ids=list(catalog_ids or []),
        )

        payload = self._fetch_catalog_via_page(params)
        if payload is not None:
            return payload

        # Fallback : navigation catalog (plus lent)
        search_url = f"{self.base_url}/catalog?{urlencode(params)}"
        log.warning("catalog_fetch_fallback_navigate", url=search_url)
        catalog_payload: dict[str, Any] | None = None

        def _on_response(response: Any) -> None:
            nonlocal catalog_payload
            if "/api/v2/catalog/items" not in response.url:
                return
            if response.status != 200:
                return
            try:
                data = response.json()
            except Exception:
                return
            if isinstance(data, dict) and (
                "items" in data or "catalog_items" in data
            ):
                catalog_payload = data

        self.page.on("response", _on_response)
        try:
            self.page.goto(search_url, wait_until="domcontentloaded")
            self.page.wait_for_timeout(2000)
            if catalog_payload is None:
                catalog_payload = self._fetch_catalog_via_page(params)
        finally:
            self.page.remove_listener("response", _on_response)

        if catalog_payload is None:
            raise RuntimeError(
                "Impossible de récupérer /api/v2/catalog/items "
                "(page bloquée ou structure changée)"
            )
        return catalog_payload

    def _fetch_catalog_via_page(
        self, params: list[tuple[str, str]]
    ) -> dict[str, Any] | None:
        api_url = f"{self.base_url}/api/v2/catalog/items?{urlencode(params)}"
        result = self.page.evaluate(
            """async (url) => {
                const res = await fetch(url, {
                    headers: { 'Accept': 'application/json' },
                    credentials: 'include'
                });
                if (!res.ok) {
                    return { __error: res.status, __body: await res.text() };
                }
                return await res.json();
            }""",
            api_url,
        )
        if not isinstance(result, dict):
            return None
        if result.get("__error"):
            log.warning(
                "catalog_fetch_failed",
                status=result.get("__error"),
                body=str(result.get("__body", ""))[:300],
            )
            return None
        if "items" not in result and "catalog_items" not in result:
            return None
        return result


@contextmanager
def vinted_browser(
    *,
    base_url: str = DEFAULT_BASE_URL,
    headless: bool = True,
    delay_seconds: float = 3.0,
) -> Generator[VintedBrowser, None, None]:
    client = VintedBrowser(
        base_url=base_url,
        headless=headless,
        delay_seconds=delay_seconds,
    )
    client.start()
    try:
        yield client
    finally:
        client.stop()
