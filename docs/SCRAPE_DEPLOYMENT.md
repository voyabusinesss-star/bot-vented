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

Divise la conso par ~2–3 ; annonces un peu moins fraîches.  
Pendant la phase **OFF**, le worker **ferme Chromium** (`browser_closed=True` dans les logs) — pas de keep-alive proxy.

```env
SCRAPE_BURST_ON_SECONDS=300
SCRAPE_BURST_OFF_SECONDS=600
SCRAPE_BLOCK_HEAVY_RESOURCES=true
```

## Est-ce que 10 Go tient la route ? (calcul rapide)

Le bot appelle surtout **`/api/v2/catalog/items`** (~50–150 Ko JSON/requête via proxy).  
Le warm-up (`vinted.fr` une fois par session) est le seul vrai chargement HTML — d’où `page.route()` pour bloquer images/fonts/CSS/analytics.

**Formule :**

```text
Go/mois ≈ (requêtes_catalogue/jour × Ko moyen × 30) / 1_000_000
         + warm-ups/jour × ~0,3–1 Mo (avec blocage assets)
         + filtres_privés/jour × ~100 Ko
```

Hypothèses : **~40 cibles** actives, poll **5 min** par marque, rafales **8 h/jour** (5 min ON / 10 min OFF) :

| Poste | Calcul | / jour | / mois (×30) |
|-------|--------|--------|--------------|
| Catalogue API | 40 × (3600/300) × 8 h × 100 Ko | ~384 Mo | **~11,5 Go** |
| Idem avec poll **3 min** | 40 × 20/h × 8 h × 100 Ko | ~640 Mo | **~19 Go** ❌ |
| Poll 5 min + **75 Ko** moyen | 3840 × 75 Ko | ~288 Mo | **~8,6 Go** ✅ |
| Warm-ups (~96 restarts/j) | 96 × 0,5 Mo | ~48 Mo | ~1,4 Go |
| Filtres privés (45 s) | 8×3600/45 × 100 Ko | ~64 Mo | ~1,9 Go |

**Verdict 10 Go/mo :**

| Config | Tient ? |
|--------|---------|
| Poll 5 min + rafales 8 h/j + blocage assets | **Oui, juste** (~9–10 Go) |
| Poll 3 min ou 24/7 sans rafales | **Non** → viser **25–50 Go** |
| Mac local sans proxy | **Illimité** (pas de compteur Webshare) |

Surveille Webshare dashboard la 1ère semaine ; si >70 % du quota à mi-mois, passe poll **6–8 min** ou OFF plus long.

## E — Test Mac 48 h (baseline sans proxy)

À lancer **en parallèle** de la config Railway — compare le taux de blocage sur IP box vs cloud.

```bash
cd /chemin/vers/bot-vinted
# .env local : DATABASE_URL (Postgres Railway), DISCORD_*, pas de SCRAPE_PROXY_URLS
uv run vinted-bot scrape --loop
```

**Checklist 48 h :**

| Métrique | OK | Problème |
|----------|-----|----------|
| Logs `catalog_fetch_failed` 403 | rare / 0 | fréquent → IP ou comportement |
| Logs `402` | absent | N/A sans proxy |
| `brand_worker_target_done` | régulier | silence >5 min |
| Salons Discord | annonces fraîches | vide |

Si **403 fréquents même en local** avec poll ≥2 min → ce n’est pas l’IP Railway ; revoir fréquence ou session.  
Si **local OK, Railway KO sans proxy** → proxy résidentiel nécessaire en cloud (normal).

## F — Railway direct sans proxy + auto-redeploy 403

Mode « IP datacenter Railway » sans Webshare. Le scrape repart seul après redeploy (`railway-entrypoint.sh` → `scrape --loop`).

**Variables `bot-scrape`** (profil complet : `docs/railway-scrape-channels.env`) :

```env
SCRAPE_PROXY_URLS=
SCRAPE_FAST_MODE=1
SCRAPE_POLL_SECONDS_MIN=0.3
SCRAPE_POLL_SECONDS_MAX=0.8
REQUEST_DELAY_SECONDS=0.5
SCRAPE_BURST_ON_SECONDS=0
SCRAPE_BURST_OFF_SECONDS=0
SCRAPE_AUTO_REDEPLOY_ENABLED=1
SCRAPE_403_REDEPLOY_THRESHOLD=8
SCRAPE_AUTO_REDEPLOY_COOLDOWN_SECONDS=1800
```

**Limites :** le redeploy change d’hôte Railway, pas garanti une nouvelle IP egress. Si >2–3 redeploys/h sans posts → repasser proxy résidentiel FR.

### Token Railway (auto-redeploy)

1. Railway → **Project** → **Settings** → **Tokens** → **Create Project Token** (droits deploy).
2. Récupérer `serviceId` et `environmentId` :
   - URL dashboard du service `bot-scrape`, ou
   - `railway status --json` (depuis le repo lié).
3. Coller **uniquement sur `bot-scrape`** (jamais sur `bot-vented` public) :

```env
RAILWAY_API_TOKEN=<project-token>
RAILWAY_SERVICE_ID=<uuid>
RAILWAY_ENVIRONMENT_ID=<uuid>
```

4. Désactiver **Static Outbound IPs** sur `bot-scrape`.
5. Sur Mac local : `ENABLE_SCRAPE=0` pour une seule source de posts.

**Observabilité :** heartbeat `consecutive_403`, `consecutive_thread_limit`, logs `chromium_health` (toutes les 5 min), `scrape_thread_limit_threshold_reached`, `railway_redeploy_triggered`, alerte Discord `#logs`.

**Thread limit Playwright :** si `can't start new thread` persiste (≥3 en 10 min), auto-redeploy avec log du nombre de processus Chromium au crash. Cooldown partagé avec les 403 (défaut 15 min).

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
