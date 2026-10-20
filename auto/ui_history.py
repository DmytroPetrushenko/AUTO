"""Explicit review and personnel/history editing for the local Streamlit UI."""

import pandas as pd
import streamlit as st

from auto.constants import ACCOUNTING_STATUSES, TECHNICAL_CATEGORIES, TECHNICAL_CONDITIONS
from auto.history import apply_history, delete_vehicle, list_history, preview_history
from auto.personnel import PersonInput, list_people, save_person
from auto.services import Role, list_audit, list_lookup

KIND_LABELS = {
    "unit": "Підрозділ",
    "service": "Служба забезпечення",
    "mvo": "МВО",
    "status": "Статуси й експлуатація",
    "military_number": "Військовий номер",
    "vin": "VIN",
    "chassis_number": "Шасі / рама",
    "serial_number": "Заводський номер",
}


def person_label(person):
    return " ".join(filter(None, [person.last_name, person.first_name, person.patronymic]))


def _ui():
    from auto import ui

    return ui


def reset_review():
    st.session_state.card_preview = None
    st.session_state.history_preview = None
    st.session_state.pop("bundle_review", None)


def set_review(key, plan):
    st.session_state[key] = plan
    prefix = "card" if key == "card_preview" else "history"
    st.session_state[f"{prefix}_confirmed"] = False
    for duplicate in plan["duplicates"]:
        field = duplicate["field"]
        st.session_state[f"{prefix}_approve_{field}"] = False
        st.session_state[f"{prefix}_reason_{field}"] = ""


def approval_values(prefix):
    plan = st.session_state.get(f"{prefix}_preview")
    return (
        {
            d["field"]: {
                "confirmed": st.session_state.get(f"{prefix}_approve_{d['field']}", False),
                "reason": st.session_state.get(f"{prefix}_reason_{d['field']}", ""),
            }
            for d in plan["duplicates"]
        }
        if plan
        else {}
    )


def periods_frame(engine, events):
    names = {}
    for field, kind in (("unit_id", "units"), ("support_service_id", "support_services")):
        names[field] = {e.id: e.name for e in list_lookup(engine, kind, active_only=False)}
    names["mvo_id"] = {p.id: person_label(p) for p in list_people(engine)}
    rows = []
    for event in events:
        values = []
        for field, value in event["value"].items():
            rendered = (
                names[field].get(value, "—")
                if field in names
                else ("—" if value is None else str(value))
            )
            values.append(f"{_ui().LABELS.get(field, field)}: {rendered}")
        rows.append(
            {
                "Тип": KIND_LABELS[event["kind"]],
                "Початок": str(event["valid_from"]) if event["valid_from"] else "Невідомо",
                "Кінець (не включно)": str(event["valid_to"]) if event["valid_to"] else "Відкритий",
                "Значення": "; ".join(values),
            }
        )
    return pd.DataFrame(rows)


def review_panel(engine, actor, prefix, confirm_callback):
    plan = st.session_state.get(f"{prefix}_preview")
    if not plan:
        return
    st.subheader("Перегляд «було → стане»")
    st.caption("Дані ще не записані. Межі періодів зміняться лише після підтвердження.")
    if prefix == "card" and st.session_state.get("bundle_review"):
        st.markdown("**Уся картка буде збережена однією транзакцією, включно з окремими формами.**")
        st.write(st.session_state.bundle_review["operations"])
    changed = set(plan["changed_kinds"])
    left, right = st.columns(2)
    with left:
        st.markdown("**Було**")
        events = [e for e in plan["before"] if e["kind"] in changed]
        if events:
            st.dataframe(periods_frame(engine, events), hide_index=True, width="stretch")
        else:
            st.caption("Періодів немає")
    with right:
        st.markdown("**Стане**")
        events = [e for e in plan["after"] if e["kind"] in changed]
        if events:
            st.dataframe(periods_frame(engine, events), hide_index=True, width="stretch")
    if plan["blocked_documents"]:
        st.error("Зміну блокують застосовані документи: " + ", ".join(plan["blocked_documents"]))
    denied = False
    for duplicate in plan["duplicates"]:
        field = duplicate["field"]
        st.warning(f"Збіг {_ui().LABELS[field]}: {duplicate['value']}")
        st.dataframe(
            pd.DataFrame(duplicate["matches"]).rename(
                columns={
                    "vehicle_id": "ID картки",
                    "military_number": "Військовий номер",
                    "status": "Обліковий статус",
                    "brand_id": "ID марки",
                    "model_id": "ID моделі",
                }
            ),
            hide_index=True,
            width="stretch",
        )
        for match in duplicate["matches"]:
            st.button(
                "Відкрити картку зі збігом: " + match["vehicle_id"],
                key=f"{prefix}_match_{field}_{match['vehicle_id']}",
                on_click=_ui().request_navigation,
                args=(engine, "card", match["vehicle_id"]),
            )
        if duplicate["approval_required"]:
            allowed = field == "military_number" or actor.role == Role.ADMIN
            denied |= not allowed
            if not allowed:
                st.error("Погодження цього фізичного ідентифікатора доступне лише адміністратору.")
            st.checkbox(
                "Погоджую повтор: " + _ui().LABELS[field],
                key=f"{prefix}_approve_{field}",
                disabled=not allowed,
            )
            st.text_input(
                "Причина повтору: " + _ui().LABELS[field],
                key=f"{prefix}_reason_{field}",
                disabled=not allowed,
            )
    st.checkbox("Підтверджую показані зміни та узгодження періодів", key=f"{prefix}_confirmed")
    st.button(
        "Підтвердити та зберегти",
        key=f"{prefix}_commit",
        type="primary",
        on_click=confirm_callback,
        args=(engine, actor),
        disabled=bool(plan["blocked_documents"]) or denied or actor.role == Role.VIEWER,
    )
    st.button("Закрити перегляд", key=f"{prefix}_close", on_click=reset_review)


def confirm_history(engine, actor):
    request = st.session_state.history_request
    plan = st.session_state.history_preview
    try:
        target = st.session_state.pending
        apply_history(
            engine,
            st.session_state.vehicle_id,
            request["event_id"],
            request["value"],
            request["date"],
            actor,
            request["version"],
            plan["token"],
            confirmed=st.session_state.get("history_confirmed", False),
            approvals=approval_values("history"),
        )
        _ui().start_card(engine, st.session_state.vehicle_id)
        if target:
            _ui().navigate(engine, *target)
        st.session_state.notice = ("success", "Історію збережено; межі періодів узгоджено")
    except _ui().ERRORS as exc:
        st.session_state.notice = ("error", _ui().friendly_error(exc))


def _reference_select(engine, field, current, key, disabled=False):
    if field == "mvo_id":
        options = {
            p.id: person_label(p) + (" (неактивний)" if not p.is_active else "")
            for p in list_people(engine)
            if p.is_active or p.id == current
        }
    else:
        kind = "units" if field == "unit_id" else "support_services"
        options = {
            e.id: e.name
            for e in list_lookup(engine, kind, active_only=False)
            if e.is_active or e.id == current
        }
    ids = list(options)
    return st.selectbox(
        _ui().LABELS[field],
        ids,
        index=ids.index(current) if current in ids else None,
        format_func=lambda x: options.get(x, "—"),
        key=key,
        disabled=disabled,
    )


def history_panel(engine, actor):
    vehicle_id = st.session_state.vehicle_id
    if not vehicle_id:
        st.info("Історія з'явиться після збереження картки.")
        return
    events = list_history(engine, vehicle_id)
    st.subheader("Датовані періоди")
    st.caption("Початок включно, кінець не включно. «Невідомо» не замінюється вигаданою датою.")
    selected_kind = st.selectbox(
        "Тип історії", list(KIND_LABELS), format_func=KIND_LABELS.get, key="history_kind"
    )
    chosen = [e for e in events if e["kind"] == selected_kind]
    st.dataframe(periods_frame(engine, chosen), hide_index=True, width="stretch")
    if chosen and actor.role != Role.VIEWER:
        ids = [e["id"] for e in chosen]
        labels = {
            e["id"]: f"{e['valid_from'] or 'Невідомо'} → {e['valid_to'] or 'Відкритий'}"
            for e in chosen
        }
        event_id = st.selectbox(
            "Період для виправлення",
            ids,
            format_func=labels.get,
            key=f"history_event_{selected_kind}",
        )
        event = next(e for e in chosen if e["id"] == event_id)
        version = st.session_state.vehicle_version
        with st.form(f"history_edit_{event_id}_{version}"):
            day = st.date_input(
                "Дата фактичної події",
                value=event["valid_from"],
                key=f"history_date_{event_id}_{version}",
            )
            payload = {}
            for field, current in event["value"].items():
                key = f"history_value_{field}_{event_id}_{version}"
                if field in ("unit_id", "support_service_id", "mvo_id"):
                    payload[field] = _reference_select(engine, field, current, key)
                elif field == "is_operated":
                    payload[field] = st.checkbox(_ui().LABELS[field], value=current, key=key)
                elif field in ("accounting_status", "technical_condition", "technical_category"):
                    options = {
                        "accounting_status": ACCOUNTING_STATUSES,
                        "technical_condition": TECHNICAL_CONDITIONS,
                        "technical_category": TECHNICAL_CATEGORIES,
                    }[field]
                    payload[field] = st.selectbox(
                        _ui().LABELS[field], options, index=options.index(current), key=key
                    )
                else:
                    payload[field] = (
                        st.text_input(_ui().LABELS[field], value=current or "", key=key) or None
                    )
            if st.form_submit_button("Переглянути виправлення", key="history_preview_button"):
                try:
                    if st.session_state.draft != st.session_state.baseline:
                        raise _ui().BusinessError(
                            "Спочатку збережіть або скасуйте зміни основної картки"
                        )
                    plan = preview_history(
                        engine, vehicle_id, event_id, payload, day, actor, version
                    )
                    st.session_state.history_request = {
                        "event_id": event_id,
                        "value": payload,
                        "date": day,
                        "version": version,
                    }
                    set_review("history_preview", plan)
                except _ui().ERRORS as exc:
                    st.error(_ui().friendly_error(exc))
        review_panel(engine, actor, "history", confirm_history)
    st.subheader("Аудит змін")
    events_audit = list_audit(engine, vehicle_id)
    with st.expander("Фільтри аудиту"):
        author = st.text_input("Користувач", key="audit_user")
        actions = st.multiselect(
            "Тип зміни", sorted({e.action for e in events_audit}), key="audit_actions"
        )
        since = st.date_input("Від дати (UTC)", value=None, key="audit_since")
        until = st.date_input("До дати (UTC)", value=None, key="audit_until")
    rows = []
    for event in events_audit:
        if (
            (author and author.casefold() not in event.user_id.casefold())
            or (actions and event.action not in actions)
            or (since and event.recorded_at.date() < since)
            or (until and event.recorded_at.date() > until)
        ):
            continue
        for field, value in event.new_values.items():
            if event.old_values.get(field) != value:
                rows.append(
                    {
                        "Час UTC": event.recorded_at.isoformat(),
                        "Автор": event.user_id,
                        "Дія": event.action,
                        "Поле": _ui().LABELS.get(field, field),
                        "Було": str(event.old_values.get(field, "")),
                        "Стало": "" if value is None else str(value),
                    }
                )
    if rows:
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    else:
        st.info("Змін за цими умовами немає.")


def people_panel(engine, actor):
    people = list_people(engine)
    if people:
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "ID": p.id,
                        "ПІБ": person_label(p),
                        "Звання": p.rank,
                        "Посада": p.position,
                        "Активний": p.is_active,
                        "Версія": p.version,
                    }
                    for p in people
                ]
            ),
            hide_index=True,
            width="stretch",
        )
    ids = [None] + [p.id for p in people]
    labels = {p.id: person_label(p) for p in people}
    selected = st.selectbox(
        "Особа для редагування",
        ids,
        format_func=lambda x: labels.get(x, "Нова особа"),
        key="person_edit",
    )
    person = next((p for p in people if p.id == selected), None)
    snapshots = st.session_state.setdefault("person_snapshots", {})
    opened = snapshots.setdefault(selected, person.model_dump()) if person else None
    if person:
        with st.expander("Історія змін особи"):
            rows = [
                {
                    "Час UTC": e.recorded_at.isoformat(),
                    "Автор": e.user_id,
                    "Дія": e.action,
                    "Було": str(e.old_values),
                    "Стало": str(e.new_values),
                }
                for e in list_audit(engine, str(person.id), entity_type="personnel")
            ]
            st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    disabled = actor.role == Role.VIEWER or (person is not None and actor.role != Role.ADMIN)
    with st.form(f"person_form_{selected}_{opened['version'] if opened else 0}"):
        data = {}
        fields = {
            "first_name": "Ім'я",
            "last_name": "Прізвище",
            "patronymic": "По батькові",
            "rank": "Звання",
            "position": "Посада",
            "personal_number": "Особистий номер",
        }
        for field, label in fields.items():
            data[field] = st.text_input(
                label,
                value=opened[field] or "" if opened else "",
                disabled=disabled,
                key=f"person_{field}_{selected}",
            )
        data["unit_id"] = _reference_select(
            engine,
            "unit_id",
            opened["unit_id"] if opened else None,
            f"person_unit_{selected}",
            disabled=disabled,
        )
        data["is_active"] = st.checkbox(
            "Активна особа",
            value=opened["is_active"] if opened else True,
            disabled=disabled or actor.role != Role.ADMIN,
        )
        if st.form_submit_button("Зберегти особу" if person else "Додати особу", disabled=disabled):
            try:
                save_person(
                    engine,
                    PersonInput.model_validate(data),
                    actor,
                    person_id=selected,
                    expected_version=opened["version"] if opened else None,
                )
                snapshots.pop(selected, None)
                st.session_state.notice = ("success", "Персонал збережено")
                st.rerun()
            except _ui().ERRORS as exc:
                st.error(_ui().friendly_error(exc))
    if person and st.button("Перезавантажити збережену особу (скасувати чернетку)"):
        snapshots.pop(selected, None)
        for field in (*fields, "unit"):
            st.session_state.pop(f"person_{field}_{selected}", None)
        st.rerun()


def remove_card(engine, actor):
    try:
        delete_vehicle(
            engine,
            st.session_state.vehicle_id,
            actor,
            expected_version=st.session_state.vehicle_version,
            reason=st.session_state.get("delete_reason", ""),
            confirmed=st.session_state.get("delete_confirmed", False),
        )
        reset_review()
        _ui().navigate(engine, "registry")
        st.session_state.notice = ("success", "Помилкову картку видалено; аудит збережено")
    except _ui().ERRORS as exc:
        st.session_state.notice = ("error", _ui().friendly_error(exc))


def deletion_panel(engine, actor):
    if actor.role != Role.ADMIN or not st.session_state.vehicle_id:
        return
    with st.expander("Видалення помилкової картки"):
        st.caption(
            "Залежності перевіряються сервером. Автоматична початкова історія не блокує видалення."
        )
        st.text_input("Причина видалення", key="delete_reason")
        st.checkbox("Підтверджую видалення помилкової картки", key="delete_confirmed")
        st.button(
            "Видалити помилкову картку",
            key="delete_card",
            on_click=remove_card,
            args=(engine, actor),
        )
