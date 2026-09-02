# Promotion rule

Written before the first measured run. Frozen at tag `eval-frozen`. The run either clears it or the change is reverted. The failure mode is not having no threshold, it is having a threshold nobody enforces.

**Question:** does the four-stage pipeline (v1: Intake, Plan, Build, Verify) beat one strong agent (v0: Intake, Build, Verify) on the same five ideas, with the same Build model, at an acceptable cost?

**v1 stays only if all three hold:**

1. **Verify pass rate.** v1 passes Verify on at least as many ideas as v0, and on at least 3 of 5.
2. **Human score.** v1's rubric total across the five ideas is higher than v0's. A tie goes to v0, because v0 is the simpler system and the burden of proof is on the second stage.
3. **Cost.** v1's total reported cost across the five ideas is at most 1.5x v0's. Reported cost, not billed, so the rule survives the move off the subscription.

**If v1 fails the rule,** the Plan stage is removed and the next candidate stage (Evidence) is tested against v0 directly. The answer "the pipeline does not beat one agent yet" is a valid outcome of this MVP and is written into the report as such.

**If v1 passes,** v1 becomes the floor that the next added stage has to beat.

**What does not count:** dev runs, runs with a different Build model between graphs, runs where the corpus or the rubric changed after tagging, or any run not in `eval/results/`.

## The ladder

There is always exactly one champion variant and at most one challenger. A challenger differs from the champion by exactly one variable (one added or changed stage), never two. The challenger runs the full frozen corpus and is promoted only if it clears the three-part rule above against the current champion. A `killed` run counts as a verify non-pass and scores 0 human points; the panel's kills are part of the challenger's record, not excused from it. On promotion the challenger becomes champion and the ladder moves one rung. On failure the variable is removed and the result recorded here; the champion stands.

Planned rung order: v1 -> v1r (repair) -> v2e (evidence) -> v2p (panel) -> v2d (design) -> v2x (split build). The ladder is a process, not code; nothing enforces it except this file and the recorded results.


## The crew

The `crew` variant is the acceptance shape, not a ladder rung: all 14 stations of the-crew.svg (three of them programs: review, verify, security), a publish interrupt, a live health check. It never runs in `eval/results/` and never competes on the ladder; the ladder stays one variable per rung. The crew is what the champion's stages graduate into.
