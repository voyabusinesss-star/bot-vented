# Scrape Vinted — où tourner et combien ça coûte

Le scrape consomme surtout de la **bande passante proxy** (Playwright + ~40 marques + filtres privés).
Avec 1 Go Webshare en mode agressif, le quota part en ~30 min — ce n’est pas un bug.

## Options (par coût / tenue)

| Option | Coût | 24/7 ? | Notes |
|--------|------|--------|-------|
| **A — Mac local** | 0 € proxy | Oui si Mac allumé | IP box FR, pas de Webshare |
| **B — Railway optimisé** | 0 € (1 Go) | Non (~3–8 h/jour) | Poll lent, pas de fallback navigateur |
| **C — Rafales cron** | 0–5 € | Quasi | `SCRAPE_BURST_ON/OFF_SECONDS` |
| **D — Webshare 10–50 Go Sticky FR** | 5–30 €/mo | Oui | Seule voie Railway simple 24/7 |

## A — Scrape local (recommandé si pas de budget proxy)

Railway garde Discord + Whop + Postgres. Le Mac scrape seulement :

```bash
cd /chemin/vers/bot-vinted
# .env : pas de SCRAPE_PROXY_URLS (IP box FR)
uv run vinted-bot scrape --loop
```

Variables minimales locales : `DATABASE_URL` (Railway Postgres public), `DISCORD_*`, `VINTED_BASE_URL`.

## B — Railway + 1 Go (mode économie)

Variables `bot-scrape` (voir `docs/railway-scrape-channels.env`) :

```env
SCRAPE_PROXY_URLS=http://USER:PASS@p.webshare.io:80
# Sticky FR (user-fr), PAS -fr-rotate
REQUEST_DELAY_SECONDS=2
SCRAPE_POLL_SECONDS_MIN=10
SCRAPE_POLL_SECONDS_MAX=15
PRIVATE_FILTER_SCRAPE_INTERVAL_SECONDS=45
BOT_PREVIEW_VIA_OUTBOX=1
DISCORD_OUTBOX_MAX_MESSAGES=8
```

**Important :** `SCRAPE_PROXY_URLS` en URL simple ou CSV — pas `["http://..."]` avec crochets.

## C — Rafales (cron-like sans cron externe)

Divise la conso par ~2–3 ; annonces un peu moins fraîches :

```env
SCRAPE_BURST_ON_SECONDS=300
SCRAPE_BURST_OFF_SECONDS=600
```

## D — 24/7 Railway pro

1. Webshare **Sticky FR** 10–50 Go/mo (endpoint `-fr`, pas `-rotate`)
2. Garder les optimisations B
3. Surveiller heartbeat : `outbox_pending`, `outbox_lag_seconds`, logs `402`

## Dépannage rapide

| Symptôme | Cause probable | Action |
|----------|----------------|--------|
| `402 Bandwidth limit` | Quota Webshare | Upgrade ou attendre reset / scrape local |
| `browser_proxy_invalid` | URL proxy mal parsée | URL simple sans `[ ]` JSON |
| Salons vides mais heartbeat OK | Backlog outbox ou 429 | Voir `outbox_pending` / `discord_outbox_lagging` |
| `can't start new thread` | Spirale Playwright | Redéployer fixes + backoff 600 s sur 402 |
