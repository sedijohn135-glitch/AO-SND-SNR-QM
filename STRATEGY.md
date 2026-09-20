# Strategy specification — AO + SND + SNR + QM

The reference for every rule the bot implements. Step names (HAPI 1–6) follow
the original brief; the Albanian rule is quoted, then the implementation
decision that encodes it.

Timeframes: **H4** (direction), **M15** (zones / early warning), **M5**
(structure, pattern, entry). Only *closed* candles are ever evaluated —
`drop_forming_bar()` removes a still-forming bar so a signal cannot appear and
vanish inside the same candle.

---

## HAPI 1 — Direction (H4)

> *Analizo grafikun 4-orësh (H4). A po bën çmimi Higher Highs (Trend Rritës)
> apo Lower Lows (Trend Rënës)? … Nëse çmimi refuzon H4 Demand, kërko vetëm
> BUY. Nëse refuzon H4 Supply, kërko vetëm SELL.*

| Swing sequence | Bias | Permission |
| --- | --- | --- |
| Higher High **and** Higher Low | `BULLISH` | BUY only |
| Lower High **and** Lower Low | `BEARISH` | SELL only |
| anything else | `RANGING` | no trading |

`TrendBias.allows(direction)` is the single place this permission is enforced.
A `RANGING` H4 blocks both directions — the bot stands down rather than
guessing. Requiring *both* the highs and the lows to agree keeps the bot out of
expanding ranges, where a higher high sits beside a lower low.

### Counter-trend scalps (`ALLOW_COUNTER_TREND`, default off)

Danial takes counter-trend scalps when the M15 AO divergence and M5 QM are
exceptionally clear, noting they carry higher risk. Setting
`ALLOW_COUNTER_TREND=true` permits the opposite direction — a SELL under a
bullish H4, or a BUY under a bearish one.

Two safeguards stay:

- **AO divergence becomes mandatory.** With the trend it is a warning (HAPI 2);
  against the trend it is the entire justification, so a counter-trend setup
  without it is rejected outright.
- **A ranging H4 still blocks both directions** — counter-trend trading needs a
  trend to trade against.

The trend-aligned direction is always evaluated first and wins if it produces
anything; the counter-trend pass only runs when it does not. Everything
downstream — structure break, QM, risk — applies unchanged. Such trades carry
`(Counter-Trend)` in the Telegram alert and `[COUNTER-TREND PASS]` in the
report.

The rule also says *where* price must be: a bearish H4 wants price at Supply, a
bullish H4 at Demand. `REQUIRE_H4_ZONE_PROXIMITY` (default **on**) enforces
this — price must sit inside the required zone, or within
`H4_ZONE_PROXIMITY_ATR` × H4 ATR of it. When no zone of the wanted kind can be
mapped at all the check is *skipped* rather than failed, so a detection gap
cannot silently block every trade.

Modules: `bot/strategy/trend.py`, `bot/strategy/snd.py` (H4 zone map).

### How a zone is drawn

Candle classification is ATR-relative, so the same thresholds work on XAUUSD
and BTCUSD without retuning:

- **impulsive** — body ≥ 0.6 × ATR(14) *and* the body fills ≥ 50% of the
  candle's range (a wide range that is mostly wick is indecision, not an
  impulse);
- **basing** — anything *not* impulsive whose range stays within 1.5 × ATR. A
  base is a pause, so the test is the absence of a strong move rather than a
  strict doji shape.

> Both thresholds were loosened after live BTCUSD data showed the originals
> (1.0 × ATR, and a near-bodyless base) mapping almost no zones at all — around
> 0.5 per 500 bars, of which 0.2 unbroken. Textbook Drop-Base-Drop structures
> visible on the chart were being missed. The current values map roughly 21 per
> 500 bars, ~5 unbroken.

A zone is `impulse → 1..5 base candles → impulse in the same direction`. Its
boundaries follow the usual proximal/distal construction, where the *proximal*
line is the edge price meets first:

| Zone | Proximal (met first) | Distal |
| --- | --- | --- |
| **Supply** (approached from below) | **bottom** = lowest body of the base | **top** = highest high of the base |
| **Demand** (approached from above) | **top** = highest body of the base | **bottom** = lowest low of the base |

`touches` counts later candles that traded into the zone. `broken` is set once
a candle *closes* through the distal line; broken zones are never targeted.

## HAPI 2 — Early warning: AO divergence (M15 / M5) — *optional*

> *Divergjenca NUK është sinjal hyrjeje, por paralajmërim për t'u përgatitur
> për Setupin QM.*

- **Bearish** (prepare to SELL): price Higher High, AO Lower High.
- **Bullish** (prepare to BUY): price Lower Low, AO Higher Low.

AO = `SMA(median price, 5) − SMA(median price, 34)`, median = `(high+low)/2`.

Two filters keep the signal meaningful: the two pivots must be no more than 60
bars apart, and the AO must be on the correct side of zero (a bearish
divergence belongs above the zero line, a bullish one below). AO needs 34 bars
before it returns a value, so a pivot inside that warm-up has no reading and is
skipped rather than guessed at.

Divergence never creates or blocks a trade. It is recorded in the report as a
flag only. Module: `bot/strategy/divergence.py`, indicator in
`bot/indicators.py`.

## HAPI 3 — Structure break (MSS / BOS) — the hard gate

> *Nëse nuk ka thyerje zone, anuloje çdo setup. "Instant Entry" është e
> ndaluar!*

Before any entry, price must break a nearby SND zone or S/R level:

- **for SELL** — break a Demand area (Rally-Base-Rally) or the most recent
  swing low,
- **for BUY** — break a Supply area (Drop-Base-Drop) or the most recent swing
  high.

The break must be confirmed by a candle **body close** beyond the level — a
wick through it is not a market structure shift — and must have happened within
the last 50 closed candles. References older than 150 bars are considered
stale. M5 is checked first, then M15. No break → the pipeline stops and no
setup is produced. Module: `bot/strategy/structure.py`.

## HAPI 4 — Quasimodo pattern and entry (M5)

> *Për SELL: Identifiko një majë (Left Shoulder) … një majë më të lartë (Head)
> … një rënie poshtë fundit të mëparshëm (Lower Low).*

**SELL** — four pivots in order:
1. `left_shoulder` — a swing **high**
2. the swing **low** after it
3. `head` — a swing **high** strictly above the left shoulder
4. `breakout` — a swing **low** strictly below the low from (2)

Entry zone: from the **left-shoulder price** (near edge) to the **highest wick
of the head** (far edge). Order: **SELL LIMIT** at the left-shoulder price.

**BUY** is the exact mirror: low → high → lower low → higher high, with a
**BUY LIMIT** at the left-shoulder low.

Confirmed parameters:

| Parameter | Value |
| --- | --- |
| Pivot strength | **2/2** — two bars either side confirm a pivot |
| Breakout confirmation | a candle **body close** beyond the prior extreme; a wick is not enough |
| Maximum pattern age | **36 M5 candles (3 hours)** after the breakout closes |
| Entry placement | **exactly at the left-shoulder price** — no deeper offset into the zone |
| Head level | the **absolute extreme** of every candle between shoulder and breakout, not the matched pivot |
| Minimum depth | the head must be at least **0.5 × ATR** beyond the shoulder |

### Why the head is not simply the matched pivot

The four pivots locate the *formation*; the head's **price** is the absolute
extreme across it. A 2/2 fractal only needs two bars either side, so it will
mark a micro-pause a couple of points below the shoulder while the real
structural low sits far beneath. The stop is measured from the head, so that
mistake produces a stop of noise width — one live BUY was placed with a 19.96
point stop on BTCUSD and a fictitious 32:1 reward ratio.

Taking the absolute extreme is necessary but not sufficient: the same fractal
will match a formation that is *itself* only a couple of points tall, where the
extreme and the pivot coincide at the same trivial level.
`MIN_HEAD_DEPTH_ATR` therefore rejects any formation shallower than half an
ATR. Structure that small is noise, and a stop drawn inside it is a guaranteed
loss rather than a risk limit.

A pattern price has already closed through is dropped at detection, and a setup
whose price already sits inside the zone is reported as `WAITING_RETEST` rather
than sent — a limit order there would fill instantly, which HAPI 3 forbids.

Module: `bot/strategy/quasimodo.py`, pivots from `bot/strategy/swings.py`.

## HAPI 5 — SNR confluence

> *Verifiko nëse pika e hyrjes te "Left Shoulder" përputhet horizontalisht me
> një nivel historik të Support/Resistance.*

Swing pivots — highs and lows in the same pool, since broken support becomes
resistance — are clustered into horizontal levels with an ATR-relative
tolerance (0.25 × ATR). A cluster of at least 2 pivots becomes a level. If the
left shoulder lines up with one, the setup is marked maximum-probability.
Confluence raises confidence; it is not a filter that blocks a trade.
Module: `bot/strategy/snr.py`.

## HAPI 6 — Trade management

> *Kjo është një strategji "Scalping". Objektivi i vetëm është Zona e Kundërt
> … më e afërt në M15 ose M5.*

| Rule | Implementation |
| --- | --- |
| **Stop loss** | Beyond the head's extreme wick, padded by `1.5 × spread` (min. one tick). `risk.stop_loss_for()` |
| **Take profit** | The **near edge** of the nearest opposing SND zone — M15 first, then M5, searching the whole loaded history. Unbroken zones are preferred; mitigated ones are used rather than giving up. `risk.take_profit_for()` |
| **Take profit fallback** | When no zone yields a usable target — none found, wrong side of entry, or too close to clear the R:R floor — the target becomes a fixed **1:2** multiple of the stop distance. A valid QM setup is never blocked for want of a zone. `risk.fallback_take_profit()` |
| **Invalidation** | A candle **closing** beyond the head kills the setup; the pending order is cancelled. `quasimodo.is_invalidated()` |
| **Sizing** | `RISK_PERCENT` of balance over the stop distance, snapped to the broker volume step. `risk.position_volume()` |
| **Currency** | The balance is in the account's *deposit* currency; the stop distance is in the symbol's *quote* currency. The risk budget is converted with a live rate before the two meet. `ctrader.conversion.CurrencyConverter` |
| **Margin cap** | Size is capped so the position's margin stays within 80% of free margin. A tight stop otherwise sizes into an order the broker rejects. `risk.margin_capped_volume()` |
| **R:R floor** | A zone target below 1.0 R:R is discarded in favour of the fixed 1:2 fallback, not rejected outright. `risk.MIN_RISK_REWARD` |

Exit at the **first** opposing zone — this is a scalp, the trade is not held
for a second target.

### Currency and margin

On a EUR account trading USD-quoted instruments, risking "2.5% of balance"
against a stop quoted in USD is a unit mismatch. The rate comes from the broker:
`ProtoOATrader.depositAssetId` and the symbol's `quoteAssetId` feed
`ProtoOASymbolsForConversionReq`, whose chain is then priced from live spots.
Without a rate the tick is skipped rather than sized on a guess — a wrong rate
on a JPY-quoted pair would over-size by orders of magnitude.

Margin is then checked before the order goes out. Because size scales inversely
with the stop, a tight M5 QM stop can demand more margin than the account holds.
The position is scaled down to fit 80% of free margin and the Telegram alert
says `(Scaled for margin)`. If even the broker minimum will not fit, the setup
is reported rather than sent. The report and the Telegram message both name the source
of the target (`zone` or `fixed 1:2`), so it is always visible which applied.

---

## Macroeconomic news filter

A pre-flight gate that runs before HAPI 1, ahead of any analysis — a blackout
has to pull resting orders whether or not a setup exists.

| Rule | Value |
| --- | --- |
| Source | Forex Factory weekly JSON (`ff_calendar_thisweek.json`) |
| Watched | `country == "USD"`, `impact == "High"` (both configurable) |
| Blackout window | event time −30 min to +30 min |
| On blackout | suppress new entries **and** cancel pending limit orders |
| Open positions | **left alone** — the stop loss already caps the risk |

Applies to XAUUSD and BTCUSD alike: a USD release moves both.

**Fail-safe.** The feed is rate limited to two downloads per five minutes, so
the calendar is cached and refreshed at most hourly, with exponential backoff
on failure. If it cannot be read, the bot trades on the last good copy while it
is under 24h old; with no usable copy at all it blocks new entries. Better to
miss a trade than to trade blind into NFP.

All-day and tentative entries are published at local midnight with no real
release time, so a ±30 minute window around them is meaningless — they are
skipped by default.

Module: `bot/news.py`, gate in `main._tick`.

## Instrument calendar

The switch happens on **session boundaries**, not midnight:

| Market state | Instrument |
| --- | --- |
| Gold market **open** | `XAUUSD` |
| Gold market **closed** (the weekend gap) | `BTCUSD` |

So the Sunday-evening gold reopen is traded as gold from the moment it opens,
and Bitcoin only fills the window while gold is genuinely shut.

Two sources answer "is gold open?", in order of authority:

1. **The broker's own schedule** (`ProtoOASymbol.schedule`) — a list of weekly
   intervals in seconds from Sunday 00:00 in `scheduleTimeZone`. Exact, and it
   covers holidays and daily maintenance breaks.
2. **The configured session window**, used when the broker publishes no
   schedule. Defined in a market timezone (`America/New_York` by default) so
   daylight saving is handled by the zone rather than a hardcoded UTC offset:

   `GOLD_SESSION_OPEN=SUN 18:00` → 22:00 UTC in summer, 23:00 UTC in winter
   `GOLD_SESSION_CLOSE=FRI 17:00` → 21:00 UTC in summer, 22:00 UTC in winter

On changeover the bot cancels any pending order left on the outgoing
instrument before analysing the new one, so a stale gold limit cannot sit in
the book all weekend. Modules: `bot/scheduler.py`, `bot/session.py`.

## Execution order of gates

```
News blackout active?                yes ──► cancel pendings, skip the tick
   │                                         (open positions untouched)
   ▼
H4 bias ──► direction permitted?      no ──► stand down
   │                                    (ALLOW_COUNTER_TREND retries the
   │                                     opposite way, divergence required)
   │
   ▼
Price at the required H4 zone?        no ──► stand down (gate is configurable)
   │
   ├─ AO divergence (flag only, never blocks)
   │
   ▼
M5/M15 break closed through?          no ──► stand down ("Instant Entry" forbidden)
   │
   ▼
M5 Quasimodo found (≤36 bars old)?    no ──► stand down
   │
   ▼
QM invalidated (close beyond head)?  yes ──► stand down
   │
   ▼
Risk: SL, then TP (zone, else 1:2)
      sizing above broker minimum?    fail ──► stand down
   │
   ▼
Price still outside the zone?         no ──► WAITING_RETEST (no instant fill)
   │
   ▼
VALID ──► send LIMIT order
```
