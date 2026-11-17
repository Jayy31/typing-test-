# Accuracy audit (2026-11) and fix tracker

Four code reviews (prediction pipeline, pricing/exits, backtest/look-ahead, learning) found the
problems below. **A next session continues from the first row that is not `done`.** Update the
Status column and the "Progress" section in the same commit as each fix. One PR per group
(A, B, C), merged when CI is green. Every fix needs a test.

Status: `todo` | `done (PR #n)` | `wontfix (why)`. "Checked" = confirmed by reading the code
before fixing (others must be confirmed while fixing; if a finding is wrong, mark `wontfix`).

## Progress
- 2026-11-16: audit finished, this file written.
- 2026-11-16: group A fixed (all rows), tests in `worker/tests/test_audit_fixes.py`. A2 note: the
  `row.iv or 30.0` fallback remains only in trade attribution / predicted-move targets. A11b: IV
  events are future `results` dates only (`options.yaml iv_event_types`). New config:
  `options.yaml simulator.risk_neutral_drift`, `entry.yaml recheck`, `exits.yaml day_rules_after`.
  Next: group B, then C.
- 2026-11-17: group A merged (PR #41). Group B fixed (see rows; B4 and B10a partly). New config:
  `learning.yaml soft_stages` / `review_min_changed`. Calibrator Brier is now cross-validated and a
  calibrator is activated only if it beats the raw probabilities. Next: group C.

## A. Live trading and trade selection
| # | Where | Problem | Fix | Checked | Status |
|---|---|---|---|---|---|
| A1 | `trading/paper_engine.py:383` (+134,177,459), `market/live.py` chain depth window | Open trade skipped when its row has no bid (Kotak chain rows only get bid/ask within `strikes_each_side` of ATM); `with_depth` only called after the bid guard; no expiry settlement, trade stays OPEN forever | Call depth before the guards (entry + monitor); settle at intrinsic on/after expiry | yes | done (PR #41) |
| A2 | `signals/options_stage.py:303`; `paper_engine.py:270,365` | IV solver failure replaced by ATM IV / 30 -> inflated EV, picked by max-EV | Skip candidates with no solvable IV; keep fallback only for attribution | yes | done (PR #41) |
| A3 | `sim/simulator.py:90-96` | Paths have no risk-neutral drift while BS uses r: calls penalised ~4 pts vs puts | Add (r)·t to the log path | yes | done (PR #41) |
| A4 | `signals/prediction.py:30-64`, `positioning/evidence.py:78-96` | p_move from the sign of point masses (analogue quantiles, positioning mean), measured hit rates unused | p_move = AI part + analogue weight x hit_rate + positioning weight x hit_up | yes | done (PR #41) |
| A5 | `kg/signs.py:57`, `history/event_study.py:433`, `signals/prediction.py:52` | Graph analogue stats applied with wrong sign when the learned sign flips the default | Flip stats (agree flag) when sign is flipped | no | done (PR #41) |
| A6 | `signals/service.py:154`, `history/analogues.py:96` | Analogue event type = alphabetical first; `kg:` keys beat direct types | Order by evidence weight, direct types first | yes | done (PR #41) |
| A7 | `kg/propagate.py:178`, `signals/candidates.py:44,159`, `signals/scoring.py:20` | Graph support rows count as independent sources / votes | Exclude support rows from source count and source quality | no | done (PR #41) |
| A8 | `signals/options_stage.py:200-204,336` | Next-month expiry chosen on EV although current month is valid | Only the rule's expiry (current if >= min days and event not after it, else next) | yes | done (PR #41) |
| A9 | `signals/options_stage.py:252-254` | Edge ratio: predicted move and implied move on different horizons | One horizon for both (prediction window) | no | done (PR #41) |
| A10 | `trading/paper_engine.py:132-178` | Entry does not re-check spread / premium drift vs the priced candidate | Re-run liquidity check + max premium drift at entry | no | done (PR #41) |
| A11a | `trading/lab_signals.py:192` | Lab `next_open` on Friday/holiday eve always hits entry TTL | Use `deferred.next_open` | yes | done (PR #41) |
| A11b | `signals/options_stage.py:238-246,284` | IV crush / event-in-window for past (today) events and any calendar row | Only future results-type events | no | done (PR #41) |
| A11c | `trading/paper_engine.py:560` | Exit fill class uses total lots, partial exits unclassified | Classify with the lots of that exit | no | done (PR #41) |
| A11d | `market/kotak.py:226`, `market/live.py` | Row ts = fetch time, stale LTP used for IV | Rows without depth: no IV from LTP into atm_iv/history unless traded today (volume>0) | no | done (PR #41) |
| A11e | `scheduler.py:217`, `regime/service.py:41` | FII figure stored every calendar day -> weekends double count | De-duplicate by trading day | no | done (PR #41) |
| A11f | `paper_engine.py:393` vs `sim/exits_vec.py:101` | Live date-based exits fire at first poll, sim at close | Live TIME/WINDOW/NOMOVE only after 15:00 of the qualifying day | no | done (PR #41) |

## B. Learning system
| # | Where | Problem | Fix | Checked | Status |
|---|---|---|---|---|---|
| B1 | `calibration/service.py:205-209` | Rejection never replayed -> labelled T1 not reached | Leave unresolved (excluded) when no replay state | yes | done (group B PR) |
| B2 | `tracking/counterfactual.py:124-129`, `config/tracking.yaml` | 60 oldest open rows per run incl. rows with no option reference -> newer rows starved | Only rows with an option reference; least-recently-updated first; stock-only rows resolved separately | yes | done (group B PR) |
| B3 | `tracking/scorecard.py:56-73`, `research/review.py:54` | `scored_at` never written -> weekly review always empty | Set `scored_at` | yes | done (group B PR) |
| B4 | `signals/service.py:218,464,559`, `signals/deferred.py` | After-hours predictions never scored / calibrated | Create score + records when the deferred signal is priced | yes | partly done (group B PR): after-hours analyses that raised a signal are scored at the open. todo: after-hours analyses that did NOT raise a signal still get no scorecard row |
| B5 | `analytics/learning.py:74-120` | Replay counts hard-rejected candidates | Only soft-gate rejections (stage in soft set) | no | done (group B PR) |
| B6 | `analytics/learning.py:51-95,140` | Costs differ (trades net, rejections gross); rows not clustered; lab books swept in | Same cost on rejections; cluster bootstrap by news event; `book == watchlist` only | no | done (group B PR) |
| B7 | `analytics/learning.py:296-312` | Revert suggested with zero evidence, no significance test | Need min differing opportunities and CI below 0 | no | done (group B PR) |
| B8 | `analytics/live.py:66-69` | T1 hit counts TIME/THESIS exits | Use targets_hit only | yes | done (group B PR) |
| B9 | `calibration/fit.py:136`, `calibration/service.py:309` | In-sample Brier, unconditional activation | Cross-validated Brier; activate only if better than raw | no | done (group B PR) |
| B10a | `analytics/ai_vs_rules.py:66` | Neutral AI rows never resolve; `made` over-counted; no clustering | Resolve neutral rows from stock move; made = this analysis created the signal | no | partly done (group B PR): `made` fixed. todo: neutral-AI rows still never resolve (need a price path without a PredictionScore) and rows are not clustered by news event. Report-only; does not feed predictions |
| B10b | `analytics/live.py:256-289` | Drift check ignores live sampling noise | Alert only if live CI and backtest CI do not overlap | no | done (group B PR) |
| B10c | `tracking/scorecard.py:59-64,108` | Direction hit measured at window + buffer close | Use the window day | no | done (group B PR) |
| B10d | `analytics/learning.py:215` | Lookback 4 months instead of 3 | Fix month arithmetic | no | done (group B PR) |

## C. History, backtests, Strategy Lab
| # | Where | Problem | Fix | Checked | Status |
|---|---|---|---|---|---|
| C1 | `lab/refine.py:33-47`, `lab/registry.py:242` | Holdout failure triggers variants, each with a fresh holdout | Stage `holdout_failed`; hypothesis closed | yes | todo |
| C2 | `history/event_study.py:99`, `backtest/data.py:181`, `lab/scheduled.py:118` | Events with no time enter at same-day close (before after-hours news) | Unknown time -> next trading day | yes | todo |
| C3 | `lab/backtest.py:241-266,394`, `backtest/engine.py:646` | `next_open`: no exits on entry day, full-day data on entry bar | Check exits on the entry day; previous-day liquidity for tiers | no | todo |
| C4 | `lab/backtest.py:414` | base_rate direction for scheduled events uses direction-adjusted mean | Use raw up/down stat; else no direction (skip) | yes | todo |
| C5 | `lab/backtest.py:433-441` | news_core spec ignores placebo shift / variants | Pass shift through; plateau skipped for news_core | yes | todo |
| C6 | `positioning/history.py:181-199` | Raw closes: splits appear as returns | Adjusted closes + corp-action window exclusion | no | todo |
| C7 | `lab/sources.py:128-133` | "Stable halves" split is not chronological | Sort by date | yes | todo |
| C8a | `history/event_study.py:299,328`, `backtest/engine.py` | Analogue move includes day-0 jump not tradable at reaction close | Post-entry stat (close day0 -> day+w) for gating in backtest | no | todo |
| C8b | `history/corp_actions.py:195`, `backtest/engine.py:655` | Demerger / special dividend: contract lost until expiry | Exit at last tradable price before ex-date | no | todo |
| C8c | `lab/registry.py:153`, `lab/validate.py:40` | Stage 0 trades exit on post-cutoff prices | Force exit at cutoff | no | todo |
| C8d | `lab/books.py:220` | END (still open) trades counted in incubation | Exclude END | no | todo |
| C8e | `history/iv_backfill.py:31` | IV from non-traded closes | Require volume > 0 | no | todo |
| C8f | `history/event_study.py:502,545` | History regime label uses same-day close | Use previous day | no | todo |
| C8g | `positioning/features.py`, `history/iv_backfill.py:44` | Live vs history skew at different strikes; live PCR drops rising/falling | Same strike rule live; keep EOD pcr change state | no | todo |

## Found sound (do not re-audit)
BS pricing/greeks, pure + vectorised exits (stop first, SL moves, whole lots), costs and spread
(not double counted), attribution sum, walk-forward folds, point-in-time universe/lots, R1 guard,
Platt/isotonic maths, deflated Sharpe, event-clustered bootstrap in live metrics and backtest.
