# Railway : 4 services (même image / même repo)

Architecture recommandée pour éviter les OOM Chromium :

| Service        | `APP_ROLE`  | Rôle                                      | HTTP public |
|----------------|-------------|-------------------------------------------|-------------|
| `bot-vented`   | `api`       | Discord + Whop + migrations               | oui `/health` |
| `bot-scrape`   | `scrape`    | Salons publics + filtres privés           | non |
| `bot-detector` | `detector`  | Détecteur de niches                       | non |
| `bot-fiches`   | `fiches`    | Fiches produit niches                     | non |

Tous partagent le **même Postgres** (`DATABASE_URL` → service Postgres).

## Variables minimales par service

### Commun (tous)
- `DATABASE_URL` = `${{Postgres.DATABASE_URL}}` (ou `postgresql+psycopg://…`)
- `DISCORD_BOT_TOKEN`
- salons Discord nécessaires au rôle

### `bot-vented` (api)
```
APP_ROLE=api
```
+ toutes les vars Whop / panels / salons déjà présentes.

### `bot-scrape`
```
APP_ROLE=scrape
SCRAPE_PARALLEL_WORKERS=3
SCRAPE_POLL_SECONDS_MIN=0.3
SCRAPE_POLL_SECONDS_MAX=0.8
REQUEST_DELAY_SECONDS=0.5
DISCORD_POST_DELAY_SECONDS=0
```
+ tous les `DISCORD_CHANNEL_*` marques / sneakers / ALL.

### `bot-detector`
```
APP_ROLE=detector
DISCORD_CHANNEL_NICHES=…
DISCORD_CHANNEL_NICHES_DEMO=…
DISCORD_CHANNEL_NICHES_VINTED=…
```

### `bot-fiches`
```
APP_ROLE=fiches
DISCORD_CHANNEL_FICHES_PRODUIT=…
```

## Création CLI (exemple)

```bash
railway link -p <project> -e production

railway add --service bot-scrape --repo voyabusinesss-star/bot-vented --branch main \
  --variables "APP_ROLE=scrape"

railway add --service bot-detector --repo voyabusinesss-star/bot-vented --branch main \
  --variables "APP_ROLE=detector"

railway add --service bot-fiches --repo voyabusinesss-star/bot-vented --branch main \
  --variables "APP_ROLE=fiches"
```

Puis copier / référencer les variables depuis `bot-vented` (token Discord, channels, DB).

Sur **bot-vented** uniquement : `APP_ROLE=api` (plus de scrape/detector/fiches dans le même container).
