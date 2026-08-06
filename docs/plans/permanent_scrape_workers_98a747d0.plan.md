---
name: Permanent scrape workers
overview: Remplacer le cycle scrape éphémère par un pool de workers Playwright permanents (24/7), chacun propriétaire d’un groupe de marques, qui poll en boucle 2–5s, déduplique via la DB, poste Discord immédiatement sur nouvel ID, avec rotation/sticky proxy configurable.
todos:
  - id: config-proxy-poll
    content: "Config: poll 2-5s + SCRAPE_PROXY_URLS"
    status: completed
  - id: browser-proxy
    content: "Playwright/VintedBrowser: proxy sticky + rotate on restart"
    status: completed
  - id: scrape-workers
    content: "Nouveau scrape_workers.py: pool permanent sticky"
    status: completed
  - id: scheduler-wire
    content: scheduler.run_scrape_loop → permanent pool
    status: completed
  - id: docs-env
    content: Documenter vars Railway + test partition/proxy
    status: completed
isProject: false
---

# Workers scrape permanents 24/7

## Objectif

Passer d’un cycle « lance N navigateurs → scrape tout → sleep » à un **pool permanent** :

```mermaid
flowchart LR
  subgraph railway [Railway scrape --loop]
    S[Supervisor]
    W0[Worker0 sticky brands + proxy0]
    W1[Worker1 sticky brands + proxy1]
    Wn[WorkerN sticky brands + proxyN]
    F[FilterWorker private DM]
  end
  S --> W0
  S --> W1
  S --> Wn
  S --> F
  W0 --> Vinted[Vinted catalog]
  W1 --> Vinted
  Wn --> Vinted
  W0 --> DB[(Postgres vinted_id + discord_posted_at)]
  W0 --> Discord[Discord salons]
```

## Décisions (defaults)

- **Dédup** : garder la source de vérité actuelle — `listings.vinted_id` + `discord_posted_at` + bootstrap checkpoint ([`scrape_search.py`](src/vinted_bot/services/scrape_search.py)). Cache mémoire LRU optionnel par worker pour éviter du bruit, mais upsert DB conservé.
- **Post Discord** : dès qu’un **nouvel** `vinted_id` passe le **filtre deal** (critères revente), post immédiat — on ne retire pas le deal filter (sinon spam salons).
- **Proxies** : support env `SCRAPE_PROXY_URLS` (liste CSV). **1 proxy sticky par worker** (plus stable que rotate mid-session). Si vide → connexion directe. Rotation = redémarrage navigateur / réassignation proxy périodique.
- **Intervalle** : `SCRAPE_POLL_SECONDS_MIN=2`, `SCRAPE_POLL_SECONDS_MAX=5` entre deux recherches **du même worker**. Avec beaucoup de cibles, le délai effectif par marque = `(n_targets_worker × sleep)`.

## Implémentation

### 1. Config — [`src/vinted_bot/config.py`](src/vinted_bot/config.py)

Ajouter :
- `scrape_parallel_workers` (déjà là, défaut 6)
- `scrape_poll_seconds_min` / `scrape_poll_seconds_max` (2 / 5)
- `scrape_proxy_urls: list[str]` depuis `SCRAPE_PROXY_URLS` (séparateur `,` ou `\n`)
- Parser/normaliser les URLs proxy Playwright (`http://user:pass@host:port`)

Documenter dans [`.env.example`](.env.example) et [`docs/railway-scrape-channels.env`](docs/railway-scrape-channels.env).

### 2. Proxy Playwright — [`playwright_browser.py`](src/vinted_bot/clients/playwright_browser.py) + [`vinted_browser.py`](src/vinted_bot/clients/vinted_browser.py)

- `launch_vinted_browser(..., proxy: dict | None = None)`
- `VintedBrowser(__init__ proxy_server=...)` → `new_context(proxy=...)` (Playwright)
- Sur `restart()`, option de changer de proxy (rotation)

### 3. Nouveau module workers — `src/vinted_bot/jobs/scrape_workers.py`

- `partition_targets(targets, n_workers) -> list[list[SearchTarget]]` : partition **sticky** (pas round-robin à chaque cycle) ; high-priority marques réparties en premier.
- `BrandWorker` (thread daemon) :
  - possède 1 `VintedBrowser` long-lived (+ proxy assigné)
  - boucle : pour chaque cible du groupe → `scrape_search_once(..., browser=self.browser)` → sleep `uniform(min, max)`
  - recycle navigateur tous les `browser_restart_every_cycles` succès ou sur erreur
  - heartbeat : `write_scrape_heartbeat(worker_id=..., cycle=..., posted=..., brands=...)`
- `FilterWorker` : même idée pour filtres privés (intervalle existant ou poll min/max)
- `run_permanent_scrape_pool()` : démarre N brand workers + 1 filter worker, join forever, restart thread mort

Réutiliser intact `scrape_search_once` (bootstrap, deal filter, Discord immédiat sur insert).

### 4. Scheduler — [`src/vinted_bot/jobs/scheduler.py`](src/vinted_bot/jobs/scheduler.py)

Remplacer `run_scrape_loop` pour appeler `run_permanent_scrape_pool()` (garder le nom CLI `scrape --loop`).

Supprimer le pattern « recreate browsers chaque cycle via `scrape_all_configured(workers=N)` » pour le path loop (garder `scrape_all_configured` pour CLI `--all --once`).

### 5. Railway

Aucun changement entrypoint sauf variables documentées. Même process `scrape --loop`.

RAM : 6 Chromium sticky ≈ lourd — défaut 6 ; documenter de baisser à 4 si OOM.

## Fichiers touchés

- [`src/vinted_bot/config.py`](src/vinted_bot/config.py)
- [`src/vinted_bot/clients/playwright_browser.py`](src/vinted_bot/clients/playwright_browser.py)
- [`src/vinted_bot/clients/vinted_browser.py`](src/vinted_bot/clients/vinted_browser.py)
- **new** `src/vinted_bot/jobs/scrape_workers.py`
- [`src/vinted_bot/jobs/scheduler.py`](src/vinted_bot/jobs/scheduler.py)
- [`.env.example`](.env.example), [`docs/railway-scrape-channels.env`](docs/railway-scrape-channels.env)
- Test unitaire léger : partition sticky + parse proxy URLs

## Hors scope

- Achat de proxies (tu colles les URLs dans Railway)
- Changement du filtre deal / critères prix
- API Vinted httpx (reste Playwright)

## Statut implémentation (2026-08-06)

- `src/vinted_bot/jobs/scrape_workers.py` + `run_permanent_scrape_pool()`
- `scheduler.py` → pool permanent pour `scrape --loop`
- `tests/test_scrape_workers.py` (partition)
- Railway : service `bot-scrape` (`APP_ROLE=scrape`), poll 0.3–0.8s, 2 workers (RAM)
- Multi-services : `docs/RAILWAY_SERVICES.md`
