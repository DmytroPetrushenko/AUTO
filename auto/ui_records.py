from datetime import date

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
from auto.ui_drafts import Editor


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
        editor = Editor(
            "document",
            f"{vehicle_id}_{chosen}",
            current[chosen].version if chosen else None,
            {
                "kind": latest.get("kind", ""),
                "number": latest.get("number") or "",
                "document_date": date.fromisoformat(latest["document_date"])
                if latest.get("document_date")
                else None,
                "issuer": latest.get("issuer") or "",
                "note": latest.get("note") or "",
                "files": [],
            },
        )
        with st.container():
            kind = editor.widget("text_input", "kind", "Тип документа", disabled=disabled)
            number = editor.widget("text_input", "number", "Номер документа", disabled=disabled)
            day = editor.widget("date_input", "document_date", "Дата документа", disabled=disabled)
            issuer = editor.widget("text_input", "issuer", "Видавець", disabled=disabled)
            note = editor.widget("text_area", "note", "Примітка до документа", disabled=disabled)
            uploads = editor.widget(
                "file_uploader",
                "files",
                "Вкладення версії (до 20 МБ кожне)",
                type=["pdf", "docx", "xlsx", "jpg", "jpeg", "png"],
                accept_multiple_files=True,
                disabled=disabled,
            )
            if st.button("Зберегти версію документа", disabled=disabled):
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
                        expected_version=editor.version,
                    )
                    editor.reset()
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
        editor = Editor(
            "equipment",
            f"{vehicle_id}_{chosen}",
            item.version if item else None,
            {
                "name": item.name if item else "",
                "quantity": item.quantity if item else 1.0,
                "installed_on": item.installed_on if item else None,
                "is_removed": item.is_removed if item else False,
                "removed_on": item.removed_on if item else None,
                "note": item.note or "" if item else "",
            },
        )
        with st.container():
            name = editor.widget("text_input", "name", "Назва обладнання", disabled=disabled)
            qty = editor.widget(
                "number_input", "quantity", "Кількість", min_value=0.01, disabled=disabled
            )
            installed = editor.widget(
                "date_input", "installed_on", "Дата встановлення", disabled=disabled
            )
            removed = editor.widget("checkbox", "is_removed", "Зняте", disabled=disabled)
            removed_on = editor.widget("date_input", "removed_on", "Дата зняття", disabled=disabled)
            note = editor.widget("text_area", "note", "Примітка до обладнання", disabled=disabled)
            if st.button("Зберегти обладнання", disabled=disabled):
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
                        expected_version=editor.version,
                    )
                    editor.reset()
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
        editor = Editor(
            "note",
            f"{vehicle_id}_{chosen}",
            item.version if item else None,
            {
                "text": item.text if item else "",
                "is_hidden": item.is_hidden if item else False,
            },
        )
        with st.container():
            text = editor.widget("text_area", "text", "Текст примітки", disabled=disabled)
            is_hidden = editor.widget("checkbox", "is_hidden", "Приховати", disabled=disabled)
            if st.button("Зберегти примітку", disabled=disabled):
                try:
                    save_note(
                        engine,
                        vehicle_id,
                        text,
                        actor,
                        note_id=chosen,
                        expected_version=editor.version,
                        is_hidden=is_hidden,
                    )
                    editor.reset()
                    st.rerun()
                except ui.ERRORS as exc:
                    st.error(ui.friendly_error(exc))
