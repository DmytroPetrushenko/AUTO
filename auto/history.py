"""Dated changes, preview fingerprints, approvals and applied-document protection."""

import hashlib
import json
from copy import deepcopy
from datetime import date, datetime
from uuid import uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import delete
from sqlmodel import Session, select

from auto.constants import normalize
from auto.models import (
    DuplicateApproval,
    Personnel,
    Vehicle,
    VehicleDocumentLock,
    VehicleHistory,
    utcnow,
)
from auto.schemas import VehicleInput
from auto.services import (
    BusinessError,
    ConflictError,
    Role,
    audit,
    require_editor,
    validate_references,
)

KINDS = {
    "unit": ("unit_id",),
    "service": ("support_service_id",),
    "mvo": ("mvo_id",),
    "status": ("accounting_status", "technical_condition", "technical_category", "is_operated"),
    "military_number": ("military_number",),
    "vin": ("vin", "vin_explanation"),
    "chassis_number": ("chassis_number",),
    "serial_number": ("serial_number",),
}
IDENTIFIERS = ("military_number", "vin", "chassis_number", "serial_number")
HISTORY_FIELDS = {field for fields in KINDS.values() for field in fields}


def today():
    return datetime.now(ZoneInfo("Europe/Kyiv")).date()


def _date(value):
    if value is None or isinstance(value, date):
        return value
    return date.fromisoformat(value)


def _digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()
    ).hexdigest()


def _event(row):
    return {
        "id": row.id,
        "kind": row.kind,
        "valid_from": row.valid_from,
        "valid_to": row.valid_to,
        "value": deepcopy(row.value),
        "is_initial": row.is_initial,
    }


def _load(session, vehicle_id):
    return [
        _event(r)
        for r in session.exec(
            select(VehicleHistory).where(VehicleHistory.vehicle_id == vehicle_id)
        ).all()
    ]


def _periods(events):
    for kind in KINDS:
        rows = sorted(
            (r for r in events if r["kind"] == kind), key=lambda r: r["valid_from"] or date.min
        )
        if len({r["valid_from"] for r in rows}) != len(rows):
            raise BusinessError("На одну дату вже є період цього типу; оберіть іншу дату")
        for index, row in enumerate(rows):
            row["valid_to"] = rows[index + 1]["valid_from"] if index + 1 < len(rows) else None
    return sorted(events, key=lambda r: (r["kind"], r["valid_from"] or date.min))


def values_at(events, on_date):
    values = {}
    for row in _periods(deepcopy(events)):
        if (row["valid_from"] is None or row["valid_from"] <= on_date) and (
            row["valid_to"] is None or on_date < row["valid_to"]
        ):
            values.update(row["value"])
    return values


def projected_vehicle(session, vehicle, on_date=None):
    """Return a detached view, so future dated changes are correct without background writes."""
    result = Vehicle.model_validate(vehicle.model_dump())
    for field, value in values_at(_load(session, vehicle.id), on_date or today()).items():
        setattr(result, field, value)
    for field in IDENTIFIERS:
        setattr(result, field + "_normalized", normalize(getattr(result, field)))
    return result


def list_history(engine, vehicle_id):
    with Session(engine) as session:
        return _periods(_load(session, vehicle_id))


def duplicate_matches(session, field, value, vehicle_id=None):
    normalized = normalize(value)
    if not normalized:
        return []
    ids = {
        v.id
        for v in session.exec(
            select(Vehicle).where(getattr(Vehicle, field + "_normalized") == normalized)
        ).all()
        if v.id != vehicle_id
    }
    for row in session.exec(select(VehicleHistory).where(VehicleHistory.kind == field)).all():
        if row.vehicle_id != vehicle_id and normalize(row.value.get(field)) == normalized:
            ids.add(row.vehicle_id)
    matches = []
    for identifier in sorted(ids):
        vehicle = session.get(Vehicle, identifier)
        if vehicle:
            matches.append(
                {
                    "vehicle_id": identifier,
                    "military_number": vehicle.military_number,
                    "brand_id": vehicle.brand_id,
                    "model_id": vehicle.model_id,
                    "status": vehicle.accounting_status,
                }
            )
    return matches


def _covered(session, vehicle_id, field, value, matches):
    if not vehicle_id:
        return False
    approved = set()
    for row in session.exec(
        select(DuplicateApproval).where(
            DuplicateApproval.vehicle_id == vehicle_id,
            DuplicateApproval.field == field,
            DuplicateApproval.normalized_value == normalize(value),
        )
    ).all():
        approved.update(row.matched_vehicle_ids)
    return {m["vehicle_id"] for m in matches} <= approved


def _duplicates(session, values, vehicle_id):
    result = []
    for field, value in values.items():
        if field not in IDENTIFIERS:
            continue
        matches = duplicate_matches(session, field, value, vehicle_id)
        if matches:
            result.append(
                {
                    "field": field,
                    "value": value,
                    "matches": matches,
                    "approval_required": not _covered(session, vehicle_id, field, value, matches),
                }
            )
    return result


def _document_conflicts(session, vehicle_id, after):
    return [
        row.document_reference
        for row in session.exec(
            select(VehicleDocumentLock).where(VehicleDocumentLock.vehicle_id == vehicle_id)
        ).all()
        if any(
            values_at(after, row.document_date).get(field) != value
            for field, value in row.snapshot.items()
            if field in HISTORY_FIELDS
        )
    ]


def _plan(session, data, actor, vehicle_id, expected_version, event_date, history_edit=None):
    require_editor(actor)
    event_date = _date(event_date)
    old = session.get(Vehicle, vehicle_id) if vehicle_id else None
    if vehicle_id and old is None:
        raise BusinessError("Автомобіль не знайдено")
    if old and old.version != expected_version:
        raise ConflictError("Картку вже змінено. Перезавантажте її та узгодьте зміни")
    current = projected_vehicle(session, old) if old else None
    before = _load(session, vehicle_id) if old else []
    after = deepcopy(before)
    card = VehicleInput.model_validate(data)
    validate_references(session, card, current)
    changed = []
    duplicate_values = {
        f: getattr(card, f)
        for f in IDENTIFIERS
        if current is None or normalize(getattr(card, f)) != normalize(getattr(current, f))
    }
    if not old and event_date is not None and event_date > today():
        raise BusinessError("Початковий стан не може починатися в майбутньому")
    if history_edit:
        row = next((r for r in after if r["id"] == history_edit["id"]), None)
        if row is None:
            raise BusinessError("Історичний період не знайдено")
        if event_date is None and row["valid_from"] is not None:
            raise BusinessError("Не можна замінити відому дату невідомою")
        payload = history_edit["value"]
        if set(payload) != set(KINDS[row["kind"]]):
            raise BusinessError("Неправильні поля історичного періоду")
        candidate = VehicleInput.model_validate(card.model_dump() | payload)
        # Retired references may retain their existing historical value.
        historic = Vehicle.model_validate(current.model_dump() | row["value"])
        validate_references(session, candidate, historic)
        row.update(
            valid_from=event_date,
            value={f: getattr(candidate, f) for f in payload},
            is_initial=False,
        )
        changed = [row["kind"]]
        duplicate_values = payload
    else:
        for kind, fields in KINDS.items():
            payload = {f: getattr(card, f) for f in fields}
            if not old or any(getattr(current, f) != payload[f] for f in fields):
                if old and event_date is None:
                    raise BusinessError(
                        "Для зміни підрозділу, МВО, служби, статусу чи ID потрібна дата події"
                    )
                changed.append(kind)
                boundary = next(
                    (r for r in after if r["kind"] == kind and r["valid_from"] == event_date), None
                )
                if boundary:
                    boundary.update(value=payload, is_initial=False)
                else:
                    after.append(
                        {
                            "id": f"new:{kind}:{event_date}",
                            "kind": kind,
                            "valid_from": event_date,
                            "valid_to": None,
                            "value": payload,
                            "is_initial": old is None,
                        }
                    )
    after = _periods(after)
    if old and any(
        field not in values_at(after, today())
        for field in (
            "support_service_id",
            "accounting_status",
            "technical_condition",
            "technical_category",
            "is_operated",
        )
    ):
        raise BusinessError("Після виправлення відсутній чинний обов'язковий стан на сьогодні")
    duplicates = _duplicates(session, duplicate_values, vehicle_id)
    conflicts = _document_conflicts(session, vehicle_id, after) if old else []
    # Include conflicts and all duplicate cards in the fingerprint, so a changed
    # environment invalidates a previously displayed preview.
    plan = {
        "vehicle_id": vehicle_id,
        "version": old.version if old else None,
        "actor": {"id": actor.user_id, "role": actor.role.value},
        "data": card.model_dump(mode="json"),
        "event_date": event_date,
        "history_edit": history_edit,
        "before": _periods(before),
        "after": after,
        "changed_kinds": changed,
        "duplicates": duplicates,
        "blocked_documents": conflicts,
        "day": today(),
    }
    plan["token"] = _digest(plan)
    return plan


def preview_vehicle(
    engine, data, actor, *, vehicle_id=None, expected_version=None, event_date=None
):
    with Session(engine) as session:
        return _plan(session, data, actor, vehicle_id, expected_version, event_date)


def preview_history(engine, vehicle_id, event_id, value, event_date, actor, expected_version):
    with Session(engine) as session:
        vehicle = session.get(Vehicle, vehicle_id)
        if not vehicle:
            raise BusinessError("Автомобіль не знайдено")
        card = {
            f: getattr(projected_vehicle(session, vehicle), f) for f in VehicleInput.model_fields
        }
        return _plan(
            session,
            card,
            actor,
            vehicle_id,
            expected_version,
            event_date,
            {"id": event_id, "value": value},
        )


def _approve(session, plan, actor, vehicle_id, approvals):
    for duplicate in plan["duplicates"]:
        if not duplicate["approval_required"]:
            continue
        field = duplicate["field"]
        permission = (approvals or {}).get(field, {})
        if field != "military_number" and actor.role != Role.ADMIN:
            raise PermissionError(f"Повтор {field} погоджує лише адміністратор")
        if permission.get("confirmed") is not True or not str(permission.get("reason", "")).strip():
            matches = ", ".join(m["vehicle_id"] for m in duplicate["matches"])
            raise BusinessError(f"Повтор {field}: {matches}. Потрібні підтвердження і причина")
        approval = DuplicateApproval(
            vehicle_id=vehicle_id,
            field=field,
            normalized_value=normalize(duplicate["value"]),
            matched_vehicle_ids=[m["vehicle_id"] for m in duplicate["matches"]],
            reason=permission["reason"].strip(),
            approved_by=actor.user_id,
            approved_role=actor.role.value,
        )
        session.add(approval)
        audit(
            session,
            actor,
            "vehicles",
            vehicle_id,
            "duplicate_approved",
            {},
            approval.model_dump(mode="json", exclude={"id"}),
        )


def _write_history(session, vehicle_id, plan, actor):
    existing = {
        r.id: r
        for r in session.exec(
            select(VehicleHistory).where(VehicleHistory.vehicle_id == vehicle_id)
        ).all()
    }
    for item in plan["after"]:
        row = existing.get(item["id"])
        if row and _event(row) == item:
            continue
        if row is None:
            row = VehicleHistory(
                id=str(uuid4()),
                vehicle_id=vehicle_id,
                kind=item["kind"],
                value=item["value"],
                recorded_by=actor.user_id,
                updated_by=actor.user_id,
            )
            # Persist the actual stable period ID in audit snapshots too.
            item["id"] = row.id
        row.valid_from, row.valid_to, row.value = (
            item["valid_from"],
            item["valid_to"],
            item["value"],
        )
        row.is_initial = item["is_initial"]
        row.unit_id = item["value"].get("unit_id")
        row.support_service_id = item["value"].get("support_service_id")
        row.person_id = item["value"].get("mvo_id")
        row.updated_by, row.updated_at = actor.user_id, utcnow()
        session.add(row)


def save_card(
    engine,
    data,
    actor,
    *,
    vehicle_id=None,
    expected_version=None,
    event_date=None,
    preview_token=None,
    confirmed=False,
    approvals=None,
    history_edit=None,
    import_receipt=None,
):
    require_editor(actor)
    with Session(engine) as session:
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        plan = _plan(session, data, actor, vehicle_id, expected_version, event_date, history_edit)
        if plan["blocked_documents"]:
            raise BusinessError(
                "Зміну блокують застосовані документи: " + ", ".join(plan["blocked_documents"])
            )
        needs_confirmation = (
            bool(vehicle_id and plan["changed_kinds"])
            or history_edit
            or (plan["event_date"] is not None and plan["event_date"] < today())
            or any(d["approval_required"] for d in plan["duplicates"])
        )
        if preview_token is not None and preview_token != plan["token"]:
            raise ConflictError("Перегляд застарів. Перегляньте «було → стане» повторно")
        if needs_confirmation and (not confirmed or preview_token != plan["token"]):
            ids = ", ".join(m["vehicle_id"] for d in plan["duplicates"] for m in d["matches"])
            raise BusinessError(
                "Потрібні перегляд «було → стане» та підтвердження"
                + (f". Картки зі збігом: {ids}" if ids else "")
            )
        old = session.get(Vehicle, vehicle_id) if vehicle_id else None
        before = old.model_dump(mode="json") if old else {}
        values = VehicleInput.model_validate(data).model_dump()
        # Historical edits and future events must not overwrite the current projection.
        if old:
            values.update(values_at(plan["after"], today()))
            for kind in plan["changed_kinds"]:
                for field in KINDS[kind]:
                    if field not in values_at(plan["after"], today()):
                        values[field] = None
        for field in IDENTIFIERS:
            values[field + "_normalized"] = normalize(values[field])
        if old:
            for field, value in values.items():
                setattr(old, field, value)
            old.version += 1
            old.updated_by, old.updated_at = actor.user_id, utcnow()
            vehicle = old
        else:
            vehicle = Vehicle(**values, created_by=actor.user_id, updated_by=actor.user_id)
        session.add(vehicle)
        session.flush()
        if import_receipt is not None:
            from auto.models import ImportReceipt

            session.add(ImportReceipt(vehicle_id=vehicle.id, **import_receipt))
        _write_history(session, vehicle.id, plan, actor)
        _approve(session, plan, actor, vehicle.id, approvals)
        audit(
            session,
            actor,
            "vehicles",
            vehicle.id,
            "history_update" if history_edit else ("update" if old else "create"),
            before,
            vehicle.model_dump(mode="json"),
        )
        if plan["changed_kinds"]:
            audit(
                session,
                actor,
                "vehicles",
                vehicle.id,
                "periods_reconciled",
                {"periods": json.loads(json.dumps(plan["before"], default=str))},
                {
                    "periods": json.loads(json.dumps(plan["after"], default=str)),
                    "event_date": str(plan["event_date"]) if plan["event_date"] else None,
                },
            )
        session.commit()
        session.refresh(vehicle)
        return projected_vehicle(session, vehicle)


def apply_history(
    engine,
    vehicle_id,
    event_id,
    value,
    event_date,
    actor,
    expected_version,
    preview_token,
    *,
    confirmed=False,
    approvals=None,
):
    # The same preview and card transaction handle all history paths.
    with Session(engine) as session:
        vehicle = session.get(Vehicle, vehicle_id)
        if not vehicle:
            raise BusinessError("Автомобіль не знайдено")
        view = projected_vehicle(session, vehicle)
        data = {f: getattr(view, f) for f in VehicleInput.model_fields}
    return save_card(
        engine,
        data,
        actor,
        vehicle_id=vehicle_id,
        expected_version=expected_version,
        event_date=event_date,
        preview_token=preview_token,
        confirmed=confirmed,
        approvals=approvals,
        history_edit={"id": event_id, "value": value},
    )


def register_document_lock(
    engine, vehicle_id, reference, on_date, actor, *, adjustment_percent=-15
):
    """Called only when an external module applies a document, never by the current UI."""
    require_editor(actor)
    if not reference.strip() or _date(on_date) is None:
        raise BusinessError("Посилання на документ і його дата обов'язкові")
    with Session(engine) as session:
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        vehicle = session.get(Vehicle, vehicle_id)
        if not vehicle:
            raise BusinessError("Автомобіль не знайдено")
        snapshot = values_at(_load(session, vehicle_id), _date(on_date))
        if set(snapshot) != HISTORY_FIELDS:
            raise BusinessError("На дату документа немає повного стану; уточніть історію")
        person = session.get(Personnel, snapshot["mvo_id"]) if snapshot["mvo_id"] else None
        snapshot["_mvo_person"] = person.model_dump(mode="json") if person else None
        import math

        from auto.accounting import current_reading, norm_at

        if not math.isfinite(adjustment_percent) or adjustment_percent < -100:
            raise BusinessError("Неправильне коригування дорожнього листа")
        norm = norm_at(engine, vehicle_id, _date(on_date))
        snapshot["_fuel_norm"] = json.loads(json.dumps(norm, default=str))
        snapshot["_fuel_adjustment_percent"] = adjustment_percent
        reading = current_reading(engine, vehicle_id, _date(on_date))
        snapshot["_reading"] = reading.model_dump(mode="json") if reading else None
        row = VehicleDocumentLock(
            vehicle_id=vehicle_id,
            document_reference=reference,
            document_date=_date(on_date),
            snapshot=snapshot,
            registered_by=actor.user_id,
        )
        session.add(row)
        audit(
            session,
            actor,
            "vehicles",
            vehicle_id,
            "document_applied",
            {},
            {"reference": reference, "date": str(on_date), "snapshot": snapshot},
        )
        session.commit()


def delete_vehicle(engine, vehicle_id, actor, *, expected_version, reason, confirmed=False):
    require_editor(actor)
    if actor.role != Role.ADMIN:
        raise PermissionError("Видалення дозволено лише адміністратору")
    if not confirmed or not reason.strip():
        raise BusinessError("Для видалення помилкової картки потрібні підтвердження й причина")
    with Session(engine) as session:
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        vehicle = session.get(Vehicle, vehicle_id)
        if not vehicle or vehicle.version != expected_version:
            raise ConflictError("Картку вже змінено або видалено")
        if projected_vehicle(session, vehicle).accounting_status in (
            "Списано",
            "Втрачено (знищено)",
        ):
            raise BusinessError("Списані та втрачені картки не видаляються")
        blockers = []
        from auto.models import (
            FuelNorm,
            VehicleDocument,
            VehicleEquipment,
            VehicleNote,
            VehicleReading,
        )

        for model, label in (
            (VehicleDocument, "документи з вкладеннями"),
            (VehicleEquipment, "обладнання"),
            (VehicleNote, "примітки"),
            (FuelNorm, "норми пального"),
            (VehicleReading, "показники журналу/початкові"),
        ):
            if session.exec(select(model).where(model.vehicle_id == vehicle_id)).first():
                blockers.append(label)
        if session.exec(
            select(VehicleDocumentLock).where(VehicleDocumentLock.vehicle_id == vehicle_id)
        ).first():
            blockers.append("застосовані документи")
        if session.exec(
            select(DuplicateApproval).where(DuplicateApproval.vehicle_id == vehicle_id)
        ).first():
            blockers.append("погодження ідентифікаторів")
        if any(
            vehicle_id in approval.matched_vehicle_ids
            for approval in session.exec(select(DuplicateApproval)).all()
        ):
            blockers.append("погодження дублікатів інших карток")
        if any(not r["is_initial"] for r in _load(session, vehicle_id)):
            blockers.append("внесені історичні події")
        if blockers:
            raise BusinessError("Видалення блокують: " + ", ".join(blockers))
        audit(
            session,
            actor,
            "vehicles",
            vehicle_id,
            "delete",
            vehicle.model_dump(mode="json"),
            {"reason": reason.strip(), "deleted": True},
        )
        session.exec(delete(VehicleHistory).where(VehicleHistory.vehicle_id == vehicle_id))
        session.delete(vehicle)
        session.commit()
