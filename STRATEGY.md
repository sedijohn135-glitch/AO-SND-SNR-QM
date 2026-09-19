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
guessing.

Modules: `bot/strategy/trend.py`, `bot/strategy/snd.py` (H4 zone map).

## HAPI 2 — Early warning: AO divergence (M15 / M5) — *optional*

> *Divergjenca NUK është sinjal hyrjeje, por paralajmërim për t'u përgatitur
> për Setupin QM.*

- **Bearish** (prepare to SELL): price Higher High, AO Lower High.
- **Bullish** (prepare to BUY): price Lower Low, AO Higher Low.

AO = `SMA(median price, 5) − SMA(median price, 34)`, median = `(high+low)/2`.

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

The break must be confirmed by a candle **close** beyond the level, not a
wick, and must have happened within the recent lookback window. No break → the
pipeline stops and no setup is produced. Module:
`bot/strategy/structure.py`.

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

Module: `bot/strategy/quasimodo.py`, pivots from `bot/strategy/swings.py`.

## HAPI 5 — SNR confluence

> *Verifiko nëse pika e hyrjes te "Left Shoulder" përputhet horizontalisht me
> një nivel historik të Support/Resistance.*

Swing pivots are clustered into horizontal levels. If the left shoulder lines
up with one (within half an ATR), the setup is marked maximum-probability.
Confluence raises confidence; it is not a filter that blocks a trade.
Module: `bot/strategy/snr.py`.

## HAPI 6 — Trade management

> *Kjo është një strategji "Scalping". Objektivi i vetëm është Zona e Kundërt
> … më e afërt në M15 ose M5.*

| Rule | Implementation |
| --- | --- |
| **Stop loss** | Beyond the head's extreme wick, padded by `1.5 × spread` (min. one tick). `risk.stop_loss_for()` |
| **Take profit** | The **near edge** of the nearest opposing M15 SND zone — the edge price reaches first. `risk.take_profit_for()` |
| **Invalidation** | A candle **closing** beyond the head kills the setup; the pending order is cancelled. `quasimodo.is_invalidated()` |
| **Sizing** | `RISK_PERCENT` of balance over the stop distance, snapped to the broker volume step. `risk.position_volume()` |
| **R:R floor** | Setups below 1.0 R:R are rejected. `risk.MIN_RISK_REWARD` |

Exit at the **first** opposing zone — this is a scalp, the trade is not held
for a second target.

---

## Instrument calendar

| Day (in `BOT_TIMEZONE`, default UTC) | Instrument |
| --- | --- |
| Monday – Friday | `XAUUSD` |
| Saturday – Sunday | `BTCUSD` |

On changeover the bot cancels any pending order left on the outgoing
instrument before analysing the new one, so a stale gold limit cannot sit in
the book all weekend. Module: `bot/scheduler.py`.

> **Known gap:** gold closes around Friday 21:00 UTC and reopens Sunday ~22:00
> UTC. Because Sunday is a BTC day by this calendar, the Sunday-evening gold
> reopen is not traded. That follows the brief as written; say the word if you
> want the switch moved to the session boundary instead of midnight.

## Execution order of gates

```
H4 bias ──► direction permitted?      no ──► stand down
   │
   ├─ AO divergence (flag only, never blocks)
   │
   ▼
M5 structure break confirmed?         no ──► stand down ("Instant Entry" forbidden)
   │
   ▼
M5 Quasimodo found?                   no ──► stand down
   │
   ▼
QM invalidated (close beyond head)?  yes ──► stand down
   │
   ▼
Risk: SL / TP / size / R:R ≥ 1.0      fail ──► stand down
   │
   ▼
Price still outside the zone?         no ──► WAITING_RETEST (no instant fill)
   │
   ▼
VALID ──► send LIMIT order
```
