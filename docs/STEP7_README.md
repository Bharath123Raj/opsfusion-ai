# OpsFusion AI — Step 7 (v0.7.0)

Incident evidence → cited source reports → possible explanations → uncertainty → recommended checks.

This cumulative package includes Steps 1–6. Extract Step7 separately, open PowerShell in the folder containing this README, and reuse your existing `opsfusion312` Conda environment. No new dependency is needed for Step7. Keep earlier output folders and media evidence.

Two modes:

- `template` (default): offline deterministic catalogue, no key or network call.
- `llm`: an optional Ollama model selects/prioritizes compatible catalogue hypotheses and checks. Local validators enforce the output contract. Facts are assembled from source records, never authored by the LLM.

This is bounded evidence review, not physical root-cause diagnosis or an autonomous action agent.

## 1. Test the software and output policy

```powershell
conda run -n opsfusion312 python -m unittest discover -s tests -v

conda run -n opsfusion312 python -m opsfusion.reasoning_audit --output .\runs\reasoning-audit-01
```

Expected: 155 tests pass; 20/20 scripted probes pass with zero live model calls. The audit tests known output defects and valid behavior, not live-model accuracy or general prompt-injection robustness.

## 2. Create a labelled demo replay

```powershell
conda run --no-capture-output -n opsfusion312 python -m opsfusion.replay --events .\data\incident-demo\events.jsonl --engine episode --output .\runs\reasoning-replay-01
```

Expected: 24 observations, 26 first alerts across all report categories. The incident evaluation benchmark still scores only its three declared target categories; these 26 reports are not 26 independently labelled physical faults.

## 3. Generate explanations with no API key

```powershell
conda run --no-capture-output -n opsfusion312 python -m opsfusion.reasoning --replay .\runs\reasoning-replay-01\replay.json --output .\runs\reasoning-01

Start-Process .\runs\reasoning-01\report.html
notepad .\runs\reasoning-01\run_manifest.json
```

Expected: `26 explanations, 26 structurally validated`. Each report has copied source observations, possible explanations with event-ID citations, recommended checks with citations, provenance and uncertainty. Every cause stays unconfirmed. All checks are recommendations; nothing executes.

The default is `--scope first`: only the frozen first-alert snapshot is selected. To review completed context instead, pass `--scope final`; scope is recorded explicitly. First scope does not truncate external episode media or text to the first-emission time; prior batch adapters may reference completed context.

## 4. Explain your own four-source replay

First create it from your existing Step4 observations if needed:

```powershell
conda run --no-capture-output -n opsfusion312 python -m opsfusion.replay --events "$env:USERPROFILE\Downloads\OpsFusion-AI-Step4\OpsFusion-AI-Step4\runs\four-source-demo-01\combined_events.jsonl" --engine episode --output .\runs\four-source-replay-01

conda run --no-capture-output -n opsfusion312 python -m opsfusion.reasoning --replay .\runs\four-source-replay-01\replay.json --output .\runs\four-source-explanations-01

Start-Process .\runs\four-source-explanations-01\report.html
```

Expected for the previously supplied source set: 11 explanations from 26 observations. This composed replay contains real public video plus generated sensors/audio and synthetic logs, so aggregate provenance must remain visible.

Existing output folders are protected. Choose a new output name for repeats. Raw source facts and earlier model weights are not copied or modified.

## 5. Optional live Ollama Cloud mode

Direct cloud requests use `https://ollama.com/api/chat`, bearer API-key authentication and a model available to your account. No local Ollama installation or GPU is needed for direct cloud access. This mode sends selected asset IDs, source summaries, event IDs and times to the provider. Evidence file paths, source files and media bytes are not sent.

Enter your key in PowerShell without pasting it into chat:

```powershell
$opsfusionKey = Read-Host "Ollama API key" -AsSecureString
$env:OLLAMA_API_KEY = [System.Net.NetworkCredential]::new('', $opsfusionKey).Password
```

Check model names if needed:

```powershell
(Invoke-RestMethod -Uri "https://ollama.com/api/tags" -Headers @{Authorization="Bearer $env:OLLAMA_API_KEY"}).models | Select-Object name
```

The official cloud API guide uses `gpt-oss:120b`; replace it with a model actually available to your account. Start with one machine-degradation report:

```powershell
conda run --no-capture-output -n opsfusion312 python -m opsfusion.reasoning --replay .\runs\reasoning-replay-01\replay.json --mode llm --model "gpt-oss:120b" --type machine_degradation --limit 1 --output .\runs\reasoning-llm-01

notepad .\runs\reasoning-llm-01\run_manifest.json
Start-Process .\runs\reasoning-llm-01\report.html
```

Inspect `accepted_llm_selections` and `fallbacks`. A successful explanation generated through deterministic fallback is not a successful live-model selection. Model output that breaks the contract or encounters a provider failure uses a visibly labelled fallback with a generic reason code. Keys and raw provider errors are never written to report artifacts.

Ollama Cloud's current documentation says structured outputs are not supported there. The client does not pass a schema/format flag; it requests JSON in the prompt and validates the response locally. This may fall back more frequently than an endpoint with enforced structured output. Live cloud behavior was not tested in this environment; the actual HTTP path was checked against a local mock server.

For local Ollama, use a model you already have and `--endpoint http://localhost:11434/api/chat`. A cloud key is not sent to loopback endpoints. HTTP is permitted only for local loopback; external endpoints require HTTPS. Redirects are rejected.

## What the validator actually checks

- Exact response keys: incident_id, status, hypotheses, checks.
- Status must be unconfirmed, and the incident ID must match.
- Each list has two to four distinct allowed catalogue IDs.
- Every entry contains a nonempty, unique list of existing, compatible event IDs.
- Sensor, audio, controller and zone-specific checks must cite compatible observations.
- Extra fact text, probabilities, confirmed-cause fields and action commands are rejected.
- Provider tool calls, incomplete responses and malformed envelopes are rejected.

The LLM cannot invent fact text, cause probabilities or action instructions in a published report because those fields are not accepted from it. Catalogue descriptions are supplied by the program. Citation validation establishes reference/type compatibility, not semantic entailment, source accuracy or causal proof. The model can still select a plausible but wrong hypothesis; it remains explicitly possible. The catalogue is limited and does not cover every explanation.

Source summaries are quoted as reported data, not promoted to verified physical facts. A malicious source string can remain visible in the quoted section, but it cannot add output commands or override the schema. The scripted injection test verifies that program boundary only, not general resistance of every live model.

## Outputs

| File | Meaning |
|---|---|
| explanations.json | Source reports, compatible citations, possibilities, uncertainty and checks |
| report.html | Readable incident explanations |
| run_manifest.json | Input hash, scope, requested mode, accepted model selections and fallback counts |
| reasoning-audit-01/audit.json | Scripted validation probes with explicit scope |
| reports/step7_demo_explanations.json | Synthetic no-key example |
| reports/step7_audit.json | Verified scripted audit |

All reports retain cause_status=unconfirmed and automatic_action_executed=false. Risk is configured review priority, not a calibrated safety assessment. This milestone does not add raw-media analysis, probability estimation, hypothesis ground truth, historical maintenance retrieval or operational action execution.

Next: the incident review dashboard and API, followed by deployment. Retain current perception/segmentation failures, and add independently reviewed real-data explanations before claiming reasoning quality.

Official references:
- https://docs.ollama.com/cloud/api
- https://docs.ollama.com/api/chat
- https://docs.ollama.com/capabilities/structured-outputs
