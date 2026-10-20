import hashlib
from datetime import date
from io import BytesIO
from pathlib import Path
from uuid import uuid4
from zipfile import BadZipFile, ZipFile

from PIL import Image
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlmodel import Session, select

from auto.models import (
    DocumentFile,
    DocumentVersion,
    NoteVersion,
    Vehicle,
    VehicleDocument,
    VehicleEquipment,
    VehicleNote,
    utcnow,
)
from auto.services import BusinessError, ConflictError, Role, audit, require_editor

MAX_FILE_SIZE = 20 * 1024 * 1024


def require_reader(actor):
    if not actor.user_id.strip() or actor.role not in (Role.ADMIN, Role.OPERATOR, Role.VIEWER):
        raise PermissionError("Недостатньо прав для перегляду")


def attachment_root(engine):
    base = Path(engine.url.database).resolve().parent
    root = base / "attachments"
    if root.is_symlink() or root.resolve().parent != base:
        raise BusinessError("Небезпечний шлях сховища вкладень")
    return root


def validate_upload(name, content):
    if (
        not name
        or "/" in name
        or "\\" in name
        or ":" in name
        or name in (".", "..")
        or len(name) > 200
        or any(ord(c) < 32 for c in name)
    ):
        raise BusinessError("Небезпечне ім'я вкладення")
    if not content or len(content) > MAX_FILE_SIZE:
        raise BusinessError("Розмір вкладення має бути від 1 байта до 20 МБ")
    suffix = Path(name).suffix.lower()
    if suffix == ".pdf":
        if not content.startswith(b"%PDF-"):
            raise BusinessError("Вміст не є PDF")
    elif suffix in (".docx", ".xlsx"):
        try:
            with ZipFile(BytesIO(content)) as z:
                if (
                    sum(i.file_size for i in z.infolist()) > 100 * 1024 * 1024
                    or len(z.infolist()) > 5000
                    or any("vbaproject" in n.lower() for n in z.namelist())
                ):
                    raise BusinessError("Завеликий або макровмісний Office-файл")
                required = "word/document.xml" if suffix == ".docx" else "xl/workbook.xml"
                if required not in z.namelist() or "[Content_Types].xml" not in z.namelist():
                    raise BusinessError("Тип Office-файлу не відповідає розширенню")
        except BadZipFile as exc:
            raise BusinessError("Пошкоджений Office-файл") from exc
    elif suffix in (".png", ".jpg", ".jpeg"):
        try:
            with Image.open(BytesIO(content)) as img:
                if img.format != ("PNG" if suffix == ".png" else "JPEG"):
                    raise ValueError("format")
                img.verify()
        except Exception as exc:
            raise BusinessError("Пошкоджене або невідповідне зображення") from exc
    else:
        raise BusinessError("Дозволено PDF, DOCX, XLSX, JPG, PNG")
    return suffix


class DocumentInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: str
    number: str | None = None
    document_date: date | None = None
    issuer: str | None = None
    note: str | None = None

    @model_validator(mode="after")
    def check(self):
        if not self.kind.strip():
            raise ValueError("Тип документа обов’язковий")
        return self


def save_document(
    engine, vehicle_id, details, files, actor, *, document_id=None, expected_version=None
):
    require_editor(actor)
    value = DocumentInput.model_validate(details)
    if not files or sum(len(data) for _, data in files) > 50 * 1024 * 1024:
        raise BusinessError("Документ потребує хоча б одного вкладення, разом не більше 50 МБ")
    prepared = [(name, bytes(data), validate_upload(name, data)) for name, data in files]
    written = []
    try:
        with Session(engine) as session:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            if not session.get(Vehicle, vehicle_id):
                raise BusinessError("Автомобіль не знайдено")
            doc = session.get(VehicleDocument, document_id) if document_id else None
            if document_id and (
                not doc or doc.vehicle_id != vehicle_id or doc.version != expected_version
            ):
                raise ConflictError("Документ уже змінено або не належить картці")
            doc = doc or VehicleDocument(vehicle_id=vehicle_id)
            if document_id:
                doc.version += 1
            session.add(doc)
            session.flush()
            revision = DocumentVersion(
                document_id=doc.id,
                version=doc.version,
                details=value.model_dump(mode="json"),
                created_by=actor.user_id,
            )
            session.add(revision)
            session.flush()
            root = attachment_root(engine)
            root.mkdir(parents=True, exist_ok=True)
            for name, data, suffix in prepared:
                relative = str(uuid4()) + suffix
                path = root / relative
                with path.open("xb") as stream:
                    written.append(path)
                    stream.write(data)
                session.add(
                    DocumentFile(
                        version_id=revision.id,
                        original_name=name,
                        relative_path=relative,
                        sha256=hashlib.sha256(data).hexdigest(),
                        size=len(data),
                    )
                )
            audit(
                session,
                actor,
                "vehicles",
                vehicle_id,
                "document_version",
                {},
                {"document_id": doc.id, "version": doc.version, "details": revision.details},
            )
            session.commit()
            session.refresh(doc)
            return doc
    except Exception:
        for path in written:
            path.unlink(missing_ok=True)
        raise


def list_documents(engine, vehicle_id, actor):
    require_reader(actor)
    with Session(engine) as session:
        docs = session.exec(
            select(VehicleDocument).where(VehicleDocument.vehicle_id == vehicle_id)
        ).all()
        result = []
        for doc in docs:
            for v in session.exec(
                select(DocumentVersion)
                .where(DocumentVersion.document_id == doc.id)
                .order_by(DocumentVersion.version.desc())
            ).all():
                files = session.exec(
                    select(DocumentFile).where(DocumentFile.version_id == v.id)
                ).all()
                result.append({"document": doc, "revision": v, "files": files})
        return result


def read_attachment(engine, vehicle_id, file_id, actor):
    require_reader(actor)
    with Session(engine) as session:
        file = session.get(DocumentFile, file_id)
        revision = session.get(DocumentVersion, file.version_id) if file else None
        doc = session.get(VehicleDocument, revision.document_id) if revision else None
        if not doc or doc.vehicle_id != vehicle_id:
            raise BusinessError("Вкладення не належить цій картці")
        root = attachment_root(engine).resolve()
        path = (root / file.relative_path).resolve()
        if path.parent != root or not path.is_file():
            raise BusinessError("Вкладення недоступне або шлях небезпечний")
        data = path.read_bytes()
        if len(data) != file.size or hashlib.sha256(data).hexdigest() != file.sha256:
            raise BusinessError("Контрольна сума вкладення не збігається")
        return file.original_name, data


class EquipmentInput(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    name: str
    quantity: float = Field(gt=0)
    installed_on: date | None = None
    removed_on: date | None = None
    is_removed: bool = False
    note: str | None = None

    @model_validator(mode="after")
    def check(self):
        if not self.name.strip():
            raise ValueError("Назва обладнання обов’язкова")
        if self.removed_on and not self.is_removed:
            raise ValueError("Для дати зняття позначте обладнання знятим")
        if self.installed_on and self.removed_on and self.removed_on < self.installed_on:
            raise ValueError("Дата зняття передує встановленню")
        return self


def save_equipment(engine, vehicle_id, data, actor, *, equipment_id=None, expected_version=None):
    require_editor(actor)
    value = EquipmentInput.model_validate(data)
    with Session(engine) as session:
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        if not session.get(Vehicle, vehicle_id):
            raise BusinessError("Автомобіль не знайдено")
        item = session.get(VehicleEquipment, equipment_id) if equipment_id else None
        if equipment_id and (
            not item or item.vehicle_id != vehicle_id or item.version != expected_version
        ):
            raise ConflictError("Обладнання вже змінено або не належить картці")
        old = item.model_dump(mode="json") if item else {}
        item = item or VehicleEquipment(vehicle_id=vehicle_id, **value.model_dump())
        for field, v in value.model_dump().items():
            setattr(item, field, v)
        if equipment_id:
            item.version += 1
        session.add(item)
        session.flush()
        audit(
            session, actor, "vehicles", vehicle_id, "equipment", old, item.model_dump(mode="json")
        )
        session.commit()
        session.refresh(item)
        return item


def list_equipment(engine, vehicle_id, actor, *, removed=None):
    require_reader(actor)
    with Session(engine) as session:
        q = select(VehicleEquipment).where(VehicleEquipment.vehicle_id == vehicle_id)
        if removed is not None:
            q = q.where(VehicleEquipment.is_removed == removed)
        return session.exec(q.order_by(VehicleEquipment.name)).all()


def save_note(
    engine, vehicle_id, text, actor, *, note_id=None, expected_version=None, is_hidden=False
):
    require_editor(actor)
    if not text.strip():
        raise BusinessError("Текст примітки обов’язковий")
    with Session(engine) as session:
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        if not session.get(Vehicle, vehicle_id):
            raise BusinessError("Автомобіль не знайдено")
        item = session.get(VehicleNote, note_id) if note_id else None
        if note_id and (
            not item or item.vehicle_id != vehicle_id or item.version != expected_version
        ):
            raise ConflictError("Примітку вже змінено або вона не належить картці")
        old = item.model_dump(mode="json") if item else {}
        item = item or VehicleNote(vehicle_id=vehicle_id, text=text, created_by=actor.user_id)
        item.text, item.is_hidden, item.updated_at = text, is_hidden, utcnow()
        if note_id:
            item.version += 1
        session.add(item)
        session.flush()
        session.add(
            NoteVersion(
                note_id=item.id,
                version=item.version,
                text=text,
                is_hidden=is_hidden,
                created_by=actor.user_id,
            )
        )
        audit(
            session,
            actor,
            "vehicles",
            vehicle_id,
            "note_version",
            old,
            item.model_dump(mode="json"),
        )
        session.commit()
        session.refresh(item)
        return item


def list_notes(engine, vehicle_id, actor, *, include_hidden=False):
    require_reader(actor)
    with Session(engine) as session:
        q = select(VehicleNote).where(VehicleNote.vehicle_id == vehicle_id)
        if not include_hidden:
            q = q.where(VehicleNote.is_hidden == False)  # noqa: E712
        return session.exec(q.order_by(VehicleNote.created_at.desc(), VehicleNote.id)).all()


def note_versions(engine, vehicle_id, note_id, actor):
    require_reader(actor)
    with Session(engine) as session:
        item = session.get(VehicleNote, note_id)
        if not item or item.vehicle_id != vehicle_id:
            raise BusinessError("Примітку не знайдено")
        return session.exec(
            select(NoteVersion)
            .where(NoteVersion.note_id == note_id)
            .order_by(NoteVersion.version.desc())
        ).all()
