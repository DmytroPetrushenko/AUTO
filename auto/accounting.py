from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlmodel import Session, select

from auto.history import _digest, today
from auto.models import FuelNorm, Vehicle, VehicleDocumentLock, VehicleReading
from auto.services import BusinessError, ConflictError, audit, get_vehicle, require_editor


class NormInput(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    valid_from: date
    rate: float = Field(ge=0)
    unit: Literal["л/100 км", "л/мотогодину"]
    order_number: str
    order_date: date

    @model_validator(mode="after")
    def check(self):
        if not self.order_number.strip():
            raise ValueError("Номер наказу обов’язковий")
        return self


def _norms(session, vehicle_id):
    return session.exec(
        select(FuelNorm).where(FuelNorm.vehicle_id == vehicle_id).order_by(FuelNorm.valid_from)
    ).all()


def norm_at_rows(rows, day):
    chosen = next(
        (
            r
            for r in reversed(rows)
            if r["valid_from"] <= day and (r["valid_to"] is None or day < r["valid_to"])
        ),
        None,
    )
    return (
        {k: v for k, v in chosen.items() if k not in ("id", "version", "valid_to")}
        if chosen
        else None
    )


def norm_at(engine, vehicle_id, day=None):
    with Session(engine) as session:
        return norm_at_rows([r.model_dump() for r in _norms(session, vehicle_id)], day or today())


def list_norms(engine, vehicle_id):
    with Session(engine) as session:
        return _norms(session, vehicle_id)


def _norm_plan(session, vehicle_id, data, actor, norm_id, expected_version):
    require_editor(actor)
    value = NormInput.model_validate(data)
    vehicle = session.get(Vehicle, vehicle_id)
    if not vehicle:
        raise BusinessError("Автомобіль не знайдено")
    before = [r.model_dump() for r in _norms(session, vehicle_id)]
    old = next((r for r in before if r["id"] == norm_id), None)
    if norm_id and (not old or old["version"] != expected_version):
        raise ConflictError("Норму вже змінено")
    candidate = value.model_dump() | {
        "id": norm_id or "new",
        "vehicle_id": vehicle_id,
        "version": old["version"] + 1 if old else 1,
        "valid_to": None,
    }
    after = sorted(
        [r for r in before if r["id"] != norm_id] + [candidate], key=lambda r: r["valid_from"]
    )
    if len({r["valid_from"] for r in after}) != len(after):
        raise BusinessError("На цю дату норма вже існує")
    for i, r in enumerate(after):
        r["valid_to"] = after[i + 1]["valid_from"] if i + 1 < len(after) else None
    conflicts = []
    for lock in session.exec(
        select(VehicleDocumentLock).where(VehicleDocumentLock.vehicle_id == vehicle_id)
    ).all():
        proposed = norm_at_rows(after, lock.document_date)
        # JSON date serialization is canonical for applied snapshots.
        if _digest(proposed) != _digest(lock.snapshot.get("_fuel_norm")):
            conflicts.append(lock.document_reference)
    plan = {
        "before": before,
        "after": after,
        "vehicle_version": vehicle.version,
        "blocked_documents": conflicts,
        "user": actor.user_id,
        "role": actor.role.value,
    }
    plan["token"] = _digest(plan)
    return plan


def preview_norm(engine, vehicle_id, data, actor, *, norm_id=None, expected_version=None):
    with Session(engine) as session:
        return _norm_plan(session, vehicle_id, data, actor, norm_id, expected_version)


def save_norm(
    engine,
    vehicle_id,
    data,
    actor,
    *,
    preview_token,
    confirmed=False,
    norm_id=None,
    expected_version=None,
):
    require_editor(actor)
    with Session(engine) as session:
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        plan = _norm_plan(session, vehicle_id, data, actor, norm_id, expected_version)
        if not confirmed or preview_token != plan["token"]:
            raise ConflictError("Підтвердіть актуальний перегляд норм")
        if plan["blocked_documents"]:
            raise BusinessError(
                "Зміну норми блокують документи: " + ", ".join(plan["blocked_documents"])
            )
        for item in plan["after"]:
            row = session.get(FuelNorm, item["id"]) if item["id"] != "new" else None
            if row is None:
                row = FuelNorm(**{k: v for k, v in item.items() if k != "id"})
            else:
                for k, v in item.items():
                    setattr(row, k, v)
            session.add(row)
        vehicle = session.get(Vehicle, vehicle_id)
        vehicle.version += 1
        vehicle.updated_by = actor.user_id
        session.add(vehicle)
        audit(
            session,
            actor,
            "vehicles",
            vehicle_id,
            "fuel_norms",
            {"periods": [str(r) for r in plan["before"]]},
            {"periods": [str(r) for r in plan["after"]]},
        )
        session.commit()


class ReadingInput(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    value: float = Field(ge=0)
    unit: Literal["км", "мотогодини"]
    measured_on: date
    source: str
    confirmed: bool = False

    @model_validator(mode="after")
    def check(self):
        if not self.source.strip():
            raise ValueError("Джерело показника обов’язкове")
        if self.measured_on > today():
            raise ValueError("Показник не може бути з майбутнього")
        return self


def add_reading(engine, vehicle_id, data, actor, *, kind="initial", external_id=None):
    require_editor(actor)
    if data.get("value") is None:
        if any(data.get(f) for f in ("measured_on", "source")):
            raise BusinessError("Укажіть значення показника")
        return None
    value = ReadingInput.model_validate(data)
    if kind not in ("initial", "journal"):
        raise BusinessError("Невідомий тип показника")
    if kind == "journal" and not external_id:
        raise BusinessError("Запис журналу потребує стабільного зовнішнього ID")
    with Session(engine) as session:
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        vehicle = session.get(Vehicle, vehicle_id)
        if not vehicle:
            raise BusinessError("Автомобіль не знайдено")
        if value.unit != vehicle.work_unit:
            raise BusinessError("Одиниця показника не відповідає картці; перерахунку немає")
        if (
            external_id
            and session.exec(
                select(VehicleReading).where(VehicleReading.external_id == external_id)
            ).first()
        ):
            raise BusinessError("Цей запис журналу вже зареєстровано")
        if (
            kind == "initial"
            and session.exec(
                select(VehicleReading).where(
                    VehicleReading.vehicle_id == vehicle_id,
                    VehicleReading.unit == value.unit,
                    VehicleReading.kind == "initial",
                )
            ).first()
        ):
            raise BusinessError("Початковий показник уже заданий")
        reading = VehicleReading(
            vehicle_id=vehicle_id,
            kind=kind,
            external_id=external_id,
            recorded_by=actor.user_id,
            **value.model_dump(),
        )
        session.add(reading)
        session.flush()
        audit(
            session, actor, "vehicles", vehicle_id, "reading", {}, reading.model_dump(mode="json")
        )
        session.commit()
        session.refresh(reading)
        return reading


def current_reading(engine, vehicle_id, day=None):
    vehicle = get_vehicle(engine, vehicle_id)
    if not vehicle:
        raise BusinessError("Автомобіль не знайдено")
    if vehicle.work_unit == "без лічильника":
        return None
    with Session(engine) as session:
        return session.exec(
            select(VehicleReading)
            .where(
                VehicleReading.vehicle_id == vehicle_id,
                VehicleReading.unit == vehicle.work_unit,
                VehicleReading.measured_on <= (day or today()),
            )
            .order_by(
                VehicleReading.measured_on.desc(),
                VehicleReading.confirmed.desc(),
                VehicleReading.recorded_at.desc(),
                VehicleReading.id,
            )
        ).first()
