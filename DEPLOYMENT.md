# Deploying to Railway

A step-by-step checklist, from a fresh cTrader account to a running worker.

Work through it in order — steps 1–3 produce the credentials that step 5 needs.

> **Deploy against a demo account first.** `CTRADER_HOST_TYPE=demo` and
> `ENABLE_TRADING=false` are the defaults for a reason: the bot connects,
> analyses and prints its reasoning without sending a single order. Watch that
> output for a few sessions before anything touches real money.

---

## 1. Register an Open API application

1. Go to <https://openapi.ctrader.com/> and sign in with your cTrader ID.
2. **Applications → Add new application.**
3. Once it is approved, open it and copy:
   - **Client ID** → `CTRADER_APP_ID`
   - **Client Secret** → `CTRADER_APP_SECRET`
4. Under **Redirect URIs**, add one you control. For a bot with no web server,
   `http://localhost/` is fine — you only need to read the code out of the
   address bar in the next step.

## 2. Get an access token

The Open API uses OAuth2. You do this once, by hand.

1. Open this URL in a browser, substituting your values:

   ```
   https://openapi.ctrader.com/apps/auth?client_id=YOUR_APP_ID&redirect_uri=http://localhost/&scope=trading
   ```

   `scope=trading` is required — a `accounts`-only token can read but never
   place an order.

2. Approve the app and pick the trading account. You land on your redirect URI
   with `?code=...` in the address bar. Copy that code.

3. Exchange it for a token:

   ```bash
   curl "https://openapi.ctrader.com/apps/token\
?grant_type=authorization_code\
&code=THE_CODE\
&redirect_uri=http://localhost/\
&client_id=YOUR_APP_ID\
&client_secret=YOUR_APP_SECRET"
   ```

4. From the JSON response, keep:
   - `access_token` → `CTRADER_ACCESS_TOKEN`
   - `refresh_token` → `CTRADER_REFRESH_TOKEN` *(optional but recommended —
     the bot uses it to renew the access token automatically when it expires,
     which otherwise silently kills the session after a few weeks)*

## 3. Find your `ctidTraderAccountId`

This is **not** your cTrader login number. It is an internal integer, and it is
the one credential you cannot read off the UI. The repo ships a helper:

```bash
git clone https://github.com/sedijohn135-glitch/AO-SND-SNR-QM.git
cd AO-SND-SNR-QM
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

export CTRADER_APP_ID=...
export CTRADER_APP_SECRET=...
export CTRADER_ACCESS_TOKEN=...
export CTRADER_HOST_TYPE=demo          # or live

python scripts/list_accounts.py
```

```
2 account(s) reachable with this token:

  CTRADER_ACCOUNT_ID     TYPE   LOGIN
  12345678               DEMO   3012345
  87654321               LIVE   4098765

Copy the id matching your CTRADER_HOST_TYPE into CTRADER_ACCOUNT_ID.
```

Take the id whose **TYPE matches the `CTRADER_HOST_TYPE` you intend to run**.
A demo id against `live` (or vice versa) fails authentication.

## 4. Create the Railway service

1. **New Project → Deploy from GitHub repo →** select this repository.
2. Railway's Nixpacks builder reads `runtime.txt` (Python 3.11) and installs
   `requirements.txt` automatically. No Dockerfile needed.
3. This is a **worker, not a web service** — it listens on no port. Do **not**
   add a domain or a healthcheck; Railway may otherwise mark it unhealthy for
   failing to bind a port. The start command comes from `Procfile` /
   `railway.json`: `python -u main.py`.
4. Restart policy is already set to `ON_FAILURE` with 10 retries.

## 5. Set the environment variables

**Variables → Raw editor** and paste. Only the first four are required;
everything else has a working default and is listed in section 6.

```
CTRADER_APP_ID=your_client_id
CTRADER_APP_SECRET=your_client_secret
CTRADER_ACCESS_TOKEN=your_access_token
CTRADER_ACCOUNT_ID=12345678

CTRADER_REFRESH_TOKEN=your_refresh_token
CTRADER_HOST_TYPE=demo
ENABLE_TRADING=false
LOG_LEVEL=INFO
```

## 6. Every variable, in full

35 variables are read by `bot/config.py`. Defaults are what the code uses when
the variable is absent, so a minimal deployment sets only the four required
ones.

### Required — the bot will not start without these

| Variable | Description |
| --- | --- |
| `CTRADER_APP_ID` | Open API Client ID (step 1) |
| `CTRADER_APP_SECRET` | Open API Client Secret (step 1) |
| `CTRADER_ACCESS_TOKEN` | OAuth2 token, `trading` scope (step 2) |
| `CTRADER_ACCOUNT_ID` | `ctidTraderAccountId`, an integer (step 3) |

### Connection

| Variable | Default | Description |
| --- | --- | --- |
| `CTRADER_REFRESH_TOKEN` | *(unset)* | Enables automatic token renewal. Strongly recommended. |
| `CTRADER_HOST_TYPE` | `demo` | `demo` → demo.ctraderapi.com, `live` → live.ctraderapi.com |

### Safety — the switch that matters

| Variable | Default | Description |
| --- | --- | --- |
| `ENABLE_TRADING` | `false` | **Leave false until you trust the reports.** False = connect, analyse, log, send nothing. |

### Instruments and session

| Variable | Default | Description |
| --- | --- | --- |
| `SYMBOL_WEEKDAY` | `XAUUSD` | Traded whenever its market is open |
| `SYMBOL_WEEKEND` | `BTCUSD` | Traded only while the above is shut |
| `GOLD_SESSION_TIMEZONE` | `America/New_York` | Market zone for the session window |
| `GOLD_SESSION_OPEN` | `SUN 18:00` | Session open, in that zone (22:00 UTC summer / 23:00 winter) |
| `GOLD_SESSION_CLOSE` | `FRI 17:00` | Session close, in that zone |
| `BOT_TIMEZONE` | `UTC` | Zone used for logging timestamps |

The broker's own trading schedule overrides this window when it publishes one;
the window is the fallback.

### Risk

| Variable | Default | Description |
| --- | --- | --- |
| `RISK_PERCENT` | `2.5` | Percent of balance risked per trade, via the stop distance |
| `FIXED_VOLUME_LOTS` | `0` | `>0` bypasses percentage sizing entirely |
| `MAX_OPEN_POSITIONS` | `1` | Per instrument |
| `MAX_PENDING_ORDERS` | `1` | Per instrument |
| `ORDER_EXPIRY_MINUTES` | `240` | Pending order lifetime; `0` = good till cancel |
| `CANCEL_ORPHANED_ORDERS_ON_BOOT` | `true` | Cancel resting orders on startup; see below |

Sizing converts the risk budget from the account's deposit currency into the
instrument's quote currency at the broker's live rate, so a EUR account trading
USD-quoted XAUUSD and BTCUSD is sized correctly.

The bot tracks the Quasimodo behind each pending order in memory only. A
restart — every Railway deploy — empties that, so it can neither invalidate a
resting order when price closes beyond the head nor replace it, and with
`MAX_PENDING_ORDERS=1` that order blocks every new setup until it expires.
`CANCEL_ORPHANED_ORDERS_ON_BOOT` clears them at startup instead; a setup that
is still valid is re-placed on the next tick. Only `SYMBOL_WEEKDAY` and
`SYMBOL_WEEKEND` are touched. Set it to `false` if you place orders on those
instruments by hand and want them left alone.

### Strategy

| Variable | Default | Description |
| --- | --- | --- |
| `REQUIRE_H4_ZONE_PROXIMITY` | `true` | HAPI 1: price must be at the H4 zone the bias wants |
| `ALLOW_COUNTER_TREND` | `false` | Allow scalps against the H4 bias (see below) |
| `H4_ZONE_PROXIMITY_ATR` | `1.5` | How near "at the zone" means, in H4 ATRs |
| `LOOP_INTERVAL_SECONDS` | `60` | Seconds between analysis passes |
| `BARS_H4` | `400` | H4 candles fetched |
| `BARS_M15` | `500` | M15 candles fetched |
| `BARS_M5` | `500` | M5 candles fetched |

### Counter-trend scalps

HAPI 1 normally permits only trades aligned with the H4 bias. With
`ALLOW_COUNTER_TREND=true` the bot also looks for the opposite direction — a
SELL under a bullish H4, or a BUY under a bearish one.

Two safeguards remain in place:

- **AO divergence becomes mandatory.** Trading with the trend, divergence is a
  warning only; trading against it, the divergence is the entire justification,
  so a counter-trend setup without it is rejected.
- **A ranging H4 still blocks both directions.** Counter-trend trading needs a
  trend to trade against.

The trend-aligned direction is always evaluated first; the counter-trend pass
only runs if it produced nothing. Such trades are marked `(Counter-Trend)` in
the Telegram alert and `[COUNTER-TREND PASS]` in the report.

### News filter

| Variable | Default | Description |
| --- | --- | --- |
| `NEWS_FILTER_ENABLED` | `true` | Master switch |
| `NEWS_FEED_URL` | Forex Factory weekly JSON | Calendar source |
| `NEWS_CURRENCIES` | `USD` | Comma-separated codes to watch |
| `NEWS_MIN_IMPACT` | `High` | `High` / `Medium` / `Low` |
| `NEWS_BLACKOUT_BEFORE_MINUTES` | `30` | Blackout starts this long before a release |
| `NEWS_BLACKOUT_AFTER_MINUTES` | `30` | Blackout ends this long after |
| `NEWS_REFRESH_MINUTES` | `60` | Refresh interval; floor of 5 (the feed allows 2 downloads per 5 min) |
| `NEWS_CACHE_MAX_AGE_HOURS` | `24` | Past this, a stale cache fails closed |
| `NEWS_REQUEST_TIMEOUT` | `15` | Seconds |
| `NEWS_BLOCK_ALL_DAY_EVENTS` | `false` | Block on undated all-day entries |

### Telegram notifications (optional)

Leave the token or the chat id blank and notifications are simply off — the bot
logs `Telegram notifications: inactive (missing ...)` and trades normally.

| Variable | Default | Description |
| --- | --- | --- |
| `TELEGRAM_BOT_TOKEN` | *(unset)* | From [@BotFather](https://t.me/BotFather) |
| `TELEGRAM_CHAT_ID` | *(unset)* | Target chat; negative for groups |
| `TELEGRAM_ENABLED` | `true` | Silence notifications without removing credentials |
| `TELEGRAM_TIMEOUT` | `10` | Seconds per request |

**Getting the two values:**

1. Message [@BotFather](https://t.me/BotFather) → `/newbot` → copy the token
   into `TELEGRAM_BOT_TOKEN`.
2. Send your new bot any message (a bot cannot start a conversation with you),
   then open:

   ```
   https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates
   ```

   Read `result[0].message.chat.id` — that is `TELEGRAM_CHAT_ID`. For a group,
   add the bot to it, post a message there, and use the group's negative id.

You should get a **BOT STARTED** message within seconds of the next deploy. If
nothing arrives, check the logs for `Telegram send failed` — the token itself
is never logged, so the endpoint appears redacted as `bot***`.

### Logging

| Variable | Default | Description |
| --- | --- | --- |
| `LOG_LEVEL` | `INFO` | `DEBUG` for per-tick detail |

---

## 7. Verify the first run

Open the Railway **Deploy logs**. A healthy start looks like:

```
INFO  bot: Starting bot with config: {'host': 'demo.ctraderapi.com', ...}
WARNING bot: ENABLE_TRADING=false -- analysis only, no orders will be sent
INFO  bot.ctrader.client: Connecting to demo.ctraderapi.com:5035 ...
INFO  bot.ctrader.client: Socket connected
INFO  bot.ctrader.client: Application authenticated
INFO  bot.ctrader.client: Account 12345678 authenticated
INFO  bot.ctrader.symbols: Loaded 1247 tradable symbols from broker
INFO  bot.ctrader.symbols: Resolved XAUUSD -> id=41 digits=2 ...
INFO  bot.news: News calendar refreshed: 312 event(s), 8 match USD/High
INFO  bot.scheduler: Instrument selected: XAUUSD | ...
```

Then the five-point report, once per `LOOP_INTERVAL_SECONDS`.

**Check these four things specifically:**

1. **`Account ... authenticated`** — if this is missing, the credentials or the
   demo/live mismatch are wrong.
2. **`Resolved XAUUSD -> id=...`** — confirms your broker's symbol naming was
   matched. A `matched loosely` warning here is worth reading.
3. **`News calendar refreshed: N event(s)`** — the one thing never verified
   before deployment, because the build environment could not reach the feed.
   If you instead see `News calendar fetch failed`, see below.
4. **The report's `1. H4 TREND` line** — proves real candles came back.

If Telegram is configured you should also see `Telegram notifications: active`
and receive a **BOT STARTED** message.

## 8. Going live

Only after the reports have looked right for several sessions:

1. Set `ENABLE_TRADING=true`, keeping `CTRADER_HOST_TYPE=demo`. Confirm orders
   actually appear in the demo account with the stop and target you expect.
2. Only then switch `CTRADER_HOST_TYPE=live` **and** replace
   `CTRADER_ACCESS_TOKEN` / `CTRADER_ACCOUNT_ID` with the live pair from
   step 3. A demo token will not authenticate against the live host.
3. Start with a smaller `RISK_PERCENT` than you intend to end on.

---

## Troubleshooting

| Symptom | Cause and fix |
| --- | --- |
| `Configuration error: Missing required environment variable ...` | The named variable is unset in Railway. |
| Connects, then `CH_CTID_TRADER_ACCOUNT_NOT_FOUND` or similar | `CTRADER_ACCOUNT_ID` does not belong to this token, or demo/live mismatch. Re-run `scripts/list_accounts.py`. |
| `Symbol 'XAUUSD' is not available on this account` | Your broker names it differently. The log prints known examples; set `SYMBOL_WEEKDAY` to match. |
| `News calendar fetch failed` repeatedly | Outbound HTTPS blocked, feed down, or the schema changed. The bot **fails closed** and blocks entries once the cache passes 24h. `NEWS_FILTER_ENABLED=false` trades through an outage. |
| `TRADING BLOCKED - MACROECONOMIC NEWS FILTER` | Working as intended: within 30 min of a high-impact USD release, or no usable calendar. |
| Valid setups never appear | Often `REQUIRE_H4_ZONE_PROXIMITY=true` with no H4 zone nearby. Set it `false` to trade on H4 trend alone. |
| `sizes below the broker minimum` | Balance too small for `RISK_PERCENT` over that stop distance. Use `FIXED_VOLUME_LOTS` or fund the account. |
| Railway marks the service unhealthy | A healthcheck or domain was added. This is a worker and binds no port — remove them. |
| `module 'lib' has no attribute 'GEN_EMAIL'` | Something upgraded `pyOpenSSL` past the pin. Redeploy from a clean build; do not loosen the pins in `requirements.txt`. |

## What is not verified

Two things could not be exercised in the build environment and are worth
watching on the first real run:

- **Live cTrader authentication.** Outbound TCP on port 5035 was blocked, so
  the connection and auth path has never run against a real broker.
- **The news calendar schema.** `nfs.faireconomy.media` was unreachable, so the
  parser was written against documentation rather than a live response. The
  failure mode is safe — it fails closed rather than letting trades through —
  but a schema change would show up as parse warnings in the logs.
