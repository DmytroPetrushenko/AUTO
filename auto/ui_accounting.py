import pandas as pd
import streamlit as st

from auto.accounting import add_reading, current_reading, list_norms, preview_norm, save_norm
from auto.services import Role, get_vehicle
from auto.ui_drafts import Editor


def panel(engine, actor):
    from auto import ui

    vid = st.session_state.vehicle_id
    if not vid:
        return
    disabled = actor.role == Role.VIEWER
    st.subheader("Показники напрацювання")
    reading = current_reading(engine, vid)
    if reading:
        st.write(
            f"{reading.value:g} {reading.unit} · {reading.measured_on} · {reading.source} · "
            + ("Підтверджений" if reading.confirmed else "Попередній")
        )
    else:
        st.info("Немає показників")
    vehicle = get_vehicle(engine, vid)
    if vehicle.work_unit != "без лічильника":
        editor = Editor(
            "reading",
            vid,
            None,
            {
                "value": None,
                "day": None,
                "source": "",
                "confirmed": False,
            },
        )
        with st.container():
            value = editor.widget(
                "number_input", "value", "Початковий показник", min_value=0.0, disabled=disabled
            )
            day = editor.widget(
                "date_input", "day", "Дата початкового показника", disabled=disabled
            )
            source = editor.widget(
                "text_input", "source", "Джерело початкового показника", disabled=disabled
            )
            confirmed = editor.widget(
                "checkbox", "confirmed", "Початковий показник підтверджений", disabled=disabled
            )
            if st.button("Зберегти початковий показник", disabled=disabled):
                try:
                    add_reading(
                        engine,
                        vid,
                        dict(
                            value=value,
                            unit=vehicle.work_unit,
                            measured_on=day,
                            source=source,
                            confirmed=confirmed,
                        ),
                        actor,
                    )
                    editor.reset()
                    st.rerun()
                except ui.ERRORS as exc:
                    st.error(ui.friendly_error(exc))
    st.caption(
        "Поточний показник обчислюється з журналу та початкового показника; "
        "ручного поля поточного показника немає."
    )
    st.subheader("Датовані норми пального")
    norms = list_norms(engine, vid)
    if norms:
        st.dataframe(pd.DataFrame([r.model_dump() for r in norms]), hide_index=True)
    ids = {r.id: r for r in norms}
    chosen = st.selectbox(
        "Норма для виправлення",
        [None] + list(ids),
        format_func=lambda x: str(ids[x].valid_from) if x else "Нова норма",
        key="norm_edit_" + vid,
    )
    item = ids.get(chosen)
    editor = Editor(
        "norm",
        f"{vid}_{chosen}",
        item.version if item else None,
        {
            "valid_from": item.valid_from if item else None,
            "rate": item.rate if item else None,
            "unit": item.unit if item else "л/100 км",
            "order_number": item.order_number if item else "",
            "order_date": item.order_date if item else None,
        },
    )
    with st.container():
        day = editor.widget("date_input", "valid_from", "Початок дії норми", disabled=disabled)
        rate = editor.widget(
            "number_input", "rate", "Базова норма", min_value=0.0, disabled=disabled
        )
        options = ["л/100 км", "л/мотогодину"]
        unit = editor.widget(
            "selectbox", "unit", "Одиниця норми", options=options, disabled=disabled
        )
        order = editor.widget("text_input", "order_number", "Номер наказу", disabled=disabled)
        order_day = editor.widget("date_input", "order_date", "Дата наказу", disabled=disabled)
        if st.button("Переглянути норму", disabled=disabled):
            try:
                data = dict(
                    valid_from=day, rate=rate, unit=unit, order_number=order, order_date=order_day
                )
                p = preview_norm(
                    engine,
                    vid,
                    data,
                    actor,
                    norm_id=chosen,
                    expected_version=editor.version,
                )
                st.session_state["norm_review_" + vid] = (
                    p,
                    data,
                    chosen,
                    editor.version,
                )
                st.session_state["norm_confirm_" + vid] = False
            except ui.ERRORS as exc:
                st.error(ui.friendly_error(exc))
    if review := st.session_state.get("norm_review_" + vid):
        p, data, nid, version = review
        st.markdown("**Було → стане: норми й сусідні межі**")
        st.write("Було", p["before"])
        st.write("Стане", p["after"])
        if p["blocked_documents"]:
            st.error("Блокують документи: " + ", ".join(p["blocked_documents"]))
        confirmed = st.checkbox(
            "Підтверджую норму й межі періодів", key="norm_confirm_" + vid, disabled=disabled
        )
        if st.button("Підтвердити норму", disabled=disabled or bool(p["blocked_documents"])):
            try:
                save_norm(
                    engine,
                    vid,
                    data,
                    actor,
                    preview_token=p["token"],
                    confirmed=confirmed,
                    norm_id=nid,
                    expected_version=version,
                )
                st.session_state.vehicle_version = get_vehicle(engine, vid).version
                st.session_state.pop("norm_review_" + vid, None)
                # The reviewed editor may differ from the currently selected norm.
                reviewed_editor = Editor("norm", f"{vid}_{nid}", version, {})
                reviewed_editor.reset()
                st.rerun()
            except ui.ERRORS as exc:
                st.error(ui.friendly_error(exc))
    st.caption(
        "Коригування −15% належить окремому дорожньому листу. "
        "Знімки застосованих листів не перераховуються."
    )
