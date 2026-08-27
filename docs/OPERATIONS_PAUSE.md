# Pause / reprise des workers Railway

Mettre en pause le scrape et les workers Playwright **sans supprimer** services, variables ni Postgres.

## Ce qui reste actif (recommandé)

| Service | Garder | Rôle |
|---------|--------|------|
| `bot-vented` | oui | Discord slash, Whop webhook, panels, `/health` |
| `Postgres` | oui | DB partagée |

## Ce qu’on coupe (Playwright / Vinted)

| Service | Variable | Valeur pause |
|---------|----------|--------------|
| `bot-scrape` | `ENABLE_SCRAPE` | `0` |
| `bot-detector` (`APP_ROLE=niches`) | `ENABLE_NICHES` ou `ENABLE_DETECTOR` | `0` |

Le container reste **Online** (boucle idle + log toutes les 5 min) — pas besoin de supprimer le service.

## Pause (Railway dashboard ou CLI)

```bash
railway variables --service bot-scrape --set "ENABLE_SCRAPE=0"
railway service redeploy --service bot-scrape

railway variables --service bot-detector --set "ENABLE_NICHES=0"
railway service redeploy --service bot-detector
```

(`ENABLE_DETECTOR=0` suffit aussi pour `APP_ROLE=niches`.)

## Reprise

```bash
railway variables --service bot-scrape --set "ENABLE_SCRAPE=1"
railway service redeploy --service bot-scrape

railway variables --service bot-detector --set "ENABLE_NICHES=1"
railway service redeploy --service bot-detector
```

Aucune migration ni changement de config scrape nécessaire : les vars `SCRAPE_*`, channels Discord, etc. restent en place.

## Test local ponctuel

Le scrape local n’impacte pas Railway si `ENABLE_SCRAPE=0` sur `bot-scrape` :

```bash
uv run vinted-bot scrape --once --brand nike   # test unitaire
uv run vinted-bot scrape --loop                # test local complet
```

## Vérifier la pause

Logs attendus :

```text
[railway] scrape en pause (ENABLE_SCRAPE=0) — relancer: ENABLE_SCRAPE=1 puis redeploy
[railway] scrape paused — 2026-08-27T08:30:00Z
```

Plus de lignes `brand_scrape_start` / `catalog_search` sur le service concerné.
