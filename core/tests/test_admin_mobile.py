import io
import json

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from PIL import Image, ImageDraw
from rest_framework.test import APIClient

from core.models import AIJob, Invoice, InvoiceScan, ModelRequestLog
from core.services.ai_jobs import process_next_job
from core.tests.test_ai import fake_gateway


@override_settings(AI_PROVIDER="agentrouter", AGENTROUTER_MODEL="gpt-6.1-sol")
class AdminMobileInvoiceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_demo", verbosity=0)
        User = get_user_model()
        cls.admin_user = User.objects.get(username="energia-admin")
        cls.maya = User.objects.get(username="maya")

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(self.admin_user)

    def _invoice_image(self):
        image = Image.new("RGB", (1600, 1200), "white")
        draw = ImageDraw.Draw(image)
        lines = [
            "NorthGrid Energy",
            "Account: CLASSROOM-001",
            "Billing period: 2026-10-01 to 2026-10-31",
            "Energy: 1250 kWh",
            "Maximum demand: 82.5 kW",
            "Total: 320.50 USD",
            "Tariff: EDU-1",
        ]
        for index, line in enumerate(lines):
            draw.text((120, 100 + index * 120), line, fill="black")
        output = io.BytesIO()
        image.save(output, format="JPEG", quality=90)
        return output.getvalue()

    def test_admin_context_and_schema_are_role_scoped(self):
        context = self.client.get("/api/v1/me/")
        self.assertEqual(context.status_code, 200)
        self.assertTrue(context.data["is_administrator"])
        self.assertEqual(context.data["role"], "admin")
        schemas = self.client.get("/api/v1/invoice-schemas/")
        self.assertEqual(schemas.status_code, 200)
        self.assertEqual(len(schemas.data), 1)

        staff = APIClient()
        staff.force_authenticate(self.maya)
        denied = staff.get("/api/v1/invoice-schemas/")
        self.assertEqual(denied.status_code, 403)

    def test_invoice_scan_is_ai_draft_until_mandatory_confirmation(self):
        schema = self.client.get("/api/v1/invoice-schemas/").data[0]
        context = self.client.get("/api/v1/me/").data
        response = self.client.post(
            "/api/v1/invoice-scans/",
            {
                "site_id": context["site"]["id"],
                "schema_id": schema["id"],
                "supplier": "NorthGrid Energy",
                "rotations": json.dumps([0]),
                "pages": SimpleUploadedFile(
                    "classroom-bill.jpg",
                    self._invoice_image(),
                    content_type="image/jpeg",
                ),
            },
            format="multipart",
        )
        self.assertEqual(response.status_code, 201, response.data)
        scan_id = response.data["id"]
        self.assertEqual(response.data["status"], InvoiceScan.Status.UPLOADED)
        self.assertFalse(Invoice.objects.exists())
        self.assertTrue(AIJob.objects.filter(invoice_scan_id=scan_id).exists())

        gateway = fake_gateway(
            {
                "fields": {
                    "supplier": "NorthGrid Energy",
                    "account_number": "CLASSROOM-001",
                    "billing_period_start": "2026-10-01",
                    "billing_period_end": "2026-10-31",
                    "energy_value": "1250",
                    "energy_unit": "kWh",
                    "maximum_demand_kw": "82.5",
                    "total_cost": "320.50",
                    "currency": "USD",
                    "tariff": "EDU-1",
                },
                "confidences": {
                    "billing_period_start": 0.96,
                    "billing_period_end": 0.96,
                    "energy_value": 0.93,
                    "energy_unit": 0.98,
                    "total_cost": 0.94,
                    "currency": 0.99,
                },
                "provenance": {
                    "energy_value": "both",
                    "total_cost": "vision",
                },
                "field_boxes": {
                    "energy_value": [0.1, 0.4, 0.4, 0.1],
                },
                "summary": "Electricity bill fields extracted for confirmation.",
            }
        )
        job = process_next_job(gateway=gateway)
        self.assertEqual(job.status, AIJob.Status.COMPLETED)
        scan = self.client.get(f"/api/v1/invoice-scans/{scan_id}/")
        self.assertEqual(scan.status_code, 200)
        self.assertEqual(scan.data["status"], InvoiceScan.Status.READY)
        self.assertEqual(scan.data["raw_fields"]["energy_value"], "1250")
        self.assertEqual(scan.data["model_name"], "gpt-6.1-sol")
        self.assertTrue(ModelRequestLog.objects.filter(purpose="invoice.extraction").exists())
        self.assertFalse(Invoice.objects.exists())

        confirmation = self.client.post(
            f"/api/v1/invoice-scans/{scan_id}/confirm/",
            {"corrected_fields": {"energy_value": "1251"}},
            format="json",
        )
        self.assertEqual(confirmation.status_code, 200, confirmation.data)
        self.assertEqual(confirmation.data["energy_value"], "1251.0000")
        invoice = Invoice.objects.get()
        self.assertEqual(str(invoice.energy_value), "1251.0000")
        self.assertEqual(invoice.raw_extraction["energy_value"], "1250")
        self.assertEqual(invoice.confirmed_values["energy_value"], "1251")
