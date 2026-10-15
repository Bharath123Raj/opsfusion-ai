# Step 5 verification

106 software tests pass in Python 3.12 with the previous audio/sensor/video dependencies. Twenty-five new tests cover deterministic chronological replay, immutable first alerts, equal-time batching, conflicting IDs, coverage validation, one-to-one incident matching, undefined metrics, normal exposure and safe local HTML output.

The uploaded four-source report was inspected: 26 accepted observations and ten incident reports. Replaying its observation nodes emitted ten first alerts and preserved all hypotheses as unconfirmed. This checks supplied event records only; original media evidence is not independently verified.

Authored corpus: ten synthetic scenarios, 24 adapter observations and eight independent candidate incident labels. All-source fusion scores TP=7, FP=0, FN=1, precision=1.0, recall=0.875 and F1≈0.9333. Matched median delay is five virtual seconds. The missed label is the second distinct fault within the fixed 90-second grouping window. It is retained as a known failure.

Nine variants are evaluated: all, four single modalities, three missing-source variants and an audio clock +30-second stress variant. The clock variant scores TP=5, FP=2, FN=3, exposing temporal matching failure even though some same-window correlations remain possible. False positives during fault-labelled coverage are distinct from false alerts in normal exposure.

These measurements benchmark downstream fusion rules on authored events. They do not measure CV accuracy, acoustic model accuracy, real factory failure detection or causal diagnosis. The corpus intentionally contains small, simple cases and about 0.21 normal asset-hours. More diverse independent and real-data labels remain necessary.

Immutable snapshots prohibit future observation IDs, but referenced adapter episode artifacts may contain completed context. This does not establish full causal media truncation. There is no live streaming service, out-of-order transport handling or real-time notification measurement.
