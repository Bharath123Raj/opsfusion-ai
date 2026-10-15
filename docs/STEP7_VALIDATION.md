# Step 7 verification

155 software tests pass in Python 3.12 with prior perception dependencies. New tests cover exact source-text copying, future/foreign/duplicate evidence refusal, compatible citations, catalogue constraints, unsafe output fields, fallback behavior, HTML escaping and actual HTTP requests against a loopback mock server.

Twenty scripted audit probes pass with zero live model calls. Probes include valid output, unknown or incompatible citations, wrong incident/category, confirmed-cause claims, probability fields, free text conclusions, automatic actions, missing alternatives, duplicate IDs, foreign-asset/future packets, source-instruction quarantine and model/provider fallback. The suite does not establish live-model accuracy, general injection robustness or physical-fault diagnosis accuracy.

The unchanged synthetic incident corpus was replayed through the episode engine, yielding 26 first-alert explanations. Facts were copied from validated source event records. Every explanation passed structural/type compatibility checks; all causes remained unconfirmed, and no actions executed.

The supplied four-source observation set was also replayed, yielding 11 explanations under episode segmentation. This preserves the composed real-video/generated-signal provenance and is not independently annotated physical truth.

The Ollama transport was exercised by a local mock HTTP server, including valid JSON, invalid JSON, tool-call refusal and malformed envelope rejection. The cloud API was not called with a real account/key in this environment. Live provider use must be verified separately by checking accepted_llm_selections and fallback counts.

The client omits cloud structured-output enforcement because current official Ollama documentation says Cloud does not support it. Credentials are not included in prompts or report artifacts; source paths/media are not uploaded. Generic provider error codes are retained rather than raw error bodies.

Remaining limits: catalogue-limited hypotheses, no calibrated cause probabilities, no semantic-entailment grader, no physical truth verification, completed-context references in prior adapter artifacts, and sparse/delayed-source segmentation failures. Structural validation is a program boundary, not a measurement of reasoning correctness.
