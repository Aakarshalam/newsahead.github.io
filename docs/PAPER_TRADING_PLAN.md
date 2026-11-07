# Two-week live paper-trading test (₹2,00,000, Indian LRS account → US markets)

**Window:** Mon 9 Nov – Fri 20 Nov 2026 (10 NYSE sessions, no US holidays).
**Mode:** paper trading on live prices. No real money moves; every fill, fee and delay is simulated as it would happen in the app.
**Goal:** small, consistent gains over many trades, following fixed rules mechanically, and learning which rules or signals cost us money.

## 1. How a real order would be placed from India

| Step | Real world | What the simulator assumes |
|---|---|---|
| Account | US-stocks account on an LRS platform (INDmoney, Vested, Appreciate, or a broker's "global investing" tab: Kotak Neo, ICICI Direct, HDFC Sky). KYC + PAN + LRS declaration (Form A2) done in-app. | Account open and funded before Monday. |
| Funding | INR sent from a savings account via the app's banking partner. Takes 1–2 business days. Forex markup is roughly 0.5–1.5%. No TCS below ₹10 lakh per financial year; above that, 20% TCS on the excess (refundable against tax). | ₹2,00,000 at the mid rate (₹93.89/$ on 7 Nov) minus **1% markup** → **$2,108.74**. No TCS. |
| Trading hours | NYSE/Nasdaq regular session: **8:00 PM – 2:30 AM IST** (US winter time from 1 Nov). | Only regular-session fills. |
| Order types | Market, limit, stop-loss (GTC on most apps), fractional shares. After-market orders queue for the next open. | Market buy (or market-on-open if placed while closed). Stop and target behave like GTC orders. |
| Shorting | Not available on LRS retail apps. | Long-only. Bearish views only via unleveraged inverse ETFs (SH, PSQ, TBF, UDN). |
| Fees | Usually zero commission. SEC fee (~0.00278% of sell value) and FINRA TAF (~$0.000166/share, capped at $8.30) on sells. | Same, plus slippage of 0.02% (ETFs), 0.05% (large caps), 0.15% (others). |
| Settlement | US T+1. Proceeds can be reused for the next trade straight away. | Cash is reusable immediately. |
| Bringing money back | Withdrawal takes 2–5 business days; forex markup applies again. | Account value is shown **net of a 1% markup on the way back**. |
| Tax (real life) | US shares are "unlisted foreign assets": gains held under 24 months are taxed at your income slab. Report in Schedule FA. US dividend withholding is 25% (DTAA). | Not deducted. Shown as a note only. |

**Speed: how quickly an order can happen after the news.**
1. News published → appears in RSS / Google News: usually 1–30 minutes.
2. Pipeline detects it: GitHub Actions runs every 15 minutes, and its scheduler often adds 5–15 minutes of delay.
3. Order sent → filled: under a second in the app while the market is open. Outside market hours, the order waits for **8:00 PM IST** and fills at the opening price, gap included.

Each trade records the delay from signal to fill and **how much the price had already moved before we could buy**. That measures how much of the backtested edge is still available to someone acting from India.

## 2. Fixed rules (no discretion for the whole test)

| Rule | Value | Why |
|---|---|---|
| Signal quality | score ≥ 65, and the traded market's backtest confidence must be Med/High | Most event types show no reliable edge; trading them would be gambling. |
| Freshness | Enter during the reaction session, or one session later if the edge lasts ≥ 3 days | Late entries capture little of the edge. |
| Priced in | Skip if the instrument has already moved at least the backtest's average move | Avoids buying the top of a move that has already happened. |
| Size | ₹50,000 per trade (25%), max 4 open | A stop-out costs about ₹1,750 (0.9% of capital). |
| Stop-loss | **−3.5%**, always | Your rule. |
| Breakeven | Stop moves to entry once a trade is up **+3%** | Turns winners into "can't lose" trades. |
| Target | **+10% single stocks / +5% ETFs** | Over 10 years, +10% in 10 days happened 0–5% of the time for ETFs, so they get a reachable target. |
| Time exit | After the backtest's best window (1/3/5/10 sessions) | The measured edge decays; holding longer is just market exposure. |
| Daily loss cap | No new entries after ₹4,000 realised loss in a session | Stops tilt and runaway losses. |
| Cool-off | No re-entry in a symbol for one session after a stop | Avoids revenge trades. |
| End of test | Everything sold at the 20 Nov close | Clean result. |

## 3. What gets measured

- **Account value in INR** after forex both ways, the number that actually matters. **Starting hurdle: about −2%** from forex alone.
- **The same money in SPY** bought at the first entry and held. If the strategy can't beat this, the signals added nothing.
- Per trade:
  - entry/exit, fees, slippage
  - **MFE/MAE**, the best and worst price reached while the trade was open
  - signal → fill delay, and how much of the move happened before we could buy
  - SPY's return over the same hold
- **Automatic post-mortem tags:**
  - stopped then recovered (stop too tight)
  - late entry
  - gave back an open gain (should have trailed)
  - market-wide drop
  - won but SPY did better
  - weaker Med-confidence edge
  - slow fill
- **Every signal the bot skipped, with the reason.** This is the "avenues that would have prevented losses" side.

## 4. Review checkpoints

| When | Questions |
|---|---|
| Daily (≈ 3 AM IST, after the US close) | What traded, what was skipped and why, did any rule misfire? |
| After session 3 (Wed 11 Nov) | Are signals arriving fast enough? How much is already priced in at entry? |
| After session 5 (Fri 13 Nov) | First tally against SPY. **No rule changes mid-test.** Proposed changes go in a list for after the test. |
| After session 10 (Fri 20 Nov) | Full review: win rate, expectancy per trade net of costs, against SPY. Which post-mortem tags recur? Which skip rules saved money? Then decide the next version of the rules. |

## 5. Honest expectations

- **Few trades are likely.** Only about a quarter of backtested event/market pairs have Med/High confidence, and the high-confidence events (CPI, payrolls, tariffs, chip-export rules) happen a few times a month. Two weeks may give 0–6 trades, too few to judge an edge statistically. The value is in learning about execution, latency and the rules.
- **The forex round trip (~2%) is the biggest single cost.** Keep the money in USD between trades, as the simulator does. Converting per trade would make small, consistent gains impossible.
