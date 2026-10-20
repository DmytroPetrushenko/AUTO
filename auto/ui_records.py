import pandas as pd
import streamlit as st

from auto.records import (
    list_documents,
    list_equipment,
    list_notes,
    note_versions,
    read_attachment,
    save_document,
    save_equipment,
    save_note,
)
from auto.services import Role


def panels(engine, actor, tabs):
    from auto import ui

    vehicle_id = st.session_state.vehicle_id
    disabled = actor.role == Role.VIEWER
    if not vehicle_id:
        for tab in tabs:
            with tab:
                st.info("Спочатку збережіть основну картку.")
        return
    with tabs[0]:
        st.subheader("Документи та вкладення")
        docs = list_documents(engine, vehicle_id, actor)
        current = {r["document"].id: r["document"] for r in docs}
        labels = {
            r[
                "document"
            ].id: f"{r['revision'].details['kind']} {r['revision'].details.get('number') or ''}"
            for r in reversed(docs)
        }
        chosen = st.selectbox(
            "Документ для нової версії",
            [None] + list(current),
            format_func=lambda x: labels.get(x, "Новий документ"),
            key="doc_edit",
        )
        latest = next((r["revision"].details for r in docs if r["document"].id == chosen), {})
        with st.form(f"document_{vehicle_id}_{chosen}"):
            kind = st.text_input(
                "Тип документа", value=latest.get("kind", ""), disabled=disabled, key="doc_kind"
            )
            number = st.text_input(
                "Номер документа", value=latest.get("number") or "", disabled=disabled
            )
            from datetime import date

            day = st.date_input(
                "Дата документа",
                value=date.fromisoformat(latest["document_date"])
                if latest.get("document_date")
                else None,
                disabled=disabled,
            )
            issuer = st.text_input("Видавець", value=latest.get("issuer") or "", disabled=disabled)
            note = st.text_area(
                "Примітка до документа", value=latest.get("note") or "", disabled=disabled
            )
            uploads = st.file_uploader(
                "Вкладення версії (до 20 МБ кожне)",
                type=["pdf", "docx", "xlsx", "jpg", "jpeg", "png"],
                accept_multiple_files=True,
                disabled=disabled,
                key="doc_uploads",
            )
            if st.form_submit_button("Зберегти версію документа", disabled=disabled):
                try:
                    save_document(
                        engine,
                        vehicle_id,
                        dict(
                            kind=kind,
                            number=number or None,
                            document_date=day,
                            issuer=issuer or None,
                            note=note or None,
                        ),
                        [(f.name, f.getvalue()) for f in uploads],
                        actor,
                        document_id=chosen,
                        expected_version=current[chosen].version if chosen else None,
                    )
                    st.success("Версію збережено")
                    st.rerun()
                except ui.ERRORS as exc:
                    st.error(ui.friendly_error(exc))
        for entry in docs:
            rev = entry["revision"]
            with st.expander(
                f"{rev.details['kind']} {rev.details.get('number') or ''} · версія {rev.version}"
            ):
                st.write(rev.details)
                st.caption(f"{rev.created_by} · {rev.created_at.isoformat()} UTC")
                for f in entry["files"]:
                    try:
                        name, data = read_attachment(engine, vehicle_id, f.id, actor)
                        st.download_button(name, data, file_name=name, key="file_" + f.id)
                    except ui.ERRORS as exc:
                        st.error(ui.friendly_error(exc))
    with tabs[1]:
        st.subheader("Обладнання")
        mode = st.selectbox(
            "Показувати обладнання", ["Встановлене", "Зняте", "Усе"], key="equipment_filter"
        )
        all_items = list_equipment(engine, vehicle_id, actor)
        visible = [i for i in all_items if mode == "Усе" or i.is_removed == (mode == "Зняте")]
        if visible:
            st.dataframe(pd.DataFrame([i.model_dump() for i in visible]), hide_index=True)
        names = {i.id: i.name for i in all_items}
        chosen = st.selectbox(
            "Позиція обладнання",
            [None] + list(names),
            format_func=lambda x: names.get(x, "Нова позиція"),
            key="equipment_edit",
        )
        item = next((i for i in all_items if i.id == chosen), None)
        with st.form(f"equipment_{chosen}_{item.version if item else 0}"):
            name = st.text_input(
                "Назва обладнання", value=item.name if item else "", disabled=disabled
            )
            qty = st.number_input(
                "Кількість", min_value=0.01, value=item.quantity if item else 1.0, disabled=disabled
            )
            installed = st.date_input(
                "Дата встановлення", value=item.installed_on if item else None, disabled=disabled
            )
            removed = st.checkbox(
                "Зняте", value=item.is_removed if item else False, disabled=disabled
            )
            removed_on = st.date_input(
                "Дата зняття", value=item.removed_on if item else None, disabled=disabled
            )
            note = st.text_area(
                "Примітка до обладнання", value=item.note or "" if item else "", disabled=disabled
            )
            if st.form_submit_button("Зберегти обладнання", disabled=disabled):
                try:
                    save_equipment(
                        engine,
                        vehicle_id,
                        dict(
                            name=name,
                            quantity=qty,
                            installed_on=installed,
                            removed_on=removed_on,
                            is_removed=removed,
                            note=note or None,
                        ),
                        actor,
                        equipment_id=chosen,
                        expected_version=item.version if item else None,
                    )
                    st.rerun()
                except ui.ERRORS as exc:
                    st.error(ui.friendly_error(exc))
    with tabs[2]:
        st.subheader("Примітки")
        hidden = st.checkbox("Показати приховані примітки", key="notes_hidden")
        notes = list_notes(engine, vehicle_id, actor, include_hidden=True)
        for n in notes:
            if hidden or not n.is_hidden:
                st.markdown(n.text)
                st.caption(f"{n.created_by} · {n.created_at.isoformat()} UTC · версія {n.version}")
                with st.expander("Попередні версії " + n.id):
                    for v in note_versions(engine, vehicle_id, n.id, actor):
                        st.write(
                            f"v{v.version} · {v.created_by} · {v.created_at.isoformat()} UTC",
                            v.text,
                        )
        names = {n.id: f"{n.text[:50]} · v{n.version}" for n in notes}
        chosen = st.selectbox(
            "Примітка для редагування",
            [None] + list(names),
            format_func=lambda x: names.get(x, "Нова примітка"),
            key="note_edit",
        )
        item = next((n for n in notes if n.id == chosen), None)
        with st.form(f"note_{chosen}_{item.version if item else 0}"):
            text = st.text_area(
                "Текст примітки",
                value=item.text if item else "",
                disabled=disabled,
                key="note_text",
            )
            is_hidden = st.checkbox(
                "Приховати", value=item.is_hidden if item else False, disabled=disabled
            )
            if st.form_submit_button("Зберегти примітку", disabled=disabled):
                try:
                    save_note(
                        engine,
                        vehicle_id,
                        text,
                        actor,
                        note_id=chosen,
                        expected_version=item.version if item else None,
                        is_hidden=is_hidden,
                    )
                    st.rerun()
                except ui.ERRORS as exc:
                    st.error(ui.friendly_error(exc))
