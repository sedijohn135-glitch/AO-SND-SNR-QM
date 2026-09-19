# AO + SND + SNR + QM — cTrader trading bot

A standalone Python trading bot that connects straight to the broker over the
**cTrader Open API** (protobuf over TLS) — no Windows machine and no cTrader
desktop terminal required. Built to run as a Railway worker.

Strategy: **Awesome Oscillator divergence + Supply & Demand + Support &
Resistance + Quasimodo**, traded on XAUUSD during the week and BTCUSD at the
weekend. The full rule set lives in [`STRATEGY.md`](STRATEGY.md).

---

## Status

This is the scaffold: transport, authentication, data, scheduling, risk and
execution are working. The **pattern-recognition math is deliberately not
written yet** — it is held for your sign-off, as agreed.

| Area | Module | Status |
| --- | --- | --- |
| Config from environment | `bot/config.py` | Done |
| Open API auth + reconnect | `bot/ctrader/client.py` | Done |
| Symbol lookup / contract details | `bot/ctrader/symbols.py` | Done |
| H4 / M15 / M5 OHLCV → pandas | `bot/ctrader/trendbars.py` | Done |
| Weekday/weekend switching | `bot/scheduler.py` | Done |
| Awesome Oscillator, ATR | `bot/indicators.py` | Done |
| Swing/pivot detection | `bot/strategy/swings.py` | Done |
| Stop loss / take profit / sizing | `bot/risk.py` | Done |
| Order placement + cancellation | `bot/execution.py` | Done |
| Five-point analysis report | `bot/report.py` | Done |
| **H4 trend classification** | `bot/strategy/trend.py` | **Awaiting sign-off** |
| **SND zone mapping (DBD/RBR)** | `bot/strategy/snd.py` | **Awaiting sign-off** |
| **SNR level clustering** | `bot/strategy/snr.py` | **Awaiting sign-off** |
| **Structure break (BOS/MSS)** | `bot/strategy/structure.py` | **Awaiting sign-off** |
| **AO divergence detection** | `bot/strategy/divergence.py` | **Awaiting sign-off** |
| **Quasimodo recognition** | `bot/strategy/quasimodo.py` | **Awaiting sign-off** |

Every stub carries a docstring describing the exact algorithm intended, and
sets `IMPLEMENTED = False`. The pipeline runs end to end today: it stops at
the first unimplemented gate and says so, rather than inventing a signal.

```
====================================================================
 XAUUSD | 2026-09-19 15:45:16 UTC
====================================================================

1. H4 TREND        : RANGING
   H4 trend detection not implemented yet

2. AO DIVERGENCE   : NO

3. STRUCTURE       : NOT BROKEN - entry forbidden

4. QM SETUP        : PENDING - pattern engine not implemented yet

5. LEVELS          : n/a - no executable setup

NOTES
   - HAPI 1 pending: H4 trend detection not implemented.
====================================================================
```

---

## How the weekday/weekend switching works

`bot/scheduler.py` owns the calendar. Three pieces:

1. **Selection.** `SymbolSchedule.symbol_for(moment)` converts the moment into
   `BOT_TIMEZONE` (default UTC) and returns `SYMBOL_WEEKEND` when
   `weekday() in {5, 6}` (Saturday, Sunday), otherwise `SYMBOL_WEEKDAY`.
   Evaluating in a configured zone rather than the container's local time
   keeps the boundary deterministic wherever Railway schedules the worker.

2. **Transition detection.** The loop calls `poll()` once per tick, which
   returns `(active_symbol, switch_or_None)`. `switch` is non-`None` exactly
   once per changeover, so handover work runs once and not on every tick.

3. **Handover.** On a switch `main.py` cancels any pending order left on the
   outgoing instrument and drops its cached QM pattern, so a gold limit order
   cannot sit unattended in the book all weekend. Only then does it resolve
   the new symbol, subscribe to its spot feed and analyse it.

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
| `BOT_TIMEZONE` | no | `UTC` | IANA zone used for the day-of-week test |
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
pytest                 # 66 tests, no network needed
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
  scheduler.py              XAUUSD weekdays / BTCUSD weekends
  indicators.py             Awesome Oscillator, ATR
  risk.py                   stop loss, take profit, position sizing
  execution.py              spot feed, balance, orders, cancellation
  report.py                 the five-point analysis output
  ctrader/
    client.py               auth, reconnect, Deferred→async bridge
    symbols.py              symbol lookup, contract details, volume maths
    trendbars.py            OHLCV fetch and decode
  strategy/
    types.py                shared value objects
    swings.py               pivot detection
    engine.py               HAPI 1→6 orchestration
    trend.py snd.py snr.py structure.py divergence.py quasimodo.py
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
pytest -q     # 66 tests
```

Covers the calendar and its timezone boundary, AO/ATR against a manual SMA
reference, pivot confirmation, trendbar delta/scale decoding, volume
normalisation, the full risk chain, config validation and redaction, the
report renderer, and an end-to-end pipeline pass on synthetic data. No network
access required.
