import base64
import hashlib
import json
import time
from pathlib import Path

from django.conf import settings
from openai import OpenAI

from core.models import ModelRequestLog


class ModelGatewayUnavailable(RuntimeError):
    pass


class ModelGatewayInvalidResponse(RuntimeError):
    pass


def _first_json_object(raw):
    cleaned = (raw or "").strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.removeprefix("```json").removeprefix("```")
        cleaned = cleaned.removesuffix("```").strip()
    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start >= 0 and end > start:
        try:
            parsed = json.loads(cleaned[start : end + 1])
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass
    raise ModelGatewayInvalidResponse("Model response did not contain a JSON object")


class ModelGateway:
    def __init__(self, *, client=None):
        self.provider = settings.AI_PROVIDER
        if self.provider == "groq":
            self.model = settings.GROQ_MODEL
            api_key = settings.GROQ_API_KEY
            base_url = settings.GROQ_BASE_URL
            self.supports_json_schema = False
        else:
            self.model = settings.AGENTROUTER_MODEL
            api_key = settings.AGENTROUTER_API_KEY
            base_url = settings.AGENTROUTER_BASE_URL
            self.supports_json_schema = True
        self._configured = bool(api_key)
        self.client = client
        if self.client is None and self._configured:
            self.client = OpenAI(
                api_key=api_key,
                base_url=base_url,
                timeout=settings.AI_REQUEST_TIMEOUT_SECONDS,
                max_retries=settings.AI_MAX_RETRIES,
            )

    @property
    def configured(self):
        return self._configured or self.client is not None

    def structured_completion(
        self,
        *,
        organization,
        actor,
        purpose,
        system_prompt,
        user_prompt,
        image_paths=None,
        json_schema=None,
    ):
        image_paths = list(image_paths or [])
        request_fingerprint = {
            "purpose": purpose,
            "model": self.model,
            "system": system_prompt,
            "user": user_prompt,
            "images": [self._file_hash(path) for path in image_paths],
        }
        request_hash = hashlib.sha256(
            json.dumps(request_fingerprint, sort_keys=True).encode()
        ).hexdigest()
        if not self.configured:
            ModelRequestLog.objects.create(
                organization=organization,
                actor=actor,
                purpose=purpose,
                model_name=self.model,
                status=ModelRequestLog.Status.UNAVAILABLE,
                request_hash=request_hash,
                error_code="API_KEY_NOT_CONFIGURED",
            )
            raise ModelGatewayUnavailable("AgentRouter API key is not configured")

        content = [{"type": "text", "text": user_prompt}]
        for path in image_paths:
            mime = "image/png" if str(path).lower().endswith(".png") else "image/jpeg"
            encoded = base64.b64encode(Path(path).read_bytes()).decode("ascii")
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime};base64,{encoded}"},
                }
            )
        response_format = {"type": "json_object"}
        if json_schema and self.supports_json_schema:
            response_format = {
                "type": "json_schema",
                "json_schema": {
                    "name": purpose.replace(".", "_")[:64],
                    "strict": True,
                    "schema": json_schema,
                },
            }
        elif json_schema:
            system_prompt = (
                f"{system_prompt}\nThe JSON object must match this schema exactly: "
                f"{json.dumps(json_schema, separators=(',', ':'))}"
            )
        started = time.monotonic()
        try:
            request_options = {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": content},
                ],
                "temperature": 0,
                "response_format": response_format,
            }
            if self.provider == "groq":
                request_options["max_tokens"] = 1800
            else:
                request_options["max_completion_tokens"] = 1800
            response = self.client.chat.completions.create(
                **request_options,
            )
            raw = response.choices[0].message.content or ""
            result = _first_json_object(raw)
            usage = getattr(response, "usage", None)
            ModelRequestLog.objects.create(
                organization=organization,
                actor=actor,
                purpose=purpose,
                model_name=self.model,
                provider_request_id=getattr(response, "id", "") or "",
                status=ModelRequestLog.Status.SUCCEEDED,
                input_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
                output_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
                latency_ms=int((time.monotonic() - started) * 1000),
                request_hash=request_hash,
            )
            return result, getattr(response, "id", "") or ""
        except Exception as exc:
            ModelRequestLog.objects.create(
                organization=organization,
                actor=actor,
                purpose=purpose,
                model_name=self.model,
                status=ModelRequestLog.Status.FAILED,
                latency_ms=int((time.monotonic() - started) * 1000),
                request_hash=request_hash,
                error_code=type(exc).__name__[:100],
            )
            raise

    @staticmethod
    def _file_hash(path):
        digest = hashlib.sha256()
        with Path(path).open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()
