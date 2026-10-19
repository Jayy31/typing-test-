# Phase 12 setup checklist

## PR 0: core rules
Nothing to set up. The rules are always on (no switch) and live in `worker/config/core_rules.yaml`
(index symbols, monthly-expiry window, never later than next month). If a signal is ever blocked by
them you will see stage `core_rule` on the dashboard's Rejected page.
