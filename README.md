# clc-levels

Auto-refreshing price levels for the daily CLC pre-market routine (ES futures day trading).

## What this is

`levels.csv` holds the day's key levels in BookMap CSV format, readable by
MotiveWave's **Cloud Levels** study. It refreshes automatically every 30 minutes
during the NY session (Mon–Fri) via the `refresh-levels` GitHub Actions workflow.

## How the levels are built

`scripts/compute_levels.py` (stdlib only, no keys needed):

- **Gamma walls + flip**: SPX options chain from CBOE's free delayed quotes
  (`cdn.cboe.com/.../options/_SPX.json`), 0–1 DTE window. Per-strike dollar gamma
  = OI × gamma × spot² / 1e9. Call wall = max call-side gamma at/above spot,
  put wall = max put-side gamma at/below spot, flip = strike pair straddling
  zero nearest spot. SPX levels are translated to ES price space via the live
  ES/SPX ratio.
- **Spot + prior day high/low**: Yahoo Finance ES futures (`ES=F`), free.
- Rows are duplicated for `ES` and `MES` symbols (same prices) so the study's
  symbol filter matches either chart.

## Use in MotiveWave

1. Add the **Cloud Levels** study to the ES (or MES) chart.
2. Set its Source to the web address:
   `https://raw.githubusercontent.com/amkreddy002-source/clc-levels/main/levels.csv`
3. Set Update Interval to 30 minutes. Levels refresh on their own — no downloads.

Levels are reference only. Every trade still needs Context-Location-Confirmation
on live order flow; nothing here is a trade signal.
