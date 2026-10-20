import getpass
import os
from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy import update
from sqlmodel import Session, select

from auto.constants import ARCHIVED_STATUSES, normalize
from auto.models import (
    LOOKUPS,
    AuditEvent,
    Personnel,
    Vehicle,
    VehicleModel,
    utcnow,
)
from auto.schemas import VehicleInput


class Role(StrEnum):
    ADMIN = "admin"
    OPERATOR = "operator"
    VIEWER = "viewer"


@dataclass(frozen=True)
class Actor:
    user_id: str
    role: Role


def local_actor() -> Actor:
    return Actor(
        os.environ.get("AUTO_USER_ID", getpass.getuser()),
        Role(os.environ.get("AUTO_ROLE", "admin")),
    )


class BusinessError(ValueError):
    pass


class ConflictError(BusinessError):
    pass


def require_editor(actor: Actor):
    if not actor.user_id.strip() or actor.role not in (Role.ADMIN, Role.OPERATOR):
        raise PermissionError("Недостатньо прав для зміни даних")


def audit(session, actor, entity_type, entity_id, action, old, new):
    session.add(
        AuditEvent(
            entity_type=entity_type,
            entity_id=str(entity_id),
            action=action,
            user_id=actor.user_id,
            role=actor.role.value,
            old_values=old,
            new_values=new,
        )
    )


def list_lookup(engine, kind, *, active_only=True, brand_id=None):
    model = LOOKUPS[kind]
    statement = select(model)
    if active_only:
        statement = statement.where(model.is_active == True)  # noqa: E712
    if kind == "vehicle_models" and brand_id is not None:
        statement = statement.where(model.brand_id == brand_id)
    with Session(engine) as session:
        return session.exec(statement.order_by(model.normalized_name)).all()


def save_lookup(engine, kind, name, actor, *, entry_id=None, brand_id=None, is_active=True):
    require_editor(actor)
    if entry_id is not None and actor.role != Role.ADMIN:
        raise PermissionError("Лише адміністратор перейменовує або деактивує довідники")
    if entry_id is None and not is_active:
        raise BusinessError("Новий запис довідника має бути активним")
    normalized = normalize(name)
    if not normalized or len(name) > 200:
        raise BusinessError("Назва довідника обов'язкова, максимум 200 символів")
    model = LOOKUPS[kind]
    with Session(engine) as session:
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        item = session.get(model, entry_id) if entry_id else None
        if entry_id and item is None:
            raise BusinessError("Запис довідника не знайдено")
        statement = select(model).where(model.normalized_name == normalized)
        if kind == "vehicle_models":
            from auto.models import VehicleBrand

            brand = session.get(VehicleBrand, brand_id)
            if not brand or not brand.is_active:
                raise BusinessError("Оберіть активну марку")
            if item and item.brand_id != brand_id:
                raise BusinessError("Зміну марки моделі не дозволено")
            statement = statement.where(model.brand_id == brand_id)
        matches = session.exec(statement).all()
        if any(match.id != entry_id for match in matches):
            raise BusinessError("Дублікат назви в довіднику (включно з неактивними)")
        old = item.model_dump(mode="json") if item else {}
        if item is None:
            item = model(
                name=name,
                normalized_name=normalized,
                is_active=is_active,
                **({"brand_id": brand_id} if kind == "vehicle_models" else {}),
            )
        else:
            item.name, item.normalized_name, item.is_active = name, normalized, is_active
        session.add(item)
        session.flush()
        audit(
            session,
            actor,
            kind,
            item.id,
            "update" if old else "create",
            old,
            item.model_dump(mode="json"),
        )
        session.commit()
        session.refresh(item)
        return item


def add_person(engine, first_name, last_name, actor, **optional):
    require_editor(actor)
    if not first_name.strip() or not last_name.strip():
        raise BusinessError("Ім'я та прізвище обов'язкові")
    allowed = {"patronymic", "rank", "position", "unit_id", "personal_number"}
    if set(optional) - allowed:
        raise BusinessError("Невідомі поля персоналу")
    with Session(engine) as session:
        person = Personnel(first_name=first_name, last_name=last_name, **optional)
        session.add(person)
        session.flush()
        audit(session, actor, "personnel", person.id, "create", {}, person.model_dump(mode="json"))
        session.commit()
        session.refresh(person)
        return person


def validate_references(session, card, old):
    for field, kind in (
        ("brand_id", "vehicle_brands"),
        ("model_id", "vehicle_models"),
        ("vehicle_type_id", "vehicle_types"),
        ("support_service_id", "support_services"),
        ("unit_id", "units"),
        ("engine_type_id", "engine_types"),
        ("fuel_type_id", "fuel_types"),
    ):
        value = getattr(card, field)
        if value is None:
            continue
        entry = session.get(LOOKUPS[kind], value)
        if entry is None or (not entry.is_active and (old is None or getattr(old, field) != value)):
            raise BusinessError(f"Оберіть активне значення довідника: {kind}")
    if session.get(VehicleModel, card.model_id).brand_id != card.brand_id:
        raise BusinessError("Модель не належить обраній марці")


def save_vehicle(engine, data, actor, *, vehicle_id=None, expected_version=None):
    require_editor(actor)
    card = VehicleInput.model_validate(data)
    with Session(engine) as session:
        # Serialize checks and writes so two simultaneous creates cannot bypass duplicate checks.
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        old = session.get(Vehicle, vehicle_id) if vehicle_id else None
        if vehicle_id and old is None:
            raise BusinessError("Автомобіль не знайдено")
        if old and expected_version != old.version:
            raise ConflictError("Картку вже змінено. Перезавантажте її та узгодьте зміни")
        validate_references(session, card, old)
        values = card.model_dump()
        for field in ("military_number", "vin", "chassis_number", "serial_number"):
            normalized = normalize(values[field])
            values[field + "_normalized"] = normalized
            if normalized:
                statement = select(Vehicle).where(
                    getattr(Vehicle, field + "_normalized") == normalized,
                )
                if old:
                    statement = statement.where(Vehicle.id != old.id)
                matches = session.exec(statement).all()
                if matches:
                    ids = ", ".join(v.id for v in matches)
                    raise BusinessError(
                        f"Повтор {field}; картки зі збігом: {ids}. "
                        "Погодження винятків буде доступне наступним етапом.",
                    )
        before = old.model_dump(mode="json") if old else {}
        if old:
            values.update(updated_at=utcnow(), updated_by=actor.user_id, version=old.version + 1)
            result = session.execute(
                update(Vehicle)
                .where(
                    Vehicle.id == old.id,
                    Vehicle.version == expected_version,
                )
                .values(**values)
            )
            if result.rowcount != 1:
                raise ConflictError("Конфлікт версій картки")
            session.refresh(old)
            vehicle = old
        else:
            vehicle = Vehicle(**values, created_by=actor.user_id, updated_by=actor.user_id)
            session.add(vehicle)
        session.flush()
        audit(
            session,
            actor,
            "vehicles",
            vehicle.id,
            "update" if old else "create",
            before,
            vehicle.model_dump(mode="json"),
        )
        session.commit()
        session.refresh(vehicle)
        return vehicle


def get_vehicle(engine, vehicle_id):
    with Session(engine) as session:
        return session.get(Vehicle, vehicle_id)


def completeness(vehicle):
    missing = []
    if not any(
        (vehicle.military_number, vehicle.vin, vehicle.chassis_number, vehicle.serial_number)
    ):
        missing.append("Ідентифікатор")
    for field, label in (
        ("manufacture_value", "Дата виготовлення"),
        ("accounting_date", "Дата обліку"),
        ("unit_id", "Підрозділ"),
    ):
        if getattr(vehicle, field) is None:
            missing.append(label)
    if not vehicle.engine_not_applicable:
        for field, label in (("engine_type_id", "Тип двигуна"), ("fuel_type_id", "Вид пального")):
            if getattr(vehicle, field) is None:
                missing.append(label)
    return missing


def list_vehicles(engine, *, search="", include_archived=False, incomplete_only=False, **filters):
    with Session(engine) as session:
        statement = select(Vehicle)
        if not include_archived and not filters.get("accounting_status"):
            statement = statement.where(Vehicle.accounting_status.not_in(ARCHIVED_STATUSES))
        allowed = {
            "unit_id",
            "support_service_id",
            "vehicle_type_id",
            "accounting_status",
            "technical_condition",
            "technical_category",
            "is_operated",
        }
        if set(filters) - allowed:
            raise BusinessError("Невідомий фільтр")
        for field, value in filters.items():
            if value is not None:
                statement = statement.where(getattr(Vehicle, field) == value)
        vehicles = session.exec(statement.order_by(Vehicle.created_at.desc(), Vehicle.id)).all()
        brands = {x.id: x.name for x in session.exec(select(LOOKUPS["vehicle_brands"])).all()}
        models = {x.id: x.name for x in session.exec(select(VehicleModel)).all()}
        query = normalize(search)
        return [
            v
            for v in vehicles
            if (not incomplete_only or completeness(v))
            and (
                not query
                or query
                in (
                    normalize(
                        " ".join(
                            filter(
                                None,
                                [
                                    v.military_number,
                                    v.vin,
                                    v.chassis_number,
                                    v.serial_number,
                                    brands.get(v.brand_id),
                                    models.get(v.model_id),
                                ],
                            )
                        )
                    )
                    or ""
                )
            )
        ]


def list_audit(engine, vehicle_id):
    with Session(engine) as session:
        return session.exec(
            select(AuditEvent)
            .where(
                AuditEvent.entity_type == "vehicles",
                AuditEvent.entity_id == vehicle_id,
            )
            .order_by(AuditEvent.recorded_at.desc(), AuditEvent.id.desc())
        ).all()
