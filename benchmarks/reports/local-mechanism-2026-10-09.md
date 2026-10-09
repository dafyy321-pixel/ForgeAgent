# ForgeAgent experiment report

Experiment: `be63e092-b8df-429f-9e77-c18c4640614f`. Status: `completed`.
Dataset: `mechanism@1`, model: `fixture@1`. Independent cases: 1; repeats: 2.

| Configuration | Disposition rate | Model cost USD | Full cost USD | Mean wall seconds |
|---|---:|---:|---:|---:|
| baseline | 1.000 | 0.000000 | incomplete | 25.833 |
| no_plan | 1.000 | 0.000000 | incomplete | 17.947 |
| no_summary | 1.000 | 0.000000 | incomplete | 19.702 |
| fusion | 1.000 | 0.000000 | incomplete | 18.648 |

Paired case-cluster bootstrap comparisons:

```json
{
  "no_plan": {
    "baseline": "baseline",
    "paired_difference": 0,
    "cluster_95_ci": null,
    "cost_ratio": null,
    "noninferiority_supported": false
  },
  "no_summary": {
    "baseline": "baseline",
    "paired_difference": 0,
    "cluster_95_ci": null,
    "cost_ratio": null,
    "noninferiority_supported": false
  },
  "fusion": {
    "baseline": "baseline",
    "paired_difference": 0,
    "cluster_95_ci": null,
    "cost_ratio": null,
    "noninferiority_supported": false
  }
}
```

Failure, recovery and missing-cost evidence:

```json
{
  "baseline": {
    "full_cost_complete": false,
    "full_cost_usd": null,
    "known_cost_usd": 0.0,
    "full_cost_per_success_usd": null,
    "failure_distribution": {},
    "recovery_samples_seconds": [],
    "pending_recoveries": 0,
    "recovery_pickup_samples_seconds": [],
    "legacy_ambiguous_recoveries": 0,
    "missing_cost_components": [
      "environment_usd",
      "storage_usd",
      "tool:repo.write"
    ]
  },
  "no_plan": {
    "full_cost_complete": false,
    "full_cost_usd": null,
    "known_cost_usd": 0.0,
    "full_cost_per_success_usd": null,
    "failure_distribution": {},
    "recovery_samples_seconds": [],
    "pending_recoveries": 0,
    "recovery_pickup_samples_seconds": [],
    "legacy_ambiguous_recoveries": 0,
    "missing_cost_components": [
      "environment_usd",
      "storage_usd",
      "tool:repo.write"
    ]
  },
  "no_summary": {
    "full_cost_complete": false,
    "full_cost_usd": null,
    "known_cost_usd": 0.0,
    "full_cost_per_success_usd": null,
    "failure_distribution": {},
    "recovery_samples_seconds": [],
    "pending_recoveries": 0,
    "recovery_pickup_samples_seconds": [],
    "legacy_ambiguous_recoveries": 0,
    "missing_cost_components": [
      "environment_usd",
      "storage_usd",
      "tool:repo.write"
    ]
  },
  "fusion": {
    "full_cost_complete": false,
    "full_cost_usd": null,
    "known_cost_usd": 0.0,
    "full_cost_per_success_usd": null,
    "failure_distribution": {},
    "recovery_samples_seconds": [],
    "pending_recoveries": 0,
    "recovery_pickup_samples_seconds": [],
    "legacy_ambiguous_recoveries": 0,
    "missing_cost_components": [
      "environment_usd",
      "storage_usd",
      "tool:repo.write"
    ]
  }
}
```

Cluster bootstrap keeps repetitions together within registered cases. Incomplete full-cost estimates cannot establish total savings. External benchmark and oracle evidence require separate reports.

Fixture results validate machinery only; they do not measure real-model coding quality or savings.
Recovery latency starts at a recovered lease claim and ends at its first committed decision/tool/verification progress; pending recoveries remain censored.
Public historical regression pilots are not blind held-out evidence. Official benchmark scores require the external harness.

Report digest: `sha256:42d63e7f56ef3e808499ab85e4ea257bef9d5fcab277ca7deea51182d1832b70`.
