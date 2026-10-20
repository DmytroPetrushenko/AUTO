from copy import deepcopy

import pandas as pd
import streamlit as st
from pydantic import ValidationError
from pydantic_core import PydanticUndefined
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session, select

from auto.constants import (
    ACCOUNTING_STATUSES,
    MANUFACTURE_PRECISIONS,
    TECHNICAL_CATEGORIES,
    TECHNICAL_CONDITIONS,
    WORK_UNITS,
)
from auto.db import db_path, make_engine, require_current_schema
from auto.models import Personnel
from auto.schemas import VehicleInput
from auto.services import (
    BusinessError,
    Role,
    add_person,
    completeness,
    get_vehicle,
    list_audit,
    list_lookup,
    list_vehicles,
    local_actor,
    save_lookup,
    save_vehicle,
)

LOOKUP_LABELS = {
    "vehicle_brands": "Марки",
    "vehicle_models": "Моделі",
    "vehicle_types": "Типи техніки",
    "support_services": "Служби забезпечення",
    "engine_types": "Типи двигуна",
    "fuel_types": "Види пального",
    "units": "Підрозділи",
    "personnel": "Персонал",
}
LABELS = {
    "brand_id": "Марка",
    "model_id": "Модель",
    "vehicle_type_id": "Тип техніки",
    "support_service_id": "Служба забезпечення",
    "unit_id": "Підрозділ",
    "military_number": "Військовий номер",
    "vin": "VIN",
    "vin_explanation": "Пояснення VIN",
    "chassis_number": "Шасі / рама",
    "serial_number": "Заводський номер",
    "engine_type_id": "Тип двигуна",
    "fuel_type_id": "Вид пального",
    "engine_displacement_cm3": "Об'єм двигуна, см³",
    "engine_power_kw": "Потужність, кВт",
    "engine_number": "Номер двигуна",
    "payload_kg": "Вантажопідйомність, кг",
    "seats": "Кількість місць",
    "gross_weight_kg": "Повна маса, кг",
    "unloaded_weight_kg": "Маса без навантаження, кг",
    "color": "Колір",
    "manufacture_precision": "Точність дати виготовлення",
    "manufacture_value": "Дата виготовлення",
    "work_unit": "Одиниця напрацювання",
    "technical_condition": "Технічний стан",
    "technical_category": "Технічна категорія",
    "engine_not_applicable": "Двигун і пальне не застосовуються",
    "accounting_status": "Обліковий статус",
    "is_operated": "Експлуатується",
    "accounting_date": "Дата взяття на облік",
    "vk_code": "Код ВК 001-2000",
    "nomenclature_number": "Номенклатурний номер",
    "current_location": "Місце перебування",
}
ERRORS = (BusinessError, ValidationError, PermissionError, SQLAlchemyError)


@st.cache_resource
def cached_engine(path):
    return make_engine(path)


def widget_key(field):
    return f"card_{field}"


def start_card(engine, vehicle_id=None):
    vehicle = get_vehicle(engine, vehicle_id) if vehicle_id else None
    if vehicle_id and vehicle is None:
        st.session_state.notice = ("error", "Картку не знайдено")
        return
    draft = {}
    for name, field in VehicleInput.model_fields.items():
        draft[name] = (
            getattr(vehicle, name)
            if vehicle
            else (None if field.default is PydanticUndefined else field.default)
        )
    st.session_state.draft = draft
    st.session_state.baseline = deepcopy(draft)
    st.session_state.vehicle_id = vehicle_id
    st.session_state.vehicle_version = vehicle.version if vehicle else None
    for name, value in draft.items():
        if name in TEXT_FIELDS:
            value = value or ""
        st.session_state[widget_key(name)] = value
    st.session_state.page = "card"


def is_dirty():
    return st.session_state.get("draft") != st.session_state.get("baseline")


def change_field(field, engine=None):
    value = st.session_state[widget_key(field)]
    if field in TEXT_FIELDS and not value.strip():
        value = None
    st.session_state.draft[field] = value
    if field == "brand_id":
        st.session_state.draft["model_id"] = None
        st.session_state[widget_key("model_id")] = None
    if field == "vehicle_type_id" and st.session_state.draft["work_unit"] is None:
        types = list_lookup(engine, "vehicle_types")
        selected = next((t for t in types if t.id == value), None)
        if selected and selected.suggested_work_unit:
            st.session_state.draft["work_unit"] = selected.suggested_work_unit
            st.session_state[widget_key("work_unit")] = selected.suggested_work_unit


TEXT_FIELDS = {
    "military_number",
    "vin",
    "vin_explanation",
    "chassis_number",
    "serial_number",
    "manufacture_value",
    "engine_number",
    "color",
    "vk_code",
    "nomenclature_number",
    "current_location",
}


def save_current(engine, actor):
    try:
        vehicle = save_vehicle(
            engine,
            st.session_state.draft,
            actor,
            vehicle_id=st.session_state.vehicle_id,
            expected_version=st.session_state.vehicle_version,
        )
    except ERRORS as exc:
        st.session_state.notice = ("error", friendly_error(exc))
        return False
    start_card(engine, vehicle.id)
    st.session_state.notice = ("success", "Картку збережено")
    return True


def friendly_error(exc):
    if isinstance(exc, ValidationError):
        return "\n".join(
            f"{LABELS.get(str(e['loc'][0]), str(e['loc'][0])) + ': ' if e['loc'] else ''}{e['msg']}"
            for e in exc.errors(include_url=False)
        )
    if isinstance(exc, SQLAlchemyError):
        return "Помилка SQLite. Зміни не збережено; перевірте доступність БД та її зв'язки."
    return str(exc)


def navigate(engine, page, vehicle_id=None):
    if page == "card":
        start_card(engine, vehicle_id)
    else:
        st.session_state.page = page
    st.session_state.pending = None


def request_navigation(engine, page, vehicle_id=None):
    if st.session_state.page == "card" and is_dirty():
        st.session_state.pending = (page, vehicle_id)
    else:
        navigate(engine, page, vehicle_id)


def resolve_navigation(engine, actor, decision):
    target = st.session_state.pending
    if decision == "stay":
        st.session_state.pending = None
    elif decision == "discard":
        navigate(engine, *target)
    elif save_current(engine, actor):
        navigate(engine, *target)


def add_from_card(engine, actor, kind, field):
    try:
        entry = save_lookup(
            engine,
            kind,
            st.session_state[f"plus_{field}"],
            actor,
            brand_id=st.session_state.draft["brand_id"],
        )
        st.session_state[widget_key(field)] = entry.id
        change_field(field, engine)
    except ERRORS as exc:
        st.session_state.notice = ("error", friendly_error(exc))


def lookup_field(engine, actor, field, kind):
    selected = st.session_state.draft[field]
    entries = list_lookup(
        engine,
        kind,
        active_only=False,
        brand_id=st.session_state.draft["brand_id"] if kind == "vehicle_models" else None,
    )
    entries = [e for e in entries if e.is_active or e.id == selected]
    if kind == "vehicle_models" and not st.session_state.draft["brand_id"]:
        entries = []
    names = {e.id: e.name + (" (неактивний)" if not e.is_active else "") for e in entries}
    left, right = st.columns([8, 1], vertical_alignment="bottom")
    disabled = actor.role == Role.VIEWER
    with left:
        st.selectbox(
            LABELS[field],
            [e.id for e in entries],
            index=None,
            key=widget_key(field),
            placeholder="Оберіть значення",
            format_func=lambda x: names.get(x, "—"),
            disabled=disabled,
            on_change=change_field,
            args=(field, engine),
        )
    with right:
        with st.popover("+", disabled=disabled):
            st.text_input("Нова назва", key=f"plus_{field}")
            st.button(
                "Додати",
                key=f"add_{field}",
                on_click=add_from_card,
                args=(engine, actor, kind, field),
                disabled=kind == "vehicle_models" and not st.session_state.draft["brand_id"],
            )


def text_field(field, disabled=False, **kwargs):
    st.text_input(
        LABELS[field],
        key=widget_key(field),
        disabled=disabled,
        on_change=change_field,
        args=(field,),
        **kwargs,
    )


def select_field(field, options, disabled=False):
    st.selectbox(
        LABELS[field],
        options,
        index=None,
        key=widget_key(field),
        placeholder="Оберіть значення",
        disabled=disabled,
        on_change=change_field,
        args=(field,),
    )


def card_page(engine, actor):
    draft = st.session_state.draft
    brand_names = {e.id: e.name for e in list_lookup(engine, "vehicle_brands", active_only=False)}
    model_names = {e.id: e.name for e in list_lookup(engine, "vehicle_models", active_only=False)}
    title = " ".join(
        filter(
            None,
            [
                draft["military_number"] or "Без військового номера",
                brand_names.get(draft["brand_id"]),
                model_names.get(draft["model_id"]),
            ],
        )
    )
    st.header(title)
    st.caption(
        f"{draft['accounting_status']} · "
        f"{st.session_state.vehicle_id or 'Нова картка'} · "
        f"Версія {st.session_state.vehicle_version or '—'}"
    )
    disabled = actor.role == Role.VIEWER
    if is_dirty():
        st.warning("Є незбережені зміни")
    else:
        st.caption("Змін немає. Збереження — тільки кнопкою «Зберегти».")
    actions = st.columns(3)
    actions[0].button(
        "Зберегти",
        type="primary",
        on_click=save_current,
        args=(engine, actor),
        disabled=disabled,
        key="save_card",
    )
    actions[1].button(
        "Скасувати зміни",
        on_click=start_card,
        args=(engine, st.session_state.vehicle_id),
        disabled=disabled,
        key="cancel_card",
    )
    actions[2].button(
        "До реєстру", on_click=request_navigation, args=(engine, "registry"), key="back_registry"
    )

    tabs = st.tabs(
        ["Основне", "Технічні дані", "Облік", "Історія", "Документи", "Обладнання", "Примітки"]
    )
    with tabs[0]:
        st.subheader("Основні відомості")
        for field, kind in (
            ("brand_id", "vehicle_brands"),
            ("model_id", "vehicle_models"),
            ("vehicle_type_id", "vehicle_types"),
            ("support_service_id", "support_services"),
            ("unit_id", "units"),
        ):
            lookup_field(engine, actor, field, kind)
        st.caption("Марка, модель, тип, служба й одиниця напрацювання обов'язкові.")
        for field in (
            "military_number",
            "vin",
            "vin_explanation",
            "chassis_number",
            "serial_number",
        ):
            text_field(field, disabled)
        st.caption("Нестандартний VIN допускається з поясненням. Повтори ID поки блокуються.")
    with tabs[1]:
        st.subheader("Технічні дані")
        for field, options in (
            ("work_unit", WORK_UNITS),
            ("technical_condition", TECHNICAL_CONDITIONS),
            ("technical_category", TECHNICAL_CATEGORIES),
            ("manufacture_precision", MANUFACTURE_PRECISIONS),
        ):
            select_field(field, options, disabled)
        text_field(
            "manufacture_value", disabled, help="YYYY / YYYY-MM / YYYY-MM-DD; без вигаданих дат"
        )
        st.checkbox(
            LABELS["engine_not_applicable"],
            key=widget_key("engine_not_applicable"),
            disabled=disabled,
            on_change=change_field,
            args=("engine_not_applicable",),
        )
        lookup_field(engine, actor, "engine_type_id", "engine_types")
        lookup_field(engine, actor, "fuel_type_id", "fuel_types")
        text_field("engine_number", disabled)
        for field in (
            "engine_displacement_cm3",
            "engine_power_kw",
            "payload_kg",
            "seats",
            "gross_weight_kg",
            "unloaded_weight_kg",
        ):
            st.number_input(
                LABELS[field],
                min_value=0 if field == "seats" else 0.0,
                step=1 if field == "seats" else 1.0,
                value=None,
                key=widget_key(field),
                disabled=disabled,
                on_change=change_field,
                args=(field,),
            )
        text_field("color", disabled)
    with tabs[2]:
        st.subheader("Облік")
        select_field("accounting_status", ACCOUNTING_STATUSES, disabled)
        st.checkbox(
            LABELS["is_operated"],
            key=widget_key("is_operated"),
            disabled=disabled,
            on_change=change_field,
            args=("is_operated",),
        )
        st.date_input(
            LABELS["accounting_date"],
            value=None,
            key=widget_key("accounting_date"),
            disabled=disabled,
            on_change=change_field,
            args=("accounting_date",),
        )
        for field in ("vk_code", "nomenclature_number", "current_location"):
            text_field(field, disabled)
        st.info(
            "Немає показників. Початкові показники, журнал пробігу, МВО та норми — наступний етап."
        )
        if st.session_state.vehicle_id:
            missing = completeness(get_vehicle(engine, st.session_state.vehicle_id))
            if missing:
                st.warning("Неповні дані збереженої картки: " + ", ".join(missing))
    with tabs[3]:
        st.subheader("Історія змін")
        records = []
        if st.session_state.vehicle_id:
            for event in list_audit(engine, st.session_state.vehicle_id):
                for field, value in event.new_values.items():
                    if event.old_values.get(field) != value:
                        records.append(
                            {
                                "Час UTC": event.recorded_at.isoformat(),
                                "Автор": event.user_id,
                                "Дія": event.action,
                                "Поле": LABELS.get(field, field),
                                "Було": str(event.old_values.get(field, "")),
                                "Стало": "" if value is None else str(value),
                            }
                        )
        if records:
            st.dataframe(pd.DataFrame(records), hide_index=True, width="stretch")
        else:
            st.info("Історія з'явиться після збереження картки.")
        st.caption("Датовані періоди та узгодження заднім числом — наступний етап.")
    for tab, name in zip(tabs[4:], ("Документи", "Обладнання", "Примітки"), strict=True):
        with tab:
            st.subheader(name)
            st.info("Ця функція передбачена наступним етапом ТЗ.")


def filter_lookup(engine, kind, label, key):
    entries = list_lookup(engine, kind, active_only=False)
    names = {entry.id: entry.name for entry in entries}
    return st.selectbox(
        label,
        [e.id for e in entries],
        index=None,
        format_func=lambda x: names.get(x, "—"),
        key=key,
        placeholder="Усі",
    )


def registry_page(engine, actor):
    st.header("Реєстр техніки")
    st.button(
        "Новий автомобіль",
        type="primary",
        on_click=request_navigation,
        args=(engine, "card"),
        disabled=actor.role == Role.VIEWER,
        key="new_vehicle",
    )
    search = st.text_input("Пошук за номером, VIN, шасі, маркою або моделлю", key="registry_search")
    filters = {}
    with st.expander("Фільтри", expanded=True):
        cols = st.columns(3)
        for col, kind, field in zip(
            cols,
            ("units", "support_services", "vehicle_types"),
            ("unit_id", "support_service_id", "vehicle_type_id"),
            strict=True,
        ):
            with col:
                filters[field] = filter_lookup(engine, kind, LABELS[field], f"filter_{field}")
        cols = st.columns(3)
        for col, field, options in zip(
            cols,
            ("accounting_status", "technical_condition", "technical_category"),
            (ACCOUNTING_STATUSES, TECHNICAL_CONDITIONS, TECHNICAL_CATEGORIES),
            strict=True,
        ):
            with col:
                filters[field] = st.selectbox(
                    LABELS[field], options, index=None, key=f"filter_{field}", placeholder="Усі"
                )
        operated = st.selectbox("Експлуатація", ("Усі", "Експлуатується", "Не експлуатується"))
        filters["is_operated"] = None if operated == "Усі" else operated == "Експлуатується"
        archived = st.checkbox("Показати архівні", key="include_archived")
        incomplete = st.checkbox("Неповні дані", key="incomplete_only")
    vehicles = list_vehicles(
        engine, search=search, include_archived=archived, incomplete_only=incomplete, **filters
    )
    names = {
        kind: {e.id: e.name for e in list_lookup(engine, kind, active_only=False)}
        for kind in (
            "vehicle_brands",
            "vehicle_models",
            "vehicle_types",
            "support_services",
            "units",
        )
    }
    rows = [
        {
            "ID": v.id,
            "Військовий номер": v.military_number or "",
            "Марка / модель": (
                f"{names['vehicle_brands'][v.brand_id]} {names['vehicle_models'][v.model_id]}"
            ),
            "Тип": names["vehicle_types"][v.vehicle_type_id],
            "Служба": names["support_services"][v.support_service_id],
            "Підрозділ": names["units"].get(v.unit_id, ""),
            "Обліковий статус": v.accounting_status,
            "Технічний стан": v.technical_condition,
            "Категорія": v.technical_category,
            "Експлуатується": v.is_operated,
            "Неповні дані": ", ".join(completeness(v)),
        }
        for v in vehicles
    ]
    st.caption(f"Знайдено: {len(rows)}")
    if not rows:
        st.info("Автомобілів за цими умовами немає.")
        return
    frame = pd.DataFrame(rows)
    defaults = [
        "Військовий номер",
        "Марка / модель",
        "Тип",
        "Служба",
        "Обліковий статус",
        "Технічний стан",
    ]
    columns = st.multiselect("Колонки реєстру", list(frame.columns), default=defaults)
    sort_by = st.selectbox("Сортування", list(frame.columns), index=2)
    reverse = st.checkbox("За спаданням")
    frame = frame.sort_values(sort_by, ascending=not reverse, kind="stable")
    st.dataframe(frame[columns], hide_index=True, width="stretch")
    display = {
        row[
            "ID"
        ]: f"{row['Військовий номер'] or 'Без номера'} · {row['Марка / модель']} · {row['ID']}"
        for row in rows
    }
    selected = st.selectbox(
        "Автомобіль", frame["ID"].tolist(), format_func=display.get, key="selected_vehicle"
    )
    st.button(
        "Відкрити картку",
        on_click=request_navigation,
        args=(engine, "card", selected),
        key="open_vehicle",
    )


def dictionaries_page(engine, actor):
    st.header("Довідники")
    kind = st.selectbox(
        "Довідник", list(LOOKUP_LABELS), format_func=LOOKUP_LABELS.get, key="dictionary_kind"
    )
    disabled = actor.role == Role.VIEWER
    if kind == "personnel":
        with Session(engine) as session:
            people = session.exec(select(Personnel)).all()
        if people:
            st.dataframe(pd.DataFrame([p.model_dump() for p in people]), hide_index=True)
        with st.form("personnel_add", clear_on_submit=True):
            first = st.text_input("Ім'я", disabled=disabled)
            last = st.text_input("Прізвище", disabled=disabled)
            patronymic = st.text_input("По батькові", disabled=disabled)
            rank = st.text_input("Звання", disabled=disabled)
            position = st.text_input("Посада", disabled=disabled)
            number = st.text_input("Особистий номер", disabled=disabled)
            unit_id = filter_lookup(engine, "units", "Підрозділ", "personnel_unit")
            if st.form_submit_button("Додати особу", disabled=disabled):
                try:
                    add_person(
                        engine,
                        first,
                        last,
                        actor,
                        patronymic=patronymic or None,
                        rank=rank or None,
                        position=position or None,
                        personal_number=number or None,
                        unit_id=unit_id,
                    )
                    st.session_state.notice = ("success", "Особу додано")
                    st.rerun()
                except ERRORS as exc:
                    st.error(friendly_error(exc))
        return
    brand_id = None
    if kind == "vehicle_models":
        brand_id = filter_lookup(engine, "vehicle_brands", "Марка", "dictionary_brand")
        if brand_id is None:
            st.info("Спочатку оберіть марку.")
            return
    entries = list_lookup(engine, kind, active_only=False, brand_id=brand_id)
    if entries:
        st.dataframe(
            pd.DataFrame([{"ID": e.id, "Назва": e.name, "Активний": e.is_active} for e in entries]),
            hide_index=True,
            width="stretch",
        )
    with st.form(f"add_dictionary_{kind}", clear_on_submit=True):
        name = st.text_input("Назва нового значення", disabled=disabled)
        if st.form_submit_button("Додати значення", disabled=disabled):
            try:
                save_lookup(engine, kind, name, actor, brand_id=brand_id)
                st.session_state.notice = ("success", "Значення додано")
                st.rerun()
            except ERRORS as exc:
                st.error(friendly_error(exc))
    if actor.role == Role.ADMIN and entries:
        names = {e.id: e.name for e in entries}
        entry_id = st.selectbox(
            "Значення для зміни",
            [e.id for e in entries],
            format_func=names.get,
            key=f"edit_lookup_{kind}",
        )
        entry = next(e for e in entries if e.id == entry_id)
        with st.form(f"edit_dictionary_{kind}_{entry_id}"):
            renamed = st.text_input("Назва", value=entry.name)
            active = st.checkbox("Активний", value=entry.is_active)
            if st.form_submit_button("Зберегти довідник"):
                try:
                    save_lookup(
                        engine,
                        kind,
                        renamed,
                        actor,
                        entry_id=entry_id,
                        brand_id=brand_id,
                        is_active=active,
                    )
                    st.session_state.notice = ("success", "Довідник оновлено")
                    st.rerun()
                except ERRORS as exc:
                    st.error(friendly_error(exc))


def main():
    st.set_page_config(page_title="AUTO — Автомобілі", page_icon="🚙", layout="wide")
    st.title("AUTO · Автомобілі")
    path = db_path()
    if not path.exists():
        st.info(
            "БД ще не створена. Виконайте у PowerShell: "
            ".\\.venv\\Scripts\\python.exe -m auto.cli migrate"
        )
        return
    engine = cached_engine(path)
    try:
        require_current_schema(engine)
        actor = local_actor()
    except (RuntimeError, ValueError, SQLAlchemyError) as exc:
        st.error(str(exc))
        return
    st.session_state.setdefault("page", "registry")
    st.session_state.setdefault("pending", None)
    with st.sidebar:
        st.header("AUTO")
        st.caption(f"{actor.user_id} · {actor.role.value}")
        st.button(
            "Реєстр", on_click=request_navigation, args=(engine, "registry"), key="nav_registry"
        )
        st.button(
            "Довідники", on_click=request_navigation, args=(engine, "dictionaries"), key="nav_dict"
        )
        st.caption("Локальний режим. Чернетка існує лише в поточній сесії браузера.")
    if notice := st.session_state.pop("notice", None):
        getattr(st, notice[0])(notice[1])
    if st.session_state.pending:
        st.warning("Є незбережені зміни. Збережіть їх перед переходом або відкиньте.")
        cols = st.columns(3)
        for col, label, decision in zip(
            cols,
            ("Зберегти й перейти", "Відкинути й перейти", "Залишитися"),
            ("save", "discard", "stay"),
            strict=True,
        ):
            col.button(
                label,
                on_click=resolve_navigation,
                args=(engine, actor, decision),
                key=f"pending_{decision}",
            )
    if st.session_state.page == "card":
        card_page(engine, actor)
    elif st.session_state.page == "dictionaries":
        dictionaries_page(engine, actor)
    else:
        registry_page(engine, actor)
