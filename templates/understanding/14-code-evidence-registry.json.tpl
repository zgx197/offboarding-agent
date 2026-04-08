{
  "meta": {
    "taskId": "${task_id}",
    "module": "${module_path}",
    "updatedAt": "${updated_at}",
    "confidence": "medium"
  },
  "items": [
    {
      "id": "code-001",
      "scope": "in_target",
      "kind": "method",
      "path": "<repo-relative-path>",
      "symbol": "<namespace.type.member>",
      "startLine": 1,
      "endLine": 1,
      "role": "<why-this-code-anchor-matters>",
      "supports": [
        "<conclusion-or-contract-supported-by-this-anchor>"
      ],
      "impacts": [
        "<change-surface-or-flow-id>"
      ],
      "linkedEvidence": [
        "ev-xxx"
      ],
      "consumers": [
        "<subsystem-id-or-external-consumer>"
      ],
      "confidence": "medium",
      "notes": "<optional-note>"
    }
  ]
}
