import re

from core.models import Asset


def _normalize(value):
    return re.sub(r"[^a-z0-9]+", " ", (value or "").lower()).strip()


def _name_similarity(first, second):
    if not first or not second:
        return 0.0
    if first == second:
        return 1.0
    if first in second or second in first:
        return 0.86
    first_tokens = {token for token in first.split() if len(token) > 1}
    second_tokens = {token for token in second.split() if len(token) > 1}
    if not first_tokens or not second_tokens:
        return 0.0
    return len(first_tokens & second_tokens) / len(first_tokens | second_tokens)


def find_asset_duplicates(*, organization, name, asset_type, location, identifier):
    normalized_name = _normalize(name)
    normalized_type = _normalize(asset_type)
    normalized_location = _normalize(location)
    normalized_identifier = _normalize(identifier)
    matches = []
    assets = Asset.objects.filter(
        organization=organization, status=Asset.Status.ACTIVE
    ).select_related("department")
    for asset in assets:
        reasons = []
        exact_identifier = bool(normalized_identifier) and normalized_identifier in {
            _normalize(asset.code),
            _normalize(asset.serial_number),
            _normalize(str(asset.qr_identifier)),
        }
        if exact_identifier:
            reasons.append("Same asset code, serial number, or QR identifier")
        name_score = _name_similarity(normalized_name, _normalize(asset.name))
        if name_score >= 0.6:
            reasons.append("Similar equipment name")
        same_type = bool(normalized_type) and normalized_type == _normalize(asset.asset_type)
        if same_type:
            reasons.append("Same equipment type")
        asset_location = _normalize(asset.location)
        same_location = bool(normalized_location) and (
            normalized_location == asset_location
            or normalized_location in asset_location
            or asset_location in normalized_location
        )
        if same_location:
            reasons.append("Same location")
        score = (
            1.0
            if exact_identifier
            else (name_score * 0.7) + (0.12 if same_type else 0) + (0.18 if same_location else 0)
        )
        if score >= 0.48:
            matches.append(
                {
                    "asset_id": asset.client_id,
                    "name": asset.name,
                    "asset_code": asset.code,
                    "type": asset.asset_type,
                    "department": asset.department.name,
                    "location": asset.location,
                    "serial_number": asset.serial_number,
                    "qr_value": f"energia://asset/{asset.qr_identifier}",
                    "score": round(min(score, 1.0), 4),
                    "reasons": reasons,
                }
            )
    return sorted(matches, key=lambda item: item["score"], reverse=True)[:3]
