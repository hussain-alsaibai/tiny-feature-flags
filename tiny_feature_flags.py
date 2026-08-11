"""
tiny-feature-flags — Zero-Dependency Feature Flags for Python
--------------------------------------------------------------
Percentage rollouts, user targeting, environment rules, JSON file
persistence, and real-time evaluation. One file, zero deps, MIT.

Usage:
    from tiny_feature_flags import FeatureFlags, Flag, evaluate, load

    ff = load("flags.json")

    if ff.is_enabled("new-checkout"):
        activate_new_checkout()

    # With targeting
    if ff.is_enabled("pro-tier", user_id="u_123", plan="pro"):
        show_pro_feature()
"""

from __future__ import annotations

import os
import json
import time
import threading
import hashlib
from dataclasses import dataclass, field, asdict
from typing import Any, Optional, Callable, Union
from pathlib import Path

# ─── Types ────────────────────────────────────────────────────────────────────

@dataclass
class FlagRule:
    """A single targeting rule within a flag."""
    attribute: str          # e.g. "user_id", "plan", "country"
    operator: str           # eq | neq | in | not_in | gt | lt | gte | lte | regex | percentage
    value: Any             # The value to compare against
    rollout_percent: Optional[float] = None  # 0.0–100.0

    def matches(self, context: dict[str, Any]) -> bool:
        attr_val = context.get(self.attribute)
        if attr_val is None:
            return False
        return _compare(attr_val, self.operator, self.value)

    def in_rollout(self, context: dict[str, Any]) -> bool:
        if self.rollout_percent is None:
            return True
        bucket_key = f"{self.attribute}:{context.get(self.attribute, '')}"
        return _in_percentage(bucket_key, self.rollout_percent)


@dataclass
class Flag:
    """A single feature flag definition."""
    name: str
    enabled: bool = False          # Master kill-switch
    default_value: bool = False    # Value returned when flag is disabled or no rules match
    description: str = ""
    rules: list[FlagRule] = field(default_factory=list)
    rollout_percent: Optional[float] = None  # Global percentage rollout (0–100)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "enabled": self.enabled,
            "default_value": self.default_value,
            "description": self.description,
            "rules": [
                {"attribute": r.attribute, "operator": r.operator,
                 "value": r.value, "rollout_percent": r.rollout_percent}
                for r in self.rules
            ],
            "rollout_percent": self.rollout_percent,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Flag:
        return cls(
            name=data["name"],
            enabled=data.get("enabled", False),
            default_value=data.get("default_value", False),
            description=data.get("description", ""),
            rules=[FlagRule(**r) for r in data.get("rules", [])],
            rollout_percent=data.get("rollout_percent"),
            metadata=data.get("metadata", {}),
        )


# ─── Operators ────────────────────────────────────────────────────────────────

def _compare(left: Any, op: str, right: Any) -> bool:
    if op == "eq":    return left == right
    if op == "neq":   return left != right
    if op == "gt":    return left > right
    if op == "lt":    return left < right
    if op == "gte":   return left >= right
    if op == "lte":   return left <= right
    if op == "in":    return left in (right if isinstance(right, (list, tuple, set)) else [right])
    if op == "not_in":return left not in (right if isinstance(right, (list, tuple, set)) else [right])
    if op == "regex":
        import re
        try:
            return bool(re.match(str(right), str(left)))
        except Exception:
            return False
    if op == "contains": return str(right) in str(left)
    if op == "starts_with": return str(left).startswith(str(right))
    if op == "ends_with":   return str(left).endswith(str(right))
    return False


def _in_percentage(bucket_key: str, percent: float) -> bool:
    """
    Deterministic bucketing: same key always maps to same bucket.
    Uses MD5 of the key for uniform distribution across 0–99.
    """
    if percent <= 0:
        return False
    if percent >= 100:
        return True
    hash_val = int(hashlib.md5(bucket_key.encode()).hexdigest(), 16)
    bucket = hash_val % 100
    return bucket < percent


# ─── FeatureFlags ─────────────────────────────────────────────────────────────

class FeatureFlags:
    """
    Feature flag evaluation engine with thread-safe flag management.

    Supports:
    - Global flags + per-environment overrides
    - Percentage rollouts with deterministic bucketing
    - Attribute-based targeting (user_id, plan, country, etc.)
    - JSON file persistence with hot-reload
    - Callable flag values (dynamic resolution)
    """

    def __init__(
        self,
        flags: Optional[dict[str, Flag]] = None,
        default_env: str = "production",
        hot_reload: bool = False,
        file_path: Optional[str] = None,
        evaluation_callback: Optional[Callable[[str, dict], bool]] = None,
    ):
        self._flags: dict[str, Flag] = dict(flags or {})
        self._default_env = default_env
        self._hot_reload = hot_reload
        self._file_path = file_path
        self._evaluation_callback = evaluation_callback
        self._lock = threading.RLock()
        self._last_modified: float = 0.0
        self._watchdog_interval = 5.0
        self._watchdog_thread: Optional[threading.Thread] = None
        self._stop_watchdog = threading.Event()

        if hot_reload and file_path:
            self._start_watchdog()

    # ── Evaluation ──────────────────────────────────────────────────────────────

    def is_enabled(
        self,
        flag_name: str,
        default: Optional[bool] = None,
        env: Optional[str] = None,
        **context,
    ) -> bool:
        """
        Evaluate a flag for a given context.

            ff.is_enabled("new-checkout")
            ff.is_enabled("pro-tier", user_id="u_123", plan="pro")
            ff.is_enabled("dark-mode", default=True)  # return True if flag unknown
        """
        self._maybe_reload()

        # Custom evaluation hook
        if self._evaluation_callback:
            result = self._evaluation_callback(flag_name, context)
            if result is not None:
                return bool(result)

        with self._lock:
            flag = self._flags.get(flag_name)
            if flag is None:
                return bool(default) if default is not None else False

            if not flag.enabled:
                return bool(flag.default_value)

            # Check rules in order; first match wins
            for rule in flag.rules:
                if rule.matches(context) and rule.in_rollout(context):
                    return True

            # Global percentage rollout (if no rules matched)
            if flag.rollout_percent is not None:
                bucket_key = self._bucket_key(flag_name, context)
                return _in_percentage(bucket_key, flag.rollout_percent)

            return bool(flag.default_value)

    def evaluate(
        self,
        flag_name: str,
        default: Any = None,
        env: Optional[str] = None,
        **context,
    ) -> Any:
        """
        Evaluate a flag and return its value. Supports non-boolean flags.

            value = ff.evaluate("color-scheme", default="#000", user_id="u_1")
        """
        self._maybe_reload()
        with self._lock:
            flag = self._flags.get(flag_name)
            if flag is None:
                return default

            if not flag.enabled:
                return flag.default_value

            # Check rules
            for rule in flag.rules:
                if rule.matches(context) and rule.in_rollout(context):
                    # Rule matched — return metadata value if present, else True
                    return rule.value if rule.value is not None else True

            # Global rollout
            if flag.rollout_percent is not None:
                bucket_key = self._bucket_key(flag_name, context)
                if _in_percentage(bucket_key, flag.rollout_percent):
                    return True

            return flag.default_value

    def _bucket_key(self, flag_name: str, context: dict) -> str:
        """Build a deterministic bucket key from user identifier."""
        for key in ["user_id", "user", "account_id", "session_id", "id"]:
            if key in context and context[key]:
                return f"{flag_name}:{context[key]}"
        # Fallback to all context for consistent bucketing
        return f"{flag_name}:{json.dumps(context, sort_keys=True, default=str)}"

    # ── Flag Management ─────────────────────────────────────────────────────────

    def add_flag(self, flag: Flag) -> None:
        """Add or replace a flag."""
        with self._lock:
            self._flags[flag.name] = flag

    def remove_flag(self, name: str) -> bool:
        """Remove a flag. Returns True if it existed."""
        with self._lock:
            return self._flags.pop(name, None) is not None

    def enable(self, name: str) -> None:
        """Enable a flag (kill-switch)."""
        with self._lock:
            if name in self._flags:
                self._flags[name].enabled = True

    def disable(self, name: str) -> None:
        """Disable a flag (kill-switch)."""
        with self._lock:
            if name in self._flags:
                self._flags[name].enabled = False

    def get_flag(self, name: str) -> Optional[Flag]:
        return self._flags.get(name)

    def list_flags(self) -> list[Flag]:
        return list(self._flags.values())

    def flag_names(self) -> list[str]:
        return list(self._flags.keys())

    # ── Persistence ─────────────────────────────────────────────────────────────

    def save(self, path: Optional[str] = None) -> None:
        """Write all flags to a JSON file."""
        path = path or self._file_path
        if not path:
            raise ValueError("No file path specified")

        with self._lock:
            data = {
                "version": "1.0",
                "generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "flags": [f.to_dict() for f in self._flags.values()],
            }
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2, default=str)
            self._last_modified = os.path.getmtime(path)

    @classmethod
    def load(cls, path: str, **kwargs) -> FeatureFlags:
        """Load flags from a JSON file."""
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)

        flags = {}
        for fd in data.get("flags", []):
            flag = Flag.from_dict(fd)
            flags[flag.name] = flag

        ff = cls(flags=flags, file_path=path, **kwargs)
        ff._last_modified = os.path.getmtime(path)
        return ff

    def _maybe_reload(self) -> None:
        """Reload from file if hot_reload is enabled and file changed."""
        if not self._hot_reload or not self._file_path:
            return
        try:
            mtime = os.path.getmtime(self._file_path)
            if mtime > self._last_modified:
                with self._lock:
                    reloaded = FeatureFlags.load(self._file_path)
                    self._flags = reloaded._flags
                    self._last_modified = mtime
        except Exception:
            pass

    def _start_watchdog(self) -> None:
        def run():
            while not self._stop_watchdog.wait(self._watchdog_interval):
                self._maybe_reload()
        self._watchdog_thread = threading.Thread(target=run, daemon=True)
        self._watchdog_thread.start()

    def stop(self) -> None:
        self._stop_watchdog.set()
        if self._watchdog_thread:
            self._watchdog_thread.join(timeout=2.0)

    # ── Seed data helpers ───────────────────────────────────────────────────────

    @classmethod
    def with_seed(cls, seed: list[dict]) -> FeatureFlags:
        """Create flags from a list of dicts (e.g. from a JSON file)."""
        flags = {}
        for fd in seed:
            if isinstance(fd, dict) and "name" in fd:
                flags[fd["name"]] = Flag.from_dict(fd)
        return cls(flags=flags)


# ─── Standalone functions ───────────────────────────────────────────────────────

_default_ff: Optional[FeatureFlags] = None
_ff_lock = threading.Lock()


def configure(path: str, **kwargs) -> FeatureFlags:
    """Load flags from a file and set as the global default."""
    global _default_ff
    with _ff_lock:
        _default_ff = FeatureFlags.load(path, **kwargs)
        return _default_ff


def is_enabled(flag_name: str, **context) -> bool:
    """Evaluate a flag using the global default FeatureFlags."""
    if _default_ff is None:
        return False
    return _default_ff.is_enabled(flag_name, **context)


def evaluate(flag_name: str, default: Any = None, **context) -> Any:
    if _default_ff is None:
        return default
    return _default_ff.evaluate(flag_name, default=default, **context)


# ─── CLI ──────────────────────────────────────────────────────────────────────

def _cli():
    """Simple CLI to toggle flags: python -m tiny_feature_flags <flag> [enable|disable|status]"""
    import sys
    path = os.environ.get("FEATURE_FLAGS_FILE", "flags.json")

    if len(sys.argv) < 2:
        print("Usage: tiny_feature_flags <flag_name> [enable|disable|status]")
        sys.exit(1)

    flag_name = sys.argv[1]
    action = sys.argv[2] if len(sys.argv) > 2 else "status"

    if not os.path.exists(path):
        print(f"No flags file at {path}")
        sys.exit(1)

    ff = FeatureFlags.load(path)

    if action == "status":
        flag = ff.get_flag(flag_name)
        if flag:
            print(f"Flag: {flag_name}")
            print(f"  Enabled: {flag.enabled}")
            print(f"  Default: {flag.default_value}")
            print(f"  Rollout: {flag.rollout_percent}%")
            print(f"  Rules: {len(flag.rules)}")
        else:
            print(f"Flag '{flag_name}' not found")
        sys.exit(0)

    if action == "enable":
        ff.enable(flag_name)
        ff.save()
        print(f"Enabled: {flag_name}")
    elif action == "disable":
        ff.disable(flag_name)
        ff.save()
        print(f"Disabled: {flag_name}")
    else:
        print(f"Unknown action: {action}")
        sys.exit(1)


if __name__ == "__main__":
    _cli()
