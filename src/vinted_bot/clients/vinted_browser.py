"""Client navigateur Playwright pour Vinted.

Playwright sync_api utilise des greenlets incompatibles avec SQLAlchemy sync
dans le même thread. Toutes les ops navigateur tournent donc dans un thread
dédié ; le thread scrape peut faire de la DB sans MissingGreenlet.
"""

from __future__ import annotations

import queue
import threading
from contextlib import contextmanager
from typing import Any, Generator, Sequence
from urllib.parse import urlencode

from playwright.sync_api import Browser, BrowserContext, Page, Playwright, sync_playwright

from vinted_bot.utils.logging import get_logger
from vinted_bot.utils.rate_limit import RateLimiter

log = get_logger(__name__)

DEFAULT_BASE_URL = "https://www.vinted.fr"
_CALL_TIMEOUT_S = 55.0


def build_catalog_params(
    query: str,
    *,
    page: int = 1,
    per_page: int = 24,
    order: str = "newest_first",
    brand_ids: Sequence[int] | None = None,
    catalog_ids: Sequence[int] | None = None,
    price_from: float | None = None,
    price_to: float | None = None,
) -> list[tuple[str, str]]:
    """Paramètres catalog Vinted (tri newest + filtres optionnels)."""
    params: list[tuple[str, str]] = [
        ("page", str(page)),
        ("per_page", str(per_page)),
        ("order", order or "newest_first"),
    ]
    if query and query.strip():
        params.insert(0, ("search_text", query.strip()))
    for brand_id in brand_ids or []:
        params.append(("brand_ids[]", str(brand_id)))
    for catalog_id in catalog_ids or []:
        params.append(("catalog[]", str(catalog_id)))
    if price_from is not None:
        params.append(
            (
                "price_from",
                str(int(price_from) if float(price_from).is_integer() else price_from),
            )
        )
    if price_to is not None:
        params.append(
            (
                "price_to",
                str(int(price_to) if float(price_to).is_integer() else price_to),
            )
        )
    return params


class VintedBrowser:
    def __init__(
        self,
        *,
        base_url: str = DEFAULT_BASE_URL,
        headless: bool = True,
        delay_seconds: float = 1.0,
        timeout_ms: int = 45_000,
        proxy_url: str | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.headless = headless
        self.timeout_ms = timeout_ms
        self.proxy_url = (proxy_url or "").strip() or None
        self.rate_limiter = RateLimiter(delay_seconds)
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        self._cmd_q: queue.Queue[Any] = queue.Queue()
        self._thread: threading.Thread | None = None

    def _worker(self) -> None:
        while True:
            item = self._cmd_q.get()
            if item is None:
                break
            name, args, kwargs, reply = item
            try:
                fn = getattr(self, name)
                reply.put(("ok", fn(*args, **kwargs)))
            except Exception as exc:  # noqa: BLE001
                reply.put(("err", exc))

    def _call(self, name: str, *args: Any, **kwargs: Any) -> Any:
        if self._thread is not None and threading.current_thread() is self._thread:
            return getattr(self, name)(*args, **kwargs)
        if self._thread is None or not self._thread.is_alive():
            raise RuntimeError("VintedBrowser non démarré — appeler start()")
        reply: queue.Queue[Any] = queue.Queue(maxsize=1)
        self._cmd_q.put((name, args, kwargs, reply))
        try:
            status, payload = reply.get(timeout=_CALL_TIMEOUT_S)
        except queue.Empty as exc:
            raise TimeoutError(f"Playwright timeout ({name})") from exc
        if status == "err":
            raise payload
        return payload

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._worker,
            name="playwright-vinted",
            daemon=True,
        )
        self._thread.start()
        self._call("_start_impl")

    def stop(self) -> None:
        if self._thread is None:
            return
        try:
            if self._thread.is_alive():
                self._call("_stop_impl")
        except Exception:  # noqa: BLE001
            pass
        self._cmd_q.put(None)
        self._thread.join(timeout=30)
        self._thread = None

    def restart(self, *, proxy_url: str | None = None) -> None:
        """Recycle le navigateur. ``proxy_url`` non-None remplace le proxy sticky."""
        if proxy_url is not None:
            self.proxy_url = (proxy_url or "").strip() or None
        log.info("browser_restart", proxy=bool(self.proxy_url))
        self.stop()
        self.start()
        self.warm_up()

    def _start_impl(self) -> None:
        from vinted_bot.clients.playwright_browser import (
            apply_vinted_stealth,
            launch_vinted_browser,
        )
        from vinted_bot.utils.proxy import playwright_proxy_from_url

        proxy_dict = None
        if self.proxy_url:
            try:
                proxy_dict = playwright_proxy_from_url(self.proxy_url)
            except ValueError as exc:
                log.warning("browser_proxy_invalid", error=str(exc), url=self.proxy_url)
                proxy_dict = None

        self._playwright = sync_playwright().start()
        self._browser = launch_vinted_browser(
            self._playwright,
            headless=self.headless,
            proxy=proxy_dict,
        )
        context_kwargs: dict[str, Any] = {
            "locale": "fr-FR",
            "viewport": {"width": 1280, "height": 900},
            "user_agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            ),
        }
        # Proxy déjà passé au launch ; Playwright accepte aussi au context.
        # On le met au launch uniquement pour éviter double-config.
        self._context = self._browser.new_context(**context_kwargs)
        apply_vinted_stealth(self._context)
        self._page = self._context.new_page()
        self._page.set_default_timeout(self.timeout_ms)

    def _stop_impl(self) -> None:
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

    @property
    def page(self) -> Page:
        if self._page is None:
            raise RuntimeError("VintedBrowser non démarré — appeler start()")
        return self._page

    def warm_up(self) -> None:
        self._call("_warm_up_impl")

    def _warm_up_impl(self) -> None:
        self.rate_limiter.wait()
        log.info("browser_warmup", url=self.base_url, proxy=bool(self.proxy_url))
        # commit = plus léger que domcontentloaded (moins de crashes RAM)
        self.page.goto(self.base_url, wait_until="commit", timeout=min(self.timeout_ms, 30_000))
        try:
            self.page.wait_for_timeout(800)
        except Exception:  # noqa: BLE001
            pass
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
        price_from: float | None = None,
        price_to: float | None = None,
    ) -> dict[str, Any]:
        return self._call(
            "_search_catalog_impl",
            query,
            page=page,
            per_page=per_page,
            order=order,
            brand_ids=brand_ids,
            catalog_ids=catalog_ids,
            price_from=price_from,
            price_to=price_to,
        )

    def _search_catalog_impl(
        self,
        query: str,
        *,
        page: int = 1,
        per_page: int = 24,
        order: str = "newest_first",
        brand_ids: Sequence[int] | None = None,
        catalog_ids: Sequence[int] | None = None,
        price_from: float | None = None,
        price_to: float | None = None,
    ) -> dict[str, Any]:
        self.rate_limiter.wait()
        params = build_catalog_params(
            query,
            page=page,
            per_page=per_page,
            order=order,
            brand_ids=brand_ids,
            catalog_ids=catalog_ids,
            price_from=price_from,
            price_to=price_to,
        )
        log.info(
            "catalog_search",
            query=query,
            page=page,
            order=order,
            brand_ids=list(brand_ids or []),
            catalog_ids=list(catalog_ids or []),
            price_from=price_from,
            price_to=price_to,
        )

        payload = self._fetch_catalog_via_page(params)
        if payload is not None:
            return payload

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
            self.page.goto(search_url, wait_until="commit", timeout=min(self.timeout_ms, 45_000))
            try:
                self.page.wait_for_timeout(1500)
            except Exception:  # noqa: BLE001
                pass
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
            status = int(result.get("__error") or 0)
            body = str(result.get("__body", ""))[:300]
            log.warning(
                "catalog_fetch_failed",
                status=status,
                body=body,
            )
            if status in {429, 403} or "rate_limit" in body.lower():
                penalty = 45.0 if status == 429 else 90.0
                self.rate_limiter.penalize(penalty)
                log.warning(
                    "catalog_rate_limit_backoff",
                    status=status,
                    penalty_seconds=penalty,
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
    delay_seconds: float = 1.0,
    proxy_url: str | None = None,
) -> Generator[VintedBrowser, None, None]:
    client = VintedBrowser(
        base_url=base_url,
        headless=headless,
        delay_seconds=delay_seconds,
        proxy_url=proxy_url,
    )
    client.start()
    try:
        yield client
    finally:
        client.stop()
