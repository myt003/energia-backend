import hashlib
import uuid

from django.core.management import call_command
from django.urls import reverse
from rest_framework.test import APITestCase

from core.models import (
    CustomRule,
    Department,
    ESPDevice,
    Notification,
    Reading,
    ScoreSnapshot,
    Site,
    Task,
)


class AdminWorkspaceTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_demo", verbosity=0)
        from django.contrib.auth import get_user_model

        cls.admin = get_user_model().objects.get(username="energia-admin")
        cls.staff = get_user_model().objects.get(username="maya")

    def setUp(self):
        self.client.force_authenticate(self.admin)

    def test_admin_resources_are_organization_scoped(self):
        response = self.client.get(reverse("admin-resource-list", args=["readings"]))
        self.assertEqual(response.status_code, 200)
        self.assertGreaterEqual(response.data["count"], 12)
        self.assertTrue(response.data["results"])
        self.assertIn("canonical_value", response.data["results"][0])

    def test_admin_assets_resource_exposes_staff_qr_value(self):
        response = self.client.get(reverse("admin-resource-list", args=["assets"]))
        self.assertEqual(response.status_code, 200)
        result = response.data["results"][0]
        self.assertTrue(result["qr_value"].startswith("energia://asset/"))
        uuid.UUID(result["qr_value"].rsplit("/", 1)[-1])
        for resource in ("sites", "departments"):
            self.assertEqual(
                self.client.get(reverse("admin-resource-list", args=[resource])).status_code,
                200,
            )

    def test_admin_created_asset_qr_resolves_in_staff_workflow(self):
        organization = self.admin.memberships.first().organization
        site = Site.objects.filter(organization=organization).first()
        department = Department.objects.filter(site=site).first()
        created = self.client.post(
            reverse("admin-resource-list", args=["assets"]),
            {
                "client_id": "qr-workflow-test",
                "asset_code": "QR-WORKFLOW-001",
                "name": "QR workflow test compressor",
                "type": "Compressor",
                "site": site.id,
                "department": department.id,
                "location": "Plant floor",
                "serial_number": "QRWORKFLOW001",
            },
            format="json",
        )
        self.assertEqual(created.status_code, 201, created.data)
        qr_value = created.data["qr_value"]
        self.assertTrue(qr_value.startswith("energia://asset/"))

        self.client.force_authenticate(self.staff)
        resolved = self.client.get(reverse("asset-qr-resolve"), {"value": qr_value})
        self.assertEqual(resolved.status_code, 200)
        self.assertEqual(resolved.data["asset_code"], "QR-WORKFLOW-001")

    def test_chart_series_and_energy_flow_are_available(self):
        series = self.client.get(reverse("admin-chart-series") + "?days=365&group=month")
        self.assertEqual(series.status_code, 200)
        self.assertTrue(series.data["series"])
        self.assertIn("actual", series.data["series"][0])
        flow = self.client.get(reverse("admin-energy-flow") + "?days=365")
        self.assertEqual(flow.status_code, 200)
        self.assertTrue(flow.data["nodes"])
        self.assertTrue(flow.data["links"])

    def test_department_health_scores_are_persisted_and_explainable(self):
        response = self.client.get(reverse("admin-department-health"))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data)
        snapshot = ScoreSnapshot.objects.filter(
            department__isnull=False, user__isnull=True
        ).first()
        self.assertIsNotNone(snapshot)
        self.assertIn("inputs", dict(response.data[0]))
        self.assertIsNotNone(snapshot.energy_component)
        self.assertIsNotNone(snapshot.check_component)

    def test_rag_query_is_citation_grounded(self):
        response = self.client.post(
            reverse("admin-rag-query"),
            {"query": "What should operators do with compressed air isolation valves?", "use_model": False},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["grounded"])
        self.assertTrue(response.data["citations"])
        self.assertIn("citation", response.data["citations"][0])

    def test_rule_preview_and_publish_are_versioned(self):
        rule = CustomRule.objects.create(
            organization=self.admin.memberships.first().organization,
            code="TEST-RULE-PUBLISH",
            version=1,
            title="Test rule",
            definition={"title": "Test rule", "instruction": "Check the meter"},
            created_by=self.admin,
        )
        preview = self.client.post(
            reverse("admin-rule-preview"), {"rule_id": str(rule.id)}, format="json"
        )
        self.assertEqual(preview.status_code, 200)
        self.assertIn("matches", preview.data)
        published = self.client.post(reverse("admin-rule-publish", args=[rule.id]))
        self.assertEqual(published.status_code, 200)
        rule.refresh_from_db()
        self.assertEqual(rule.status, CustomRule.Status.PUBLISHED)

    def test_task_completion_creates_auditable_form_submission(self):
        task = Task.objects.filter(assignee=self.staff).first()
        self.assertIsNotNone(task)
        response = self.client.post(
            reverse("admin-task-complete", args=[task.id]),
            {
                "answers": {"step_1": "yes"},
                "readings": [{"value": 1234, "unit": "kWh"}],
                "idempotency_key": "admin-workspace-test",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        task.refresh_from_db()
        self.assertEqual(task.status, Task.Status.COMPLETED)

    def test_report_exports_json_csv_and_pdf(self):
        url = reverse("admin-report-generate")
        json_response = self.client.get(url)
        self.assertEqual(json_response.status_code, 200)
        score_keys = {
            (item["site"], item["department"]) for item in json_response.data["scores"]
        }
        self.assertEqual(len(score_keys), len(json_response.data["scores"]))
        self.assertGreater(json_response.data["data_quality"]["score_snapshots"], 0)
        csv_response = self.client.get(url + "?export=csv")
        self.assertEqual(csv_response.status_code, 200)
        self.assertIn(b"score", csv_response.content)
        self.assertIn(b"score_snapshots", csv_response.content)
        pdf_response = self.client.get(url + "?export=pdf")
        self.assertEqual(pdf_response.status_code, 200)
        self.assertTrue(pdf_response.content.startswith(b"%PDF"))

    def test_device_endpoint_requires_token_and_hides_employee_identifiers(self):
        device = ESPDevice.objects.first()
        url = reverse("device-department-summaries", args=[device.id])
        denied = self.client.get(url)
        self.assertEqual(denied.status_code, 403)
        token_hash = hashlib.sha256(b"energia-demo-device-token").hexdigest()
        self.assertEqual(device.token_hash, token_hash)
        self.client.credentials(HTTP_AUTHORIZATION="Device energia-demo-device-token")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["departments"])
        self.assertIn("overall_score", response.data)
        self.assertIn("top_employee", response.data)
        self.assertNotIn("user_id", str(response.data))

    def test_notifications_and_staff_history_are_available(self):
        self.assertGreater(Notification.objects.count(), 0)
        response = self.client.get(reverse("admin-staff-history", args=[self.staff.id]))
        self.assertEqual(response.status_code, 200)
        self.assertIn("tasks", response.data)
        self.assertIn("scores", response.data)

    def test_staff_cannot_access_admin_resources(self):
        self.client.force_authenticate(self.staff)
        response = self.client.get(reverse("admin-resource-list", args=["readings"]))
        self.assertEqual(response.status_code, 403)
