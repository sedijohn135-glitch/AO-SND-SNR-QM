# AO + SND + SNR + QM — cTrader trading bot

A standalone Python trading bot that connects straight to the broker over the
**cTrader Open API** (protobuf over TLS) — no Windows machine and no cTrader
desktop terminal required. Built to run as a Railway worker.

Strategy: **Awesome Oscillator divergence + Supply & Demand + Support &
Resistance + Quasimodo**, traded on XAUUSD during the week and BTCUSD at the
weekend. The full rule set lives in [`STRATEGY.md`](STRATEGY.md).

---

## Status

Complete and tested end to end. Transport, authentication, data, scheduling,
the full pattern-recognition pipeline, risk and execution are all implemented.

| Area | Module |
| --- | --- |
| Config from environment | `bot/config.py` |
| Open API auth + reconnect | `bot/ctrader/client.py` |
| Symbol lookup, contract details, trading schedule | `bot/ctrader/symbols.py` |
| H4 / M15 / M5 OHLCV → pandas | `bot/ctrader/trendbars.py` |
| Session windows (DST-aware) | `bot/session.py` |
| Gold-session instrument switching | `bot/scheduler.py` |
| Awesome Oscillator, ATR | `bot/indicators.py` |
| Swing/pivot detection | `bot/strategy/swings.py` |
| **HAPI 1** — H4 trend classification | `bot/strategy/trend.py` |
| **HAPI 1/6** — SND zone mapping (DBD/RBR) | `bot/strategy/snd.py` |
| **HAPI 2** — AO divergence | `bot/strategy/divergence.py` |
| **HAPI 3** — structure break (BOS/MSS) | `bot/strategy/structure.py` |
| **HAPI 4** — Quasimodo recognition | `bot/strategy/quasimodo.py` |
| **HAPI 5** — SNR level clustering | `bot/strategy/snr.py` |
| **HAPI 6** — stop loss / take profit / sizing | `bot/risk.py` |
| Pipeline orchestration | `bot/strategy/engine.py` |
| Order placement + cancellation | `bot/execution.py` |
| Five-point analysis report | `bot/report.py` |

Confirmed strategy parameters: **2/2 pivot strength**, breakout confirmed by a
candle **body close**, **36 M5 candles (3h)** maximum pattern age, entry
**exactly at the left-shoulder price**.

A report from a fully aligned market looks like this:

```
====================================================================
 XAUUSD | 2026-09-19 16:18:08 UTC
====================================================================

1. H4 TREND        : BEARISH
   Lower High and Lower Low on H4 (highs 145.2 -> 135.2, lows 139.8 -> 129.8).

2. AO DIVERGENCE   : NO
   No sell-side AO divergence on M15 or M5 (a warning only, not required for entry).

3. STRUCTURE       : BROKEN
   SELL BOS on M5: broke support at 89.8 (2026-01-05 05:30 UTC)

4. QM SETUP        : VALID - order may be placed
   SELL | Left Shoulder 100.20 | Head 110.20 | Breakout 79.80

5. LEVELS
   Direction   : SELL LIMIT
   Entry       : 100.20
   Entry zone  : 100.20 -> 110.20
   Stop loss   : 110.65
   Take profit : 87.60
   Risk/Reward : 1.21
   Volume      : 900 units
   SNR confluence: none

NOTES
   - H4 BEARISH -> only SELL setups allowed.
   - Take profit taken from the nearest M15 zone.
====================================================================
```

---

## How the instrument switching works

The switch happens on **session boundaries**, not midnight. Gold is traded
whenever the Gold market is open — including from the moment it reopens on
Sunday evening — and Bitcoin only fills the window while Gold is genuinely
shut.

`bot/session.py` and `bot/scheduler.py` own this. Three pieces:

1. **Is Gold open?** Two sources, in order of authority. The broker's own
   weekly schedule (`ProtoOASymbol.schedule`) is exact and covers holidays and
   maintenance breaks. When the broker publishes none, the configured session
   window answers instead — defined in a market timezone
   (`America/New_York` by default) so daylight saving is handled by the zone
   rather than a hardcoded UTC offset. `SUN 18:00` is 22:00 UTC in summer and
   23:00 UTC in winter.

2. **Transition detection.** The loop calls `poll()` once per tick, which
   returns `(active_symbol, switch_or_None)`. `switch` is non-`None` exactly
   once per changeover, and carries the reason (which source decided, and
   whether the market opened or closed).

3. **Handover.** On a switch `main.py` cancels any pending order left on the
   outgoing instrument and drops its cached QM pattern, so a gold limit order
   cannot sit unattended in the book over the weekend. Only then does it
   resolve the new symbol, subscribe to its spot feed and analyse it.

Both symbols are configurable (`SYMBOL_WEEKDAY`, `SYMBOL_WEEKEND`) and the
resolver tolerates broker naming variants — `XAUUSD`, `XAU/USD`, `GOLD`,
`XAUUSD.r` all match.

---

## Setup

### 1. Get cTrader Open API credentials

1. Register an application at <https://openapi.ctrader.com/> → **Applications**.
   You get a **Client ID** and **Client Secret**.
2. Run the OAuth2 flow with scope `trading` to obtain an **access token** (and
   a refresh token — worth keeping, the bot will use it to renew
   automatically).
3. Find your **`ctidTraderAccountId`**. This is *not* your cTrader login
   number; it is the integer the API uses. `ProtoOAGetAccountListByAccessToken`
   returns it for your token.

### 2. Configure

```bash
cp .env.example .env
# fill in CTRADER_APP_ID, CTRADER_APP_SECRET, CTRADER_ACCESS_TOKEN, CTRADER_ACCOUNT_ID
```

| Variable | Required | Default | Notes |
| --- | --- | --- | --- |
| `CTRADER_APP_ID` | yes | — | Open API Client ID |
| `CTRADER_APP_SECRET` | yes | — | Open API Client Secret |
| `CTRADER_ACCESS_TOKEN` | yes | — | OAuth2 token, scope `trading` |
| `CTRADER_ACCOUNT_ID` | yes | — | `ctidTraderAccountId` (integer) |
| `CTRADER_REFRESH_TOKEN` | no | — | Enables automatic token renewal |
| `CTRADER_HOST_TYPE` | no | `demo` | `demo` or `live` |
| `SYMBOL_WEEKDAY` | no | `XAUUSD` | Monday–Friday instrument |
| `SYMBOL_WEEKEND` | no | `BTCUSD` | Saturday–Sunday instrument |
| `BOT_TIMEZONE` | no | `UTC` | IANA zone used for logging timestamps |
| `GOLD_SESSION_TIMEZONE` | no | `America/New_York` | Market zone for the session window |
| `GOLD_SESSION_OPEN` | no | `SUN 18:00` | Session open, in the market zone |
| `GOLD_SESSION_CLOSE` | no | `FRI 17:00` | Session close, in the market zone |
| `REQUIRE_H4_ZONE_PROXIMITY` | no | `true` | Require price to be at the H4 zone (HAPI 1) |
| `H4_ZONE_PROXIMITY_ATR` | no | `1.5` | How near "at the zone" means, in H4 ATRs |
| `LOOP_INTERVAL_SECONDS` | no | `60` | Seconds between analysis passes |
| `BARS_H4` / `BARS_M15` / `BARS_M5` | no | `400`/`500`/`500` | Candles per timeframe |
| `RISK_PERCENT` | no | `0.5` | Percent of balance risked per trade |
| `FIXED_VOLUME_LOTS` | no | `0` | `>0` bypasses percentage sizing |
| `MAX_OPEN_POSITIONS` | no | `1` | Per instrument |
| `MAX_PENDING_ORDERS` | no | `1` | Per instrument |
| `ORDER_EXPIRY_MINUTES` | no | `240` | `0` = good till cancel |
| `ENABLE_TRADING` | no | `false` | **Safety switch** — see below |
| `LOG_LEVEL` | no | `INFO` | |

Credentials are only ever read from the environment. `.env` is gitignored and
`Config.redacted()` is what gets logged, so secrets stay out of the logs.

### 3. Run locally

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
pytest                 # 164 tests, no network needed
python -u main.py
```

### 4. Deploy to Railway

1. Create a project from this repository. Nixpacks picks up `runtime.txt`
   (Python 3.11) and `requirements.txt`.
2. Add every variable from the table above under **Variables**.
3. The service is a **worker**, not a web service — it exposes no port.
   `Procfile` and `railway.json` both declare `python -u main.py`.
4. Railway restarts on failure (max 10 retries). The bot also handles SIGTERM
   itself and shuts down in well under a second.

---

## Safety

`ENABLE_TRADING` defaults to **`false`**. In that mode the bot connects,
fetches data, runs the full analysis and prints the report — but never sends
an order. Leave it off until you have watched the reports for a while and are
happy with the signals, and run against `CTRADER_HOST_TYPE=demo` first.

Other guards already in place:

- risk/reward below **1.0** is rejected before an order is built,
- `MAX_OPEN_POSITIONS` / `MAX_PENDING_ORDERS` cap exposure per instrument,
- a QM invalidation (candle closing beyond the head) cancels the pending order,
- pending orders expire after `ORDER_EXPIRY_MINUTES`,
- instrument handover cancels stale orders on the outgoing symbol.

---

## Architecture

```
main.py                     24/7 loop, signal handling, reactor bootstrap
bot/
  reactor_setup.py          installs the asyncio Twisted reactor (import first!)
  config.py                 environment parsing + validation
  session.py                DST-aware market session windows
  scheduler.py              XAUUSD while Gold is open / BTCUSD while it is shut
  indicators.py             Awesome Oscillator, ATR
  risk.py                   stop loss, take profit, position sizing
  execution.py              spot feed, balance, orders, cancellation
  report.py                 the five-point analysis output
  ctrader/
    client.py               auth, reconnect, Deferred→async bridge
    symbols.py              symbol lookup, contract details, trading schedule
    trendbars.py            OHLCV fetch and decode
  strategy/
    types.py                shared value objects
    swings.py               pivot detection (2/2 confirmation)
    engine.py               HAPI 1→6 orchestration
    trend.py                HAPI 1 — H4 bias
    snd.py                  HAPI 1/6 — Drop-Base-Drop / Rally-Base-Rally zones
    divergence.py           HAPI 2 — AO divergence
    structure.py            HAPI 3 — break of structure (body close)
    quasimodo.py            HAPI 4 — QM recognition
    snr.py                  HAPI 5 — S/R level clustering
```

### Two implementation notes

**The asyncio reactor must be installed first.** `ctrader_open_api` is
Twisted-based and imports `twisted.internet.reactor` at import time. `main.py`
therefore calls `bot.reactor_setup.install()` before any other import (hence
the `# noqa: E402` markers). That gives us Twisted's proven transport with
plain `async`/`await` strategy code via `Deferred.asFuture()`.

**`pandas-ta` is not used.** It has no installable distribution for the Python
3.11 runtime here — the published versions either predate modern numpy or
require Python ≥ 3.12. The Awesome Oscillator is
`SMA(median, 5) − SMA(median, 34)`, so `bot/indicators.py` computes it (and
ATR) directly on pandas. One less dependency to break a deploy.

`requirements.txt` also pins `pyOpenSSL` and `cryptography` together: an older
pyOpenSSL against a modern cryptography aborts Twisted's TLS import with
`AttributeError: module 'lib' has no attribute 'GEN_EMAIL'`.

---

## Tests

```bash
pytest -q     # 164 tests
```

No network access required. Coverage includes:

- **Session boundaries** — the Sunday reopen and Friday close, and that the
  same `SUN 18:00` lands on 22:00 UTC in summer and 23:00 UTC in winter;
- **Instrument handover** — switching both ways, the broker schedule
  overriding the window, and the switch firing exactly once;
- **Indicators** — AO against a manually computed SMA difference;
- **Pattern modules** — each of the six, on purpose-built fixtures: HH/HL vs
  LH/LL vs expanding range; Drop-Base-Drop and Rally-Base-Rally geometry
  including which edge is proximal; repeated-bounce S/R clustering; a wick
  through a level rejected where a body close is accepted; price-HH-with-AO-LH
  divergence and the momentum-confirmed case that must *not* fire; the QM
  four-pivot shape, entry at the shoulder, the 36-bar age limit and
  invalidation;
- **Risk** — the full chain, the R:R floor and volume-step snapping;
- **Pipeline** — an aligned market producing a complete priced setup, plus each
  gate standing the bot down, and a random-walk smoke test.

`tests/factories.py` builds deterministic OHLCV from zigzag paths, so a test
states the shape it wants and gets pivots exactly where it expects them.
