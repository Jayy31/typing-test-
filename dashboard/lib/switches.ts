/** Owner switches editable from the dashboard (everything else stays in YAML / worker config).
 * Defaults mirror worker/app/flags.py DEFAULTS and the brief settings. */
export const SWITCHES: { key: string; label: string; help: string; default: boolean }[] = [
  { key: "brief.morning_enabled", label: "Morning brief (08:45)", help: "Telegram market brief before the open", default: true },
  { key: "brief.evening_enabled", label: "Evening summary (16:00)", help: "Telegram summary after the close", default: true },
  { key: "alerts_muted", label: "Mute signal alerts", help: "Health alerts always arrive", default: false },
  { key: "feat.research_agent", label: "Research agent", help: "Replaces the panel: 2 tool-using investigations a day, then the single analyst", default: false },
  { key: "feat.multi_agent", label: "Multi-agent panel", help: "Else the single analyst only (not used while the research agent is on)", default: true },
  { key: "feat.probabilities", label: "Calibrated probabilities", help: "Use a calibrator once fitted", default: true },
  { key: "feat.simulator", label: "Options simulator (EV)", help: "Strike/expiry choice by EV", default: true },
  { key: "feat.implied_move_gate", label: "Implied-move gate", help: "Edge ratio check", default: true },
  { key: "feat.event_study", label: "Historical analogues", help: "Event-study base rates", default: true },
  { key: "feat.deep_data", label: "Structured filings evidence", help: "Insider, deals, results, regulators", default: true },
  { key: "feat.knowledge_graph", label: "Knowledge graph", help: "2nd/3rd-order signals", default: true },
];
