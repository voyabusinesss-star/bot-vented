"""Client navigateur Playwright pour Vinted."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Generator
from urllib.parse import quote_plus, urlencode

from playwright.sync_api import Browser, Page, Playwright, Response, sync_playwright

from vinted_bot.utils.logging import get_logger
from vinted_bot.utils.rate_limit import RateLimiter

log = get_logger(__name__)

DEFAULT_BASE_URL = "https://www.vinted.fr"


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
        self._page: Page | None = None

    def start(self) -> None:
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(headless=self.headless)
        context = self._browser.new_context(
            locale="fr-FR",
            viewport={"width": 1280, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            ),
        )
        self._page = context.new_page()
        self._page.set_default_timeout(self.timeout_ms)

    def stop(self) -> None:
        if self._browser is not None:
            self._browser.close()
        if self._playwright is not None:
            self._playwright.stop()
        self._browser = None
        self._playwright = None
        self._page = None

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

    def search_catalog(self, query: str, *, page: int = 1, per_page: int = 24) -> dict[str, Any]:
        """
        Charge une page catalog et capture la réponse JSON /api/v2/catalog/items.
        """
        params = {
            "search_text": query,
            "page": str(page),
            "per_page": str(per_page),
            "order": "newest_first",
        }
        search_url = f"{self.base_url}/catalog?{urlencode(params)}"
        self.rate_limiter.wait()
        log.info("search_navigate", query=query, page=page, url=search_url)

        catalog_payload: dict[str, Any] | None = None

        def _on_response(response: Response) -> None:
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
            # laisse le temps aux XHR catalog de partir
            self.page.wait_for_timeout(2500)
            if catalog_payload is None:
                # fallback : tenter un fetch depuis le contexte page (cookies déjà là)
                catalog_payload = self._fetch_catalog_via_page(
                    query=query, page=page, per_page=per_page
                )
        finally:
            self.page.remove_listener("response", _on_response)

        if catalog_payload is None:
            raise RuntimeError(
                "Impossible de récupérer /api/v2/catalog/items "
                "(page bloquée ou structure changée)"
            )
        return catalog_payload

    def _fetch_catalog_via_page(
        self, *, query: str, page: int, per_page: int
    ) -> dict[str, Any] | None:
        api_url = (
            f"{self.base_url}/api/v2/catalog/items?"
            f"search_text={quote_plus(query)}&page={page}"
            f"&per_page={per_page}&order=newest_first"
        )
        log.info("catalog_fetch_fallback", url=api_url)
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
