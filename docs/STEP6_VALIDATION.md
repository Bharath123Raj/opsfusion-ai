# Step 6 verification

128 software tests pass with the prior Python 3.12 audio/sensor/video dependencies. New tests cover episode boundaries, exact/above-threshold gaps, short-gap continuity, long episodes, mechanical/safety separation, non-bridging notes and low-confidence observations, bounded corroboration, duplicate records, stable IDs and immutable first alerts.

SHA-256 equality was checked against Step5 for both `data/incident-demo/events.jsonl` and `data/incident-demo/labels.json`. Neither data nor ground truth was modified to obtain the improved result.

| Corpus | Engine | TP | FP | FN | F1 |
|---|---|---:|---:|---:|---:|
| Step5 development corpus | Fixed | 7 | 0 | 1 | 0.9333 |
| Step5 development corpus | Episode, gap 20s | 8 | 0 | 0 | 1.0000 |
| Separate authored regression corpus | Fixed | 6 | 4 | 1 | 0.7059 |
| Separate authored regression corpus | Episode, gap 20s | 6 | 1 | 1 | 0.8571 |

The original miss was the second of two faults inside a 90-second fixed cluster. The gap segmenter creates separate incident IDs after the 36-second qualifying-event silence in that scenario.

The regression corpus was authored to probe the implementation after the problem was known. It is not a blind holdout. Its continuing sparse-fault case still causes a false split; its slow-second-source case is still missed. Source timestamps may show a quiet gap even while the underlying fault continues. No physical recovery is inferred as an observed fact.

A sensitivity sweep over 5, 10, 20, 30 and 45 seconds is included. It describes tradeoffs on that same corpus and does not automatically select a threshold. Evaluating a selected threshold on these labels again would not be independent validation.

The supplied four-source observation set emits 11 first alerts with episode segmentation, versus ten with fixed segmentation. This is a count check, not a ground-truth accuracy claim. All hypotheses remain unconfirmed and actions remain recommendations only.

Future work: explicit source activity/clear state, larger independent real-data annotation, media truncation at first confirmation, clock/arrival semantics and an incremental bounded-state production engine.
