# TASK-020 ML Discord Anomaly Output

**Goal:** Format and post the four ML message types — anomaly advisory (entry), anomaly cleared (exit, optional), daily warm-up progress line, warm-up go-live notice — visually distinct from every engine message.

**Acceptance Criteria:**
- [ ] Distinct `🔬 ML` prefix/format — an ML advisory can never be mistaken for an engine state transition or a system-status alert
- [ ] Advisory: flag, anomaly score, top dimensions + z-scores, model version, explicit *"advisory only — does not change engine state"* footer
- [ ] Cleared: single line when the anomalous state exits (config-gated, default on)
- [ ] Warm-up progress: max ONE line per day per config while warming up (e.g. "still learning — day 7 of ~15")
- [ ] Go-live notice: posted once when a config's model first leaves warm-up
- [ ] Delivery failure raises `DiscordDeliveryError`-equivalent, caught by TASK-021 — never propagates into the engine loop

**Inputs:** ML Spec §8; Build Prompt 7; `src/aeolus/output/discord.py` conventions (embeds, truncation, IST presentation).

**Output:** `src/aeolus/ml/output.py`.

**Edge Cases:** webhook not configured (module disabled, log once); duplicate warm-up line suppression across restarts (persist last-posted date via `ml_anomaly_scores` or store lookup); message length truncation.

**Depends on:** TASK-018, TASK-019.

**Global constraints:** see `docs/CONSTRAINTS.md` (incl. ML overlay constraints). Posting cadence governed by TASK-018's debounce/hysteresis — this module never decides *whether*, only *how* to post.

**Status:** DRAFT
