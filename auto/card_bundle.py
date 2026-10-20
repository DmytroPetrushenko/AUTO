"""Review and atomically save a complete card using the existing services."""

import hashlib

from sqlmodel import select

from auto.accounting import NormInput, ReadingInput, add_reading, preview_norm, save_norm
from auto.history import _digest, preview_vehicle
from auto.models import DocumentVersion, VehicleDocument, VehicleEquipment, VehicleNote
from auto.records import (
    DocumentInput,
    EquipmentInput,
    save_document,
    save_equipment,
    save_note,
    validate_upload,
)
from auto.services import BusinessError, ConflictError, get_vehicle, require_editor, save_vehicle
from auto.transactions import Session, atomic


def _safe_children(children):
    return [
        {
            **r,
            "files": [
                (name, hashlib.sha256(data).hexdigest(), len(data))
                for name, data in r.get("files", [])
            ],
        }
        for r in children
    ]


def preview_bundle(engine, data, children, actor, *, vehicle_id, expected_version, event_date=None):
    require_editor(actor)
    card = preview_vehicle(
        engine,
        data,
        actor,
        vehicle_id=vehicle_id,
        expected_version=expected_version,
        event_date=event_date,
    )
    operations = []
    for child in children:
        kind, values = child["kind"], child["values"]
        identity, version = child.get("id"), child.get("version")
        before = {}
        model = {
            "document": VehicleDocument,
            "equipment": VehicleEquipment,
            "note": VehicleNote,
        }.get(kind)
        if identity and model:
            with Session(engine) as s:
                row = s.get(model, identity)
                if not row or row.vehicle_id != vehicle_id or row.version != version:
                    raise ConflictError("Запис окремої форми вже змінено")
                before = row.model_dump(mode="json")
                if kind == "document":
                    before["details"] = (
                        s.exec(
                            select(DocumentVersion).where(
                                DocumentVersion.document_id == identity,
                                DocumentVersion.version == version,
                            )
                        )
                        .one()
                        .details
                    )
        if kind == "document":
            DocumentInput.model_validate(values)
            files = child.get("files", [])
            if not files or sum(len(b) for _, b in files) > 50 * 1024 * 1024:
                raise BusinessError("Документ потребує вкладень, разом до 50 МБ")
            for name, content in files:
                validate_upload(name, content)
        elif kind == "equipment":
            EquipmentInput.model_validate(values)
        elif kind == "note":
            if not values["text"].strip():
                raise BusinessError("Текст примітки обов’язковий")
        elif kind == "reading":
            if values.get("value") is not None:
                ReadingInput.model_validate(values)
            elif values.get("measured_on") or values.get("source"):
                raise BusinessError("Укажіть значення показника")
        elif kind == "norm":
            NormInput.model_validate(values)
            norm = preview_norm(
                engine, vehicle_id, values, actor, norm_id=identity, expected_version=version
            )
            before = {"periods": norm["before"]}
            card["blocked_documents"] = sorted(
                set(card["blocked_documents"] + norm["blocked_documents"])
            )
        else:
            raise BusinessError("Невідомий вид окремої форми")
        operations.append(
            {
                "kind": kind,
                "id": identity,
                "version": version,
                "before": before,
                "after": values,
                "files": [(n, len(b)) for n, b in child.get("files", [])],
            }
        )
    result = {"card": card, "operations": operations, "payload": _safe_children(children)}
    result["token"] = _digest(result)
    return result


def save_bundle(
    engine,
    data,
    children,
    actor,
    *,
    vehicle_id,
    expected_version,
    preview_token,
    confirmed=False,
    approvals=None,
    event_date=None,
):
    require_editor(actor)
    if not confirmed:
        raise BusinessError("Підтвердьте всю картку перед збереженням")
    with atomic(engine):
        plan = preview_bundle(
            engine,
            data,
            children,
            actor,
            vehicle_id=vehicle_id,
            expected_version=expected_version,
            event_date=event_date,
        )
        if plan["token"] != preview_token:
            raise ConflictError("Перегляд усієї картки застарів")
        if plan["card"]["blocked_documents"]:
            raise BusinessError("Зміну блокують застосовані документи")
        # Rebuild the original card token because the aggregate adds norm conflicts to its copy.
        core = preview_vehicle(
            engine,
            data,
            actor,
            vehicle_id=vehicle_id,
            expected_version=expected_version,
            event_date=event_date,
        )
        v = save_vehicle(
            engine,
            data,
            actor,
            vehicle_id=vehicle_id,
            expected_version=expected_version,
            event_date=event_date,
            preview_token=core["token"],
            confirmed=True,
            approvals=approvals,
        )
        for child in children:
            kind, values = child["kind"], child["values"]
            options = {"expected_version": child.get("version")}
            if kind == "document":
                save_document(
                    engine,
                    v.id,
                    values,
                    child["files"],
                    actor,
                    document_id=child.get("id"),
                    **options,
                )
            elif kind == "equipment":
                save_equipment(engine, v.id, values, actor, equipment_id=child.get("id"), **options)
            elif kind == "note":
                save_note(
                    engine,
                    v.id,
                    values["text"],
                    actor,
                    is_hidden=values["is_hidden"],
                    note_id=child.get("id"),
                    **options,
                )
            elif kind == "reading":
                add_reading(engine, v.id, values, actor)
            elif kind == "norm":
                p = preview_norm(engine, v.id, values, actor, norm_id=child.get("id"), **options)
                save_norm(
                    engine,
                    v.id,
                    values,
                    actor,
                    norm_id=child.get("id"),
                    preview_token=p["token"],
                    confirmed=True,
                    **options,
                )
        result = get_vehicle(engine, v.id)
    return result
