# Step 4 verification

81 software tests pass on Linux/Python 3.12 with Torch 2.5.1+cpu, NumPy 1.26.4, SciPy 1.14.1, scikit-learn 1.5.2 and OpenCV 4.10.0.84. Existing Windows-compatible Conda setup is retained. Raw WAV processing, feature dimensions, split chronology, duplicate-content rejection, unknown assets, two-window causal confirmation, prefix invariance, weights-only loading, explicit log code mapping and previous pipeline behavior were checked.

Generated seed-42 audio: four 12-second normal training recordings, two 12-second normal calibration recordings, one 30-second test recording. Test deviations are at 4–10 and 18–24 seconds. A small neural autoencoder was actually trained and used for inference; predictions are not hardcoded from fault labels.

| Check | Observed result |
|---|---:|
| Test audio windows | 30 |
| Confirmed acoustic episodes | 2 |
| Mapped log events | 3 of 6 records |
| Combined source observations | 5 |
| Combined incident categories | 4 |
| Window TP / FP / TN / FN | 10 / 0 / 18 / 2 |
| Synthetic window precision / recall | 1.000 / 0.8333 |

Each synthetic deviation loses its first window to confirmation, as designed. The matching rule labels a window abnormal only if its whole second lies inside the annotated deviation interval. Independent replay source anchors and window coverage are verified by the evaluator. These scores measure generated spectral deviations, not machinery or bearing-failure accuracy.

The four aggregate reports are acoustic anomaly, operational warning, possible machine degradation and shutdown following warnings. All cause hypotheses remain unconfirmed, all demo evidence is synthetic, and no action executes. Reports are overlapping categories rather than independent verified incidents. Normal/unmapped records are not promoted to anomaly evidence by their text.

The package contains generated WAVs and raw manifests, plus a derived validation summary. Model weights and runtime-specific evidence paths are excluded so users train compatible local weights. Real industrial audio and vendor log validation are outstanding, as are incident-level modality ablations and live performance measurements.
