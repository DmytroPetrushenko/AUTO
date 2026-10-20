from pydantic import BaseModel, ConfigDict, field_validator
from sqlmodel import Session, select

from auto.constants import normalize
from auto.models import Personnel, Unit
from auto.services import BusinessError, ConflictError, Role, audit, require_editor


class PersonInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    first_name: str
    last_name: str
    patronymic: str | None = None
    rank: str | None = None
    position: str | None = None
    unit_id: int | None = None
    personal_number: str | None = None
    is_active: bool = True

    @field_validator("first_name", "last_name")
    @classmethod
    def required_name(cls, value):
        if not value.strip():
            raise ValueError("Ім'я та прізвище обов'язкові")
        return value


def list_people(engine, *, active_only=False):
    with Session(engine) as session:
        statement = select(Personnel).order_by(
            Personnel.last_name, Personnel.first_name, Personnel.id
        )
        if active_only:
            statement = statement.where(Personnel.is_active == True)  # noqa: E712
        return session.exec(statement).all()


def save_person(engine, data, actor, *, person_id=None, expected_version=None):
    require_editor(actor)
    if person_id is not None and actor.role != Role.ADMIN:
        raise PermissionError("Перейменування й деактивація персоналу — лише адміністратором")
    try:
        value = PersonInput.model_validate(data)
    except ValueError as exc:
        raise BusinessError(str(exc)) from exc
    if person_id is None and not value.is_active:
        raise BusinessError("Нова особа має бути активною")
    with Session(engine) as session:
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        old = session.get(Personnel, person_id) if person_id else None
        if person_id and (old is None or old.version != expected_version):
            raise ConflictError("Запис персоналу вже змінено. Перезавантажте його")
        if value.unit_id is not None:
            unit = session.get(Unit, value.unit_id)
            if not unit or (not unit.is_active and (old is None or old.unit_id != value.unit_id)):
                raise BusinessError("Оберіть активний підрозділ")
        number = normalize(value.personal_number)
        if number and any(
            normalize(p.personal_number) == number and p.id != person_id
            for p in session.exec(select(Personnel)).all()
        ):
            raise BusinessError("Повтор особистого номера, включно з неактивними особами")
        before = old.model_dump(mode="json") if old else {}
        person = old or Personnel(first_name=value.first_name, last_name=value.last_name)
        for field, item in value.model_dump().items():
            setattr(person, field, item)
        person.personal_number_normalized = number
        if old:
            person.version += 1
        session.add(person)
        session.flush()
        audit(
            session,
            actor,
            "personnel",
            person.id,
            "update" if old else "create",
            before,
            person.model_dump(mode="json"),
        )
        session.commit()
        session.refresh(person)
        return person
