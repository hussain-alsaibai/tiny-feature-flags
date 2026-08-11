# tiny-feature-flags — Zero-Dependency Feature Flags for Python

> **Percentage rollouts, user targeting, environment rules, JSON persistence, hot-reload. One file, zero deps, MIT.**

[![Python 3.8+](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Zero Dependencies](https://img.shields.io/badge/dependencies-zero-brightgreen.svg)](tiny_feature_flags.py)

## Why?

Launching features is risky. Feature flags let you ship code behind a switch — test with a subset of users, roll out gradually, and kill a bad feature instantly without a rollback. LaunchDarkly charges $75/mo. `tiny-feature-flags` does the 90% case in one file.

## Features

- **🎯 Attribute targeting** — user_id, plan, country, role, email domain, any context key
- **📊 Percentage rollouts** — deterministic bucketing, same user always gets same result
- **🧩 Operators** — `eq`, `neq`, `in`, `not_in`, `gt`, `lt`, `gte`, `lte`, `regex`, `contains`, `starts_with`, `ends_with`
- **🔁 Hot-reload** — watch the JSON file and reload flags without restart
- **💾 JSON persistence** — edit flags.json and the engine picks it up
- **🔧 Kill-switches** — global `enabled: false` disables a flag regardless of rules
- **🪶 Zero dependencies** — stdlib `json` only
- **📦 Single file** — drop in, no pip install needed
- **🔗 Integration** — works as a standalone module or alongside `tiny-agent`

## Install

```bash
pip install tiny-feature-flags
```

Or copy `tiny_feature_flags.py` — MIT licensed.

## Quick Start

```python
from tiny_feature_flags import FeatureFlags, Flag, FlagRule, configure

# ── Programmatic ──────────────────────────────────────────────
ff = FeatureFlags()

ff.add_flag(Flag(
    name="new-checkout",
    enabled=True,
    rollout_percent=10.0,  # 10% rollout
))

ff.add_flag(Flag(
    name="pro-tier",
    enabled=True,
    rules=[
        FlagRule(attribute="plan", operator="eq", value="pro"),
        FlagRule(attribute="country", operator="in", value=["US", "UK", "DE"]),
    ],
))

ff.add_flag(Flag(
    name="dark-mode",
    enabled=True,
    default_value=False,
    rules=[
        FlagRule(attribute="plan", operator="eq", value="pro", rollout_percent=100.0),
        FlagRule(attribute="plan", operator="eq", value="free", rollout_percent=20.0),
    ],
))

# Evaluate
if ff.is_enabled("new-checkout"):
    activate_new_checkout()

if ff.is_enabled("pro-tier", user_id="u_123", plan="pro", country="US"):
    show_pro_feature()

# ── File-based (recommended for production) ─────────────────
ff = FeatureFlags.load("flags.json")
if ff.is_enabled("llm-v4", user_id="u_456"):
    use_gpt_o1()

# ── Hot-reload (edit flags.json, engine auto-updates) ────────
ff = FeatureFlags.load("flags.json", hot_reload=True)
```

## flags.json Example

```json
{
  "version": "1.0",
  "generated": "2026-08-11T13:00:00Z",
  "flags": [
    {
      "name": "new-checkout",
      "enabled": true,
      "default_value": false,
      "rollout_percent": 10.0,
      "description": "Gradual rollout of the new checkout flow"
    },
    {
      "name": "pro-tier-feature",
      "enabled": true,
      "default_value": false,
      "rules": [
        {
          "attribute": "plan",
          "operator": "eq",
          "value": "pro",
          "rollout_percent": 100.0
        },
        {
          "attribute": "plan",
          "operator": "eq",
          "value": "enterprise",
          "rollout_percent": 100.0
        }
      ]
    },
    {
      "name": "region-lock",
      "enabled": true,
      "default_value": true,
      "rules": [
        {
          "attribute": "country",
          "operator": "not_in",
          "value": ["RU", "CN", "IR"],
          "rollout_percent": 100.0
        }
      ]
    },
    {
      "name": "ai-model-v4",
      "enabled": true,
      "default_value": false,
      "rules": [
        {
          "attribute": "user_id",
          "operator": "regex",
          "value": "^u_(test|beta)",
          "rollout_percent": 100.0
        },
        {
          "attribute": "plan",
          "operator": "eq",
          "value": "enterprise",
          "rollout_percent": 50.0
        }
      ]
    },
    {
      "name": "max-retries",
      "enabled": true,
      "default_value": 3,
      "rules": [
        {
          "attribute": "plan",
          "operator": "eq",
          "value": "pro",
          "value": 5,
          "rollout_percent": 100.0
        }
      ]
    }
  ]
}
```

## Targeting Operators

| Operator | Description | Example |
|----------|-------------|---------|
| `eq` | Equals | `plan eq "pro"` |
| `neq` | Not equals | `country neq "RU"` |
| `in` | In list | `country in ["US", "UK"]` |
| `not_in` | Not in list | `role not_in ["guest"]` |
| `gt` | Greater than | `age gt 18` |
| `lt` | Less than | `score lt 100` |
| `gte` | ≥ | `tokens gte 1000000` |
| `lte` | ≤ | `cost_usd lte 5.0` |
| `regex` | Regex match | `email regex ".*@acme.com"` |
| `contains` | Substring | `email contains "@acme"` |
| `starts_with` | Prefix | `user_id starts_with "beta_"` |
| `ends_with` | Suffix | `email ends_with ".edu"` |

## Real-World Patterns

### AI Agent Model Rollout

```python
# flags.json: gradually move users from gpt-4o-mini to gpt-4o
ff = FeatureFlags.load("flags.json")

def pick_model(user_id: str, plan: str) -> str:
    if ff.is_enabled("llm-o1", user_id=user_id, plan=plan):
        return "o1-mini"
    return "gpt-4o-mini"
```

### Gradual Cost Control

```python
# Start with 5% of users, ramp to 100%
ff = FeatureFlags.load("flags.json")

for user_id in all_users:
    if ff.is_enabled("auto-retry", user_id=user_id):
        max_retries = ff.evaluate("auto-retry-count", user_id=user_id, default=3)
        run_with_retries(task, max_retries=max_retries)
```

### Kill Switch

```python
# Instantly disable a feature for everyone — no redeploy needed
ff.enable("flaky-feature")   # re-enable
ff.disable("flaky-feature")  # kill switch

ff.save()  # persist to flags.json
```

## CLI

```bash
# Set environment variable
export FEATURE_FLAGS_FILE=flags.json

# Check flag status
python -m tiny_feature_flags new-checkout status
# Flag: new-checkout
#   Enabled: True
#   Default: False
#   Rollout: 10.0%
#   Rules: 0

# Toggle flags from CI/CD
python -m tiny_feature_flags new-checkout disable
python -m tiny_feature_flags pro-tier enable
```

## API Reference

### `FeatureFlags(...)`

```python
ff = FeatureFlags(
    flags=None,                    # dict of Flag objects
    default_env="production",      # (future: per-env overrides)
    hot_reload=False,              # watch file for changes
    file_path=None,                # path for save/load
    evaluation_callback=None,      # fn(flag_name, context) -> bool|None
)
```

### `ff.is_enabled(flag_name, default=None, **context) -> bool`

Evaluate a flag. Returns `default` if flag not found.

### `ff.evaluate(flag_name, default=None, **context) -> Any`

Evaluate and return any value (not just boolean).

### `ff.enable(name)` / `ff.disable(name)`

Toggle flag kill-switch.

### `ff.save(path=None)` / `FeatureFlags.load(path)`

JSON file persistence.

### `@ff.wrap()` decorator

Auto-track LLM calls with cost budgets.

## Ecosystem

Part of the **tiny-\*** ecosystem — zero-dependency, single-file Python tools:

| Tool | Description |
|------|-------------|
| `tiny-feature-flags` | Feature flags |
| `tiny-budget` | Token cost tracking |
| `tiny-tracer` | Distributed tracing |
| `tiny-log` | Structured logging |
| `tiny-validator` | Input validation |
| `tiny-agent` | AI agent framework |
| `fast-cache` | LRU + TTL cache |
| [All tiny-* repos →](https://github.com/hussain-alsaibai?tab=repositories&q=tiny-) |

## License

MIT — use freely, no attribution required.
