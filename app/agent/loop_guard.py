"""Run 内重复链路与信息增量护栏。"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from threading import RLock
import time
from typing import Any

from .. import config


@dataclass(frozen=True)
class CycleGuardDecision:
    reason: str
    pattern_length: int
    repeat_count: int
    information_gain: bool


class RunCycleGuard:
    """只保存单次 Agent Run 的调用链与已见证据，不跨 Run 累积。"""

    def __init__(self) -> None:
        self._history: list[tuple[str, str]] = []
        self._seen_evidence_items: set[str] = set()

    @staticmethod
    def _canonical(value: Any) -> Any:
        if isinstance(value, str):
            return re.sub(r"\s+", " ", value).strip().lower()
        if isinstance(value, list):
            return [RunCycleGuard._canonical(item) for item in value]
        if isinstance(value, dict):
            return {str(key): RunCycleGuard._canonical(value[key]) for key in sorted(value)}
        return value

    @classmethod
    def _signature(cls, tool: str, payload: dict[str, Any]) -> str:
        canonical = json.dumps(cls._canonical(payload), ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        return f"{tool}|{canonical}"

    @staticmethod
    def _result_status(result: dict[str, Any]) -> str:
        if result.get("ok") is False or result.get("error"):
            return "tool_error"
        data = result.get("data") if isinstance(result.get("data"), dict) else {}
        count = data.get("count", result.get("count"))
        if count == 0 or data.get("found") is False:
            return "empty"
        return "success"

    @staticmethod
    def _evidence_items(tool: str, result: dict[str, Any]) -> set[str]:
        data = result.get("data") if isinstance(result.get("data"), dict) else {}
        if tool == "recall_troubleshooting_strategy":
            samples = data.get("samples", result.get("samples", []))
            return {f"strategy:{item['id']}" for item in samples if isinstance(item, dict) and item.get("id") is not None}
        if tool == "test_sop_search":
            sop = data.get("sop", result.get("sop"))
            if isinstance(sop, dict) and sop.get("id"):
                return {f"sop:{sop['id']}"}
            return set()
        if tool == "defect_case_search":
            defects = data.get("defects", result.get("defects", []))
            return {f"case:{item['id']}" for item in defects if isinstance(item, dict) and item.get("id") is not None}
        return set()

    @classmethod
    def _evidence_signature(cls, tool: str, result: dict[str, Any], result_count: int) -> tuple[str, set[str]]:
        status = cls._result_status(result)
        items = cls._evidence_items(tool, result)
        summary = json.dumps({"status": status, "count": result_count, "items": sorted(items)}, separators=(",", ":"))
        digest = hashlib.sha256(summary.encode("utf-8")).hexdigest()[:16]
        return f"{status}|{result_count}|{digest}", items

    def record(self, tool: str, payload: dict[str, Any], result: dict[str, Any],
               result_count: int) -> CycleGuardDecision | None:
        call_signature = self._signature(tool, payload)
        evidence_signature, items = self._evidence_signature(tool, result, result_count)
        self._history.append((call_signature, evidence_signature))
        new_items = items - self._seen_evidence_items
        self._seen_evidence_items.update(items)

        repeat_count = config.LOOP_CYCLE_REPEAT_THRESHOLD
        for pattern_length in range(1, config.LOOP_CYCLE_MAX_PATTERN_LENGTH + 1):
            window_size = pattern_length * repeat_count
            if len(self._history) < window_size:
                continue
            window = self._history[-window_size:]
            signature_pattern = [item[0] for item in window[:pattern_length]]
            chunks = [window[index:index + pattern_length] for index in range(0, window_size, pattern_length)]
            if not all([item[0] for item in chunk] == signature_pattern for chunk in chunks):
                continue

            evidence_pattern = [item[1] for item in chunks[0]]
            information_gain = bool(new_items) or any(
                [item[1] for item in chunk] != evidence_pattern for chunk in chunks[1:]
            )
            self._history.clear()
            return CycleGuardDecision(
                reason="cycle_information_gain" if information_gain else "repeated_tool_cycle_without_new_evidence",
                pattern_length=pattern_length,
                repeat_count=repeat_count,
                information_gain=information_gain,
            )
        return None


@dataclass(frozen=True)
class CircuitDecision:
    allowed: bool
    state: str
    retry_after_seconds: int = 0


@dataclass(frozen=True)
class CircuitEvent:
    reason: str
    consecutive_failures: int


@dataclass
class _CircuitState:
    consecutive_failures: int = 0
    open_until: float = 0
    half_open_probe_active: bool = False


class ToolCircuitBreaker:
    """仅用于 Demo 演示的进程内工具熔断器，状态跨 Run 保留。"""

    def __init__(self) -> None:
        self._states: dict[str, _CircuitState] = {}
        self._lock = RLock()

    @staticmethod
    def enabled() -> bool:
        return config.DEMO_TOOL_CIRCUIT_BREAKER_ENABLED

    def should_inject_failure(self, tool: str) -> bool:
        return self.enabled() and tool in config.DEMO_TOOL_CIRCUIT_FAIL_TOOLS

    def before_call(self, tool: str) -> CircuitDecision:
        if not self.enabled():
            return CircuitDecision(allowed=True, state="disabled")
        now = time.monotonic()
        with self._lock:
            state = self._states.setdefault(tool, _CircuitState())
            if state.open_until > now:
                return CircuitDecision(
                    allowed=False,
                    state="open",
                    retry_after_seconds=max(1, int(state.open_until - now)),
                )
            if state.open_until:
                if state.half_open_probe_active:
                    return CircuitDecision(allowed=False, state="half_open")
                state.half_open_probe_active = True
                return CircuitDecision(allowed=True, state="half_open")
            return CircuitDecision(allowed=True, state="closed")

    def record(self, tool: str, success: bool) -> CircuitEvent | None:
        if not self.enabled():
            return None
        with self._lock:
            state = self._states.setdefault(tool, _CircuitState())
            was_half_open = state.half_open_probe_active
            if success:
                state.consecutive_failures = 0
                state.open_until = 0
                state.half_open_probe_active = False
                return CircuitEvent("tool_circuit_recovered", 0) if was_half_open else None

            state.consecutive_failures += 1
            if was_half_open or state.consecutive_failures >= config.DEMO_TOOL_CIRCUIT_FAILURE_THRESHOLD:
                state.open_until = time.monotonic() + config.DEMO_TOOL_CIRCUIT_COOLDOWN_SECONDS
                state.half_open_probe_active = False
                return CircuitEvent(
                    "tool_circuit_reopened" if was_half_open else "tool_circuit_opened",
                    state.consecutive_failures,
                )
            return None
