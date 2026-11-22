import hashlib
import json
import mimetypes
import subprocess
import tempfile
from pathlib import Path

from django.core.files.base import ContentFile
from PIL import Image, UnidentifiedImageError

from core.models import Evidence, EvidenceFrame, RuleVersion, VerificationResult
from core.services.model_gateway import ModelGateway


MAX_PHOTO_BYTES = 12 * 1024 * 1024
MAX_VIDEO_BYTES = 50 * 1024 * 1024
MAX_VIDEO_SECONDS = 60
MAX_VIDEO_FRAMES = 5
SUPPORTED_PHOTO_TYPES = {"image/jpeg", "image/png", "image/webp"}
SUPPORTED_VIDEO_TYPES = {"video/mp4", "video/quicktime", "video/webm"}

VERIFICATION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "evidence_quality": {
            "type": "string",
            "enum": ["adequate", "insufficient"],
        },
        "visible_summary": {"type": "string"},
        "task_consistency": {
            "type": "string",
            "enum": ["consistent", "inconsistent", "uncertain"],
        },
        "potential_issues": {
            "type": "array",
            "items": {"type": "string"},
            "maxItems": 8,
        },
        "visible_readings": {
            "type": "object",
            "additionalProperties": {"type": "string"},
        },
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "cited_frame_indexes": {
            "type": "array",
            "items": {"type": "integer", "minimum": 0},
            "maxItems": 5,
        },
    },
    "required": [
        "evidence_quality",
        "visible_summary",
        "task_consistency",
        "potential_issues",
        "visible_readings",
        "confidence",
        "cited_frame_indexes",
    ],
}


class EvidenceValidationError(ValueError):
    pass


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def validate_uploaded_media(uploaded_file, media_type):
    size = uploaded_file.size
    content_type = (uploaded_file.content_type or "").lower()
    guessed_type = mimetypes.guess_type(uploaded_file.name)[0] or ""
    mime_type = content_type or guessed_type
    if media_type == Evidence.MediaType.PHOTO:
        if size > MAX_PHOTO_BYTES:
            raise EvidenceValidationError("Photo exceeds the 12 MB limit")
        if mime_type not in SUPPORTED_PHOTO_TYPES:
            raise EvidenceValidationError("Unsupported photo format")
    elif media_type == Evidence.MediaType.VIDEO:
        if size > MAX_VIDEO_BYTES:
            raise EvidenceValidationError("Video exceeds the 50 MB limit")
        if mime_type not in SUPPORTED_VIDEO_TYPES:
            raise EvidenceValidationError("Unsupported video format")
    else:
        raise EvidenceValidationError("Unsupported evidence media type")
    return mime_type


def _photo_checks(path):
    checks = {"file_readable": False, "width": 0, "height": 0, "minimum_resolution": False}
    try:
        with Image.open(path) as image:
            image.verify()
        with Image.open(path) as image:
            checks.update(
                {
                    "file_readable": True,
                    "width": image.width,
                    "height": image.height,
                    "minimum_resolution": image.width >= 640 and image.height >= 480,
                }
            )
    except (UnidentifiedImageError, OSError):
        pass
    return checks


def _video_probe(path):
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=20,
    )
    if result.returncode != 0:
        raise EvidenceValidationError("Video could not be decoded")
    duration = float(json.loads(result.stdout)["format"]["duration"])
    if duration <= 0 or duration > MAX_VIDEO_SECONDS:
        raise EvidenceValidationError("Video duration must be between 1 and 60 seconds")
    return duration


def _sample_video_frames(evidence, path, duration):
    EvidenceFrame.objects.filter(evidence=evidence).delete()
    count = min(MAX_VIDEO_FRAMES, max(1, int(duration)))
    timestamps = [duration / 2] if count == 1 else [
        (duration - 0.1) * index / (count - 1) for index in range(count)
    ]
    frames = []
    with tempfile.TemporaryDirectory() as temp_dir:
        for index, timestamp in enumerate(timestamps):
            output = Path(temp_dir) / f"frame-{index:02d}.jpg"
            result = subprocess.run(
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-ss",
                    f"{timestamp:.3f}",
                    "-i",
                    str(path),
                    "-frames:v",
                    "1",
                    "-vf",
                    "scale='min(1280,iw)':-2",
                    "-q:v",
                    "3",
                    "-y",
                    str(output),
                ],
                capture_output=True,
                timeout=30,
            )
            if result.returncode != 0 or not output.exists():
                continue
            content = output.read_bytes()
            frame = EvidenceFrame(
                evidence=evidence,
                frame_index=index,
                timestamp_ms=int(timestamp * 1000),
                checksum_sha256=hashlib.sha256(content).hexdigest(),
            )
            frame.image.save(f"frame-{index:02d}.jpg", ContentFile(content), save=True)
            frames.append(frame)
    if not frames:
        raise EvidenceValidationError("No usable video frames were extracted")
    return frames


def _verification_prompt(task, media_type, frames):
    frame_context = ""
    if frames:
        frame_context = "\n".join(
            f"Image index {frame.frame_index} is video timestamp {frame.timestamp_ms} ms."
            for frame in frames
        )
    return (
        f"Task title: {task.title}\n"
        f"Instruction: {task.instruction}\n"
        f"Asset: {task.asset.name if task.asset else 'department-level'}\n"
        f"Evidence requirement: {task.evidence_requirement}\n"
        f"Pass condition: {task.pass_condition}\n"
        f"Media type: {media_type}\n"
        f"{frame_context}\n"
        "Assess only visible facts. Never claim internal wear, temperature without thermal data, "
        "an inaudible leak, completed repair, or safety compliance that is not visibly supported. "
        "Potential machine issues always require human confirmation. Return strict JSON."
    )


def _validated_ai_result(value):
    quality = value.get("evidence_quality")
    consistency = value.get("task_consistency")
    if quality not in {"adequate", "insufficient"}:
        raise EvidenceValidationError("AI returned invalid evidence quality")
    if consistency not in {"consistent", "inconsistent", "uncertain"}:
        raise EvidenceValidationError("AI returned invalid task consistency")
    confidence = float(value.get("confidence", 0))
    if not 0 <= confidence <= 1:
        raise EvidenceValidationError("AI returned invalid confidence")
    return {
        "evidence_quality": quality,
        "visible_summary": " ".join(str(value.get("visible_summary", "")).split())[:1200],
        "task_consistency": consistency,
        "potential_issues": [
            " ".join(str(item).split())[:300]
            for item in list(value.get("potential_issues", []))[:8]
        ],
        "visible_readings": dict(value.get("visible_readings", {})),
        "confidence": confidence,
        "cited_frame_indexes": [
            int(index) for index in list(value.get("cited_frame_indexes", []))[:5]
        ],
    }


def process_evidence(evidence, *, gateway=None):
    evidence.status = Evidence.Status.PROCESSING
    evidence.save(update_fields=["status", "updated_at"])
    task = Evidence.objects.select_related(
        "task__asset", "task__organization", "submitted_by"
    ).get(pk=evidence.pk).task
    path = evidence.file.path
    frames = []
    deterministic_checks = {
        "checksum_verified": sha256_file(path) == evidence.checksum_sha256,
        "mime_type": evidence.mime_type,
        "size_bytes": evidence.size_bytes,
    }
    try:
        if evidence.media_type == Evidence.MediaType.PHOTO:
            photo_checks = _photo_checks(path)
            deterministic_checks.update(photo_checks)
            image_paths = [path]
            if not photo_checks["file_readable"]:
                raise EvidenceValidationError("Photo could not be decoded")
        else:
            duration = _video_probe(path)
            deterministic_checks["duration_seconds"] = round(duration, 3)
            frames = _sample_video_frames(evidence, path, duration)
            image_paths = [frame.image.path for frame in frames]

        gateway = gateway or ModelGateway()
        if not gateway.configured:
            raise RuntimeError("AgentRouter is unavailable; queued for human review")
        result, request_id = gateway.structured_completion(
            organization=task.organization,
            actor=evidence.submitted_by,
            purpose="evidence.verification",
            system_prompt=(
                "You are Energia's evidence triage assistant. Assess visible evidence against the supplied task. "
                "Be factual, conservative, and structured. You do not authorize work, confirm engineering diagnoses, "
                "or override safety rules."
            ),
            user_prompt=_verification_prompt(task, evidence.media_type, frames),
            image_paths=image_paths,
            json_schema=VERIFICATION_SCHEMA,
        )
        parsed = _validated_ai_result(result)
        frame_by_index = {frame.frame_index: frame for frame in frames}
        cited_frames = [
            {
                "frame_index": index,
                "timestamp_ms": frame_by_index[index].timestamp_ms,
            }
            for index in parsed["cited_frame_indexes"]
            if index in frame_by_index
        ]
        issues = parsed["potential_issues"]
        minimum_quality = deterministic_checks.get("minimum_resolution", True)
        can_auto_verify = (
            evidence.media_type == Evidence.MediaType.PHOTO
            and minimum_quality
            and parsed["evidence_quality"] == "adequate"
            and parsed["task_consistency"] == "consistent"
            and not issues
            and parsed["confidence"] >= 0.9
            and task.urgency == RuleVersion.Urgency.ROUTINE
            and not task.event_triggered
        )
        if parsed["evidence_quality"] == "insufficient" or not minimum_quality:
            status = Evidence.Status.INSUFFICIENT
        elif issues or parsed["task_consistency"] == "inconsistent":
            status = Evidence.Status.POTENTIAL_PROBLEM
        elif can_auto_verify:
            status = Evidence.Status.VERIFIED
        else:
            status = Evidence.Status.HUMAN_REVIEW
        verification = VerificationResult.objects.create(
            evidence=evidence,
            status=status,
            factual_summary=parsed["visible_summary"],
            evidence_quality=parsed["evidence_quality"],
            confidence=parsed["confidence"],
            extracted_fields=parsed["visible_readings"],
            findings=issues,
            cited_frames=cited_frames,
            deterministic_checks=deterministic_checks,
            model_name=gateway.model,
            model_version=gateway.model,
            provider_request_id=request_id,
            needs_human_review=status != Evidence.Status.VERIFIED,
        )
    except Exception as exc:
        verification = VerificationResult.objects.create(
            evidence=evidence,
            status=Evidence.Status.HUMAN_REVIEW,
            factual_summary=(
                "Automated verification was unavailable or inconclusive. "
                "The submitted evidence is preserved for authorized human review."
            ),
            evidence_quality="unknown",
            confidence=0,
            findings=[],
            cited_frames=[],
            deterministic_checks={**deterministic_checks, "processing_error": type(exc).__name__},
            model_name=getattr(gateway, "model", "") if gateway else "",
            needs_human_review=True,
        )
    evidence.status = verification.status
    evidence.save(update_fields=["status", "updated_at"])
    return verification
