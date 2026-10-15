# Incident ground-truth contract

```json
{
  "schema": "opsfusion_incident_labels_v1",
  "scope": "Human-reviewed candidate incident episodes in a specified replay",
  "synthetic": false,
  "evaluation_types": ["restricted_zone"],
  "incidents": [{"label_id":"zone-01","asset_id":"machine-a","type":"restricted_zone","start":"2026-01-01T10:00:00Z","end":"2026-01-01T10:00:10Z"}],
  "coverage": [{"asset_id":"machine-a","start":"2026-01-01T10:00:00Z","end":"2026-01-01T10:01:00Z","normal":false}]
}
```

Each label ID must be unique. Timestamps include timezone offsets. Label category must be in evaluation_types, and its interval must fit inside the declared asset coverage. Coverage is start-inclusive/end-exclusive for predictions and may not overlap per asset. Labels describe candidate report categories, not necessarily verified physical causes. Label interval endpoints are inclusive for matching first-alert time; predictions must still be inside coverage.

A normal coverage interval must represent independently reviewed absence of the evaluated target categories. Normal exposure cannot overlap other coverage intervals for the same asset. Splitting normal/fault periods precisely gives more meaningful false-alarm rates than marking an entire scenario abnormal. Included corpus coverage is conservative at scenario level and is documented as such.

The evaluator refuses predictions for undeclared assets or outside declared coverage. It filters categories to evaluation_types and explicitly records ignored report categories. These exclusions must be stated when reporting accuracy. It never copies labels into input observations or generates labels from predictions.

Predictions use first_emitted_at from replay's immutable first alerts. Same asset/category and an emission within the labelled interval are required. A declared late tolerance may extend the end, but does not permit early matching. A maximum-cardinality bipartite matching prevents both duplicate alerts and duplicated label credit; deterministic preference follows temporal proximity, but global minimum-delay matching is not claimed.

The benchmark's clock-offset stress variant shifts audio observation timestamps by 30 seconds against unchanged labels. It does not model network arrival delay, fix clocks, or introduce a real streaming watermark.
