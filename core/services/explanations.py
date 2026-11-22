from core.models import TaskExplanation
from core.services.model_gateway import ModelGateway


EXPLANATION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "plain_instruction": {"type": "string"},
        "why_summary": {"type": "string"},
        "evidence_guidance": {"type": "string"},
        "safety_summary": {"type": "string"},
    },
    "required": [
        "plain_instruction",
        "why_summary",
        "evidence_guidance",
        "safety_summary",
    ],
}


def _bounded(value, maximum=1000):
    return " ".join(str(value or "").split())[:maximum]


def deterministic_explanation(task):
    reasons = ", ".join(task.why_assigned)
    return {
        "plain_instruction": task.instruction,
        "why_summary": f"Assigned because: {reasons}.",
        "evidence_guidance": (
            f"Required evidence: {task.evidence_requirement}. "
            f"Pass condition: {task.pass_condition}"
        ),
        "safety_summary": (
            "Follow the approved safety, production, and manufacturer procedure. "
            "Do not open, reset, repair, isolate, or change protected settings unless the task explicitly cites an authorized procedure."
        ),
    }


def get_or_create_task_explanation(*, task, actor, gateway=None, force=False):
    if not force:
        existing = TaskExplanation.objects.filter(task=task).first()
        if existing:
            return existing
    fallback = deterministic_explanation(task)
    gateway = gateway or ModelGateway()
    result = None
    if gateway.configured:
        system_prompt = (
            "You simplify one already-selected Energia task. Use only supplied fields. "
            "Do not invent obligations, technical values, diagnoses, permissions, or safety steps. "
            "Preserve the pass condition and evidence requirement. Return strict JSON."
        )
        user_prompt = (
            f"Task title: {task.title}\n"
            f"Instruction: {task.instruction}\n"
            f"Why assigned: {task.why_assigned}\n"
            f"Evidence: {task.evidence_requirement}\n"
            f"Pass condition: {task.pass_condition}\n"
            f"Source: {task.source_title} {task.source_version}, {task.source_section}\n"
            f"Recommendation owner: {task.recommendation_owner or 'not applicable'}\n"
            "Write concise employee-facing fields without adding facts."
        )
        try:
            result, _ = gateway.structured_completion(
                organization=task.organization,
                actor=actor,
                purpose="task.explanation",
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                json_schema=EXPLANATION_SCHEMA,
            )
            if not all(
                _bounded(result.get(field))
                for field in (
                    "plain_instruction",
                    "why_summary",
                    "evidence_guidance",
                    "safety_summary",
                )
            ):
                result = None
        except Exception:
            result = None
    values = result or fallback
    explanation, _ = TaskExplanation.objects.update_or_create(
        task=task,
        defaults={
            "plain_instruction": _bounded(values.get("plain_instruction")),
            "why_summary": _bounded(values.get("why_summary")),
            "evidence_guidance": _bounded(values.get("evidence_guidance")),
            "safety_summary": _bounded(values.get("safety_summary")),
            "ai_simplified": bool(result),
            "model_name": gateway.model if result else "",
            "model_version": gateway.model if result else "",
        },
    )
    return explanation
