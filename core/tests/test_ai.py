import io
import json
import subprocess
import tempfile
from pathlib import Path
from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone
from PIL import Image
from rest_framework.test import APIClient

from core.models import (
    AIJob,
    Evidence,
    EvidenceFrame,
    ModelRequestLog,
    Task,
    TaskExplanation,
)
from core.services.ai_jobs import process_next_job
from core.services.explanations import get_or_create_task_explanation
from core.services.model_gateway import ModelGateway


class FakeChatCompletions:
    def __init__(self, payload):
        self.payload = payload

    def create(self, **kwargs):
        return SimpleNamespace(
            id="fake-request-123",
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=json.dumps(self.payload))
                )
            ],
            usage=SimpleNamespace(prompt_tokens=120, completion_tokens=45),
        )


class FakeModelClient:
    def __init__(self, payload):
        self.chat = SimpleNamespace(completions=FakeChatCompletions(payload))


def fake_gateway(payload):
    return ModelGateway(client=FakeModelClient(payload))


@override_settings(AI_PROVIDER="agentrouter", AGENTROUTER_MODEL="gpt-6.1-sol")
class AIFeatureTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_demo", verbosity=0)
        cls.maya = get_user_model().objects.get(username="maya")

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(self.maya)

    def test_task_explanation_is_grounded_and_model_request_is_logged(self):
        task = Task.objects.filter(
            assignee=self.maya,
            task_kind=Task.Kind.ENERGY_HABIT,
        ).first()
        gateway = fake_gateway(
            {
                "plain_instruction": "Observe the approved idle indicator; do not change protected controls.",
                "why_summary": "Selected for your assigned packaging machine and operate interaction.",
                "evidence_guidance": "Answer yes or no and add a note if the state differs.",
                "safety_summary": "Production and manufacturer procedures take priority.",
            }
        )
        explanation = get_or_create_task_explanation(
            task=task, actor=self.maya, gateway=gateway, force=True
        )
        self.assertTrue(explanation.ai_simplified)
        self.assertEqual(explanation.model_name, "gpt-6.1-sol")
        self.assertIn("protected controls", explanation.plain_instruction)
        log = ModelRequestLog.objects.get(purpose="task.explanation")
        self.assertEqual(log.status, ModelRequestLog.Status.SUCCEEDED)
        self.assertEqual(log.provider_request_id, "fake-request-123")
        self.assertEqual(log.input_tokens, 120)

    def test_explanation_endpoint_has_deterministic_no_key_fallback(self):
        task = Task.objects.filter(assignee=self.maya).first()
        response = self.client.get(f"/api/v1/me/tasks/{task.id}/explanation/")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertFalse(response.data["ai_simplified"])
        self.assertIn(task.evidence_requirement, response.data["evidence_guidance"])
        self.assertTrue(TaskExplanation.objects.filter(task=task).exists())

    def test_photo_evidence_is_queued_idempotently_and_verified(self):
        task = Task.objects.filter(
            assignee=self.maya,
            task_kind=Task.Kind.ENERGY_HABIT,
            event_triggered=False,
        ).first()
        image_buffer = io.BytesIO()
        Image.new("RGB", (800, 600), color=(30, 30, 28)).save(
            image_buffer, format="JPEG"
        )
        image_bytes = image_buffer.getvalue()
        payload = {
            "media_type": "photo",
            "captured_at": timezone.now().isoformat(),
            "idempotency_key": "photo-test-001",
            "file": SimpleUploadedFile(
                "evidence.jpg", image_bytes, content_type="image/jpeg"
            ),
        }
        response = self.client.post(
            f"/api/v1/me/tasks/{task.id}/evidence/", payload, format="multipart"
        )
        self.assertEqual(response.status_code, 201, response.data)
        evidence_id = response.data["id"]
        self.assertEqual(response.data["status"], Evidence.Status.UPLOADED)
        self.assertTrue(AIJob.objects.filter(evidence_id=evidence_id).exists())

        duplicate = self.client.post(
            f"/api/v1/me/tasks/{task.id}/evidence/",
            {
                **payload,
                "file": SimpleUploadedFile(
                    "evidence.jpg", image_bytes, content_type="image/jpeg"
                ),
            },
            format="multipart",
        )
        self.assertEqual(duplicate.status_code, 200)
        self.assertEqual(duplicate.data["id"], evidence_id)

        gateway = fake_gateway(
            {
                "evidence_quality": "adequate",
                "visible_summary": "The accessible machine indicator is visible and appears in the documented idle state.",
                "task_consistency": "consistent",
                "potential_issues": [],
                "visible_readings": {},
                "confidence": 0.94,
                "cited_frame_indexes": [],
            }
        )
        job = process_next_job(gateway=gateway)
        self.assertEqual(job.status, AIJob.Status.COMPLETED)
        detail = self.client.get(f"/api/v1/evidence/{evidence_id}/")
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.data["status"], Evidence.Status.VERIFIED)
        self.assertFalse(detail.data["verification_result"]["needs_human_review"])
        self.assertEqual(
            detail.data["verification_result"]["model_name"], "gpt-6.1-sol"
        )

    def test_video_uses_sampled_frames_and_always_requires_human_review(self):
        task = Task.objects.filter(
            assignee=self.maya,
            asset__isnull=False,
            status=Task.Status.DUE,
        ).first()
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "clip.mp4"
            subprocess.run(
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-f",
                    "lavfi",
                    "-i",
                    "color=c=blue:s=640x480:d=3",
                    "-pix_fmt",
                    "yuv420p",
                    "-y",
                    str(path),
                ],
                check=True,
                timeout=30,
            )
            video_bytes = path.read_bytes()
        response = self.client.post(
            f"/api/v1/me/tasks/{task.id}/evidence/",
            {
                "media_type": "video",
                "captured_at": timezone.now().isoformat(),
                "idempotency_key": "video-test-001",
                "file": SimpleUploadedFile(
                    "clip.mp4", video_bytes, content_type="video/mp4"
                ),
            },
            format="multipart",
        )
        self.assertEqual(response.status_code, 201, response.data)
        gateway = fake_gateway(
            {
                "evidence_quality": "adequate",
                "visible_summary": "Sequential frames show the exterior of the assigned machine.",
                "task_consistency": "consistent",
                "potential_issues": [],
                "visible_readings": {},
                "confidence": 0.96,
                "cited_frame_indexes": [0, 2],
            }
        )
        process_next_job(gateway=gateway)
        evidence = Evidence.objects.get(id=response.data["id"])
        self.assertEqual(evidence.status, Evidence.Status.HUMAN_REVIEW)
        self.assertGreaterEqual(EvidenceFrame.objects.filter(evidence=evidence).count(), 2)
        result = evidence.verification_result
        self.assertTrue(result.needs_human_review)
        self.assertTrue(result.cited_frames)
