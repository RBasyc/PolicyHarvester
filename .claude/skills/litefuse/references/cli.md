# Litefuse CLI Reference

Documentation: https://litefuse.ai/docs/api-and-references/cli

## Install

```bash
# Run directly (recommended)
npx litefuse-cli api <resource> <action>
bunx litefuse-cli api <resource> <action>

# Or install globally
npm i -g litefuse-cli
litefuse api <resource> <action>
```

## Discovery

```bash
# List all resources and auth info
litefuse api __schema

# List actions for a resource
litefuse api <resource> --help

# Show args/options for a specific action
litefuse api <resource> <action> --help

# Preview the curl command without executing
litefuse api <resource> <action> --curl
```

## Credentials

Set environment variables:

```bash
export LANGFUSE_PUBLIC_KEY=pk-lf-...
export LANGFUSE_SECRET_KEY=sk-lf-...
export LANGFUSE_HOST=https://litefuse.cloud
```

## Tips

- Use `--json` for machine-readable JSON output
- Use `--curl` to preview the HTTP request without executing
- Pagination: use `--limit` and `--page` on list endpoints
- All list commands support filtering — check `<resource> <action> --help` for available options
- Prefer `observations-v2s` over `observations` — the v2 endpoint returns richer data
- Prefer `metrics-v2s` over `metrics` — the v2 endpoint returns richer data
- Prefer `score-v2s` over `scores` — the v1 `scores` resource only supports create/delete; use `score-v2s` for list and get operations
