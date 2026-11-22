import base64

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.db import connection
from django.test import TestCase
from rest_framework.test import APIClient

from core.models import (
    Asset,
    AssetAssignment,
    AssetCandidate,
    AuditEvent,
    RecommendationTemplate,
    StaffWorkProfile,
    Task,
)
from core.services.candidates import approve_asset_candidate
from core.services.habits import generate_energy_habits


class SynchronizedApiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_demo", verbosity=0)
        cls.maya = get_user_model().objects.get(username="maya")
        cls.admin = get_user_model().objects.get(username="energia-admin")

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(self.maya)

    def test_backend_uses_postgresql_and_health_is_public(self):
        self.assertEqual(connection.vendor, "postgresql")
        anonymous = APIClient()
        response = anonymous.get("/api/v1/health/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, {"status": "ok", "database": "postgresql"})

    def test_jwt_login_returns_access_and_refresh_tokens(self):
        anonymous = APIClient()
        response = anonymous.post(
            "/api/v1/auth/token/",
            {"username": "maya", "password": "energia-demo"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("access", response.data)
        self.assertIn("refresh", response.data)

    def test_work_profile_updates_are_persisted_and_audited(self):
        profile_response = self.client.get("/api/v1/me/work-profile/")
        self.assertEqual(profile_response.status_code, 200)
        utilities = next(
            item
            for item in profile_response.data["department_options"]
            if item["name"] == "Utilities"
        )
        response = self.client.put(
            "/api/v1/me/work-profile/",
            {
                "department_id": utilities["id"],
                "job_role": "Production operator",
                "shift": "day",
                "activity_codes": ["line_startup", "quality_checks"],
                "custom_activities": [
                    {
                        "id": "local-custom-id",
                        "title": "Inspect steam trap station",
                        "description": "Check during the afternoon round",
                        "selected": True,
                    }
                ],
                "notes": "Synced from Android",
                "is_complete": True,
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(response.data["department"]["is_pending"])
        self.assertEqual(response.data["department"]["name"], "Utilities")
        self.assertEqual(len(response.data["custom_activities"]), 1)
        profile = StaffWorkProfile.objects.get(user=self.maya)
        self.assertEqual(profile.department.name, "Packaging")
        self.assertEqual(profile.pending_department.name, "Utilities")
        self.assertTrue(
            AuditEvent.objects.filter(action="work_profile.updated", actor=self.maya).exists()
        )

    def test_assets_are_scoped_searchable_and_paginated(self):
        response = self.client.get("/api/v1/assets/?search=HS-03")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["id"], "heat-sealer-hs03")
        response = self.client.get("/api/v1/assets/?department=Utilities")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 3)

        duplicate_response = self.client.post(
            "/api/v1/assets/duplicate-suggestions/",
            {
                "name": "Cartoner",
                "type": "Packaging machine",
                "location": "Packaging line 2",
                "identifier": "CTN-12-8841",
            },
            format="json",
        )
        self.assertEqual(duplicate_response.status_code, 200)
        self.assertEqual(
            duplicate_response.data["results"][0]["asset_id"], "cartoner-c12"
        )
        self.assertEqual(duplicate_response.data["results"][0]["score"], 1.0)

    def test_equipment_assignments_replace_server_state_and_are_audited(self):
        response = self.client.put(
            "/api/v1/me/equipment/",
            {
                "assignments": [
                    {
                        "asset_id": "heat-sealer-hs03",
                        "interactions": ["operate", "inspect"],
                    }
                ]
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(len(response.data), 1)
        active = AssetAssignment.objects.filter(user=self.maya, is_active=True)
        self.assertEqual(active.count(), 1)
        self.assertEqual(active.get().asset.client_id, "heat-sealer-hs03")
        self.assertTrue(
            AuditEvent.objects.filter(
                action="asset_assignments.updated", actor=self.maya
            ).exists()
        )

    def test_candidate_submission_is_visible_to_admin_and_not_an_assignment(self):
        png = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
        )
        response = self.client.post(
            "/api/v1/asset-candidates/",
            {
                "name": "Case erector CE-09",
                "type": "Packaging machine",
                "location": "Packaging line 4",
                "identifier": "CE09-24018",
                "photo": SimpleUploadedFile("candidate.png", png, content_type="image/png"),
            },
            format="multipart",
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["status"], "pending")
        self.assertTrue(
            AssetCandidate.objects.filter(name="Case erector CE-09").exists()
        )
        self.assertFalse(
            AssetAssignment.objects.filter(asset__name="Case erector CE-09").exists()
        )

        admin_client = APIClient()
        admin_client.force_authenticate(self.admin)
        admin_response = admin_client.get("/api/v1/asset-candidates/")
        self.assertEqual(admin_response.status_code, 200)
        names = [item["name"] for item in admin_response.data["results"]]
        self.assertIn("Case erector CE-09", names)

        candidate = AssetCandidate.objects.get(name="Case erector CE-09")
        approved_asset = approve_asset_candidate(
            candidate=candidate, reviewer=self.admin
        )
        self.assertIsNotNone(approved_asset)
        candidate.refresh_from_db()
        self.assertEqual(candidate.review_status, AssetCandidate.ReviewStatus.APPROVED)
        self.assertTrue(Asset.objects.filter(id=approved_asset.id).exists())
        self.assertTrue(
            AssetAssignment.objects.filter(
                user=self.maya, asset=approved_asset, is_active=True
            ).exists()
        )
        self.assertTrue(
            AuditEvent.objects.filter(
                action="asset_candidate.approved", actor=self.admin
            ).exists()
        )

    def test_tasks_come_from_published_server_rule_snapshots(self):
        response = self.client.get("/api/v1/me/tasks/")
        self.assertEqual(response.status_code, 200, response.data)
        statuses = {item["status"] for item in response.data}
        self.assertTrue({"due", "completed", "overdue", "waiting_review"} <= statuses)
        anomaly = next(
            item for item in response.data if item["rule_id"] == "RULE-EVT-0007"
        )
        self.assertEqual(anomaly["asset_id"], "heat-sealer-hs03")
        self.assertTrue(anomaly["event_triggered"])
        task = Task.objects.get(id=anomaly["id"])
        self.assertEqual(task.rule_version_snapshot, task.rule_version.version)
        self.assertEqual(task.source_title, task.rule_version.source_title)

    def test_energy_habits_are_personalized_and_locked_safety_wins(self):
        response = self.client.get("/api/v1/me/tasks/")
        self.assertEqual(response.status_code, 200, response.data)
        habits = [item for item in response.data if item["task_kind"] == "energy_habit"]
        self.assertEqual(len(habits), 6)
        self.assertIn("DEMO-CLASSROOM-001", {item["rule_id"] for item in habits})
        self.assertNotIn(
            "HABIT-SHUTDOWN-004", {item["rule_id"] for item in habits}
        )
        self.assertTrue(all(item["recommendation_owner"] for item in habits))
        self.assertTrue(all(item["pass_condition"] for item in habits))
        self.assertTrue(all(item["citation"]["source_title"] for item in habits))

        audit = AuditEvent.objects.filter(action="energy_habits.generated").latest(
            "created_at"
        )
        exclusions = audit.metadata["excluded_by_safety"]
        self.assertEqual(len(exclusions), 2)
        self.assertTrue(
            all(
                item["constraints"] == ["SAFETY-PKG-LOCKED-01"]
                for item in exclusions
            )
        )

        snapshot_task = Task.objects.filter(
            assignee=self.maya,
            task_kind=Task.Kind.ENERGY_HABIT,
            rule_code_snapshot="HABIT-IDLE-001",
        ).first()
        original_instruction = snapshot_task.instruction
        template = RecommendationTemplate.objects.get(
            organization=snapshot_task.organization,
            code="HABIT-IDLE-001",
            version=1,
        )
        template.instruction = "Changed after task publication"
        template.save(update_fields=["instruction"])
        snapshot_task.refresh_from_db()
        self.assertEqual(snapshot_task.instruction, original_instruction)

        AssetAssignment.objects.filter(
            user=self.maya, asset__client_id="cartoner-c12"
        ).update(is_active=False)
        generate_energy_habits(user=self.maya)
        self.assertFalse(
            Task.objects.filter(
                assignee=self.maya,
                task_kind=Task.Kind.ENERGY_HABIT,
                asset__client_id="cartoner-c12",
            ).exclude(status=Task.Status.CANCELLED).exists()
        )

    def test_staff_cannot_see_another_users_candidates(self):
        other = get_user_model().objects.create_user("other-staff", password="test-pass")
        # No membership means object scope is denied rather than leaking an empty/all list.
        other_client = APIClient()
        other_client.force_authenticate(other)
        response = other_client.get("/api/v1/asset-candidates/")
        self.assertEqual(response.status_code, 403)
