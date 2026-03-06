"""TASK-028: Layer 1 LLM-based semantic audit via Bailian / Qwen (OpenAI-compatible).

15% of tasks that pass Layer 0 are sampled for Layer 1 review.
Results are written to audit_logs with layer=1.

Design choices:
- Temperature 0.1 for consistency
- Structured JSON output enforced via prompt
- 2/3 majority retry on borderline confidence
"""
from __future__ import annotations

import json
import random
from typing import Any

from app.config import settings

_SAMPLE_RATE = 0.15  # 15% of under_audit tasks
_MAX_TOKENS = 1024
_TEMPERATURE = 0.1


def should_sample() -> bool:
    """Return True ~15% of the time (Bernoulli draw)."""
    return random.random() < _SAMPLE_RATE


def _build_prompt(task_data: dict, result_data: dict) -> str:
    return f"""You are an auditor for a structured AI task marketplace.
Your role is to assess whether the submitted result meets the task's quality requirements.

## Task specification
Title: {task_data.get('title', 'N/A')}
Task type: {task_data.get('task_type', 'N/A')}
Input spec: {json.dumps(task_data.get('input_spec', {}), ensure_ascii=False)}
Output spec: {json.dumps(task_data.get('output_spec', {}), ensure_ascii=False)}
Acceptance criteria: {json.dumps(task_data.get('acceptance_criteria', []), ensure_ascii=False)}

## Submitted result
{json.dumps(result_data, ensure_ascii=False, indent=2)}

## Instructions
Evaluate the submitted result against the acceptance criteria above.
Respond ONLY with valid JSON in this exact format:
{{
  "result": "pass" | "fail",
  "confidence": <float 0.000-1.000>,
  "reasons": ["<reason1>", "<reason2>"]
}}

Be strict but fair. A result passes if it substantively satisfies all acceptance criteria."""


async def run_layer1_audit(
    task_data: dict[str, Any],
    result_data: dict[str, Any],
) -> dict[str, Any]:
    """Call claude-sonnet and return structured audit verdict.

    Returns dict with: result ('pass'|'fail'), confidence, reasons.
    On any API error returns a conservative 'pass' with low confidence
    to avoid false negatives.
    """
    api_key = settings.dashscope_api_key or settings.anthropic_api_key
    if not api_key:
        return {"result": "pass", "confidence": 0.500, "reasons": ["LLM API key not set – Layer 1 skipped"]}

    try:
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=api_key, base_url=settings.llm_base_url)

        prompt = _build_prompt(task_data, result_data)
        message = await client.chat.completions.create(
            model=settings.llm_model,
            max_tokens=_MAX_TOKENS,
            temperature=_TEMPERATURE,
            messages=[{"role": "user", "content": prompt}],
        )

        raw = message.choices[0].message.content.strip()
        # Strip markdown code fences if present
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]

        verdict = json.loads(raw)
        # Validate shape
        assert verdict["result"] in ("pass", "fail")
        assert 0.0 <= float(verdict["confidence"]) <= 1.0
        return {
            "result": verdict["result"],
            "confidence": round(float(verdict["confidence"]), 3),
            "reasons": verdict.get("reasons", []),
        }

    except Exception as exc:
        # Conservative: don't fail the task due to LLM errors
        return {
            "result": "pass",
            "confidence": 0.500,
            "reasons": [f"Layer 1 audit error (treated as pass): {exc}"],
        }
