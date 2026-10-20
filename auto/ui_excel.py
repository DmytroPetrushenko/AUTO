import hashlib

import pandas as pd
import streamlit as st

from auto.excel import (
    HEADERS,
    INPUT_FIELDS,
    apply_import,
    export_full,
    export_screen,
    headers,
    import_log,
    import_template,
    inspect_workbook,
    preview_import,
    workbook_bytes,
)
from auto.history import IDENTIFIERS
from auto.services import Role, list_vehicles


@st.cache_data(show_spinner=False, max_entries=3, ttl=600)
def workbook_info(content):
    return inspect_workbook(content)


@st.cache_data(show_spinner=False, max_entries=6, ttl=600)
def workbook_headers(content, sheet, header_row):
    return headers(content, sheet, header_row)


def exports(engine, actor, frame, vehicles):
    st.download_button(
        "Експорт «Як на екрані»",
        export_screen(frame, actor),
        file_name="AUTO-view.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    scope = st.selectbox(
        "Обсяг повного експорту",
        ["Поточна вибірка", "Усі записи, включно з архівом"],
        key="export_scope",
    )
    selected = (
        vehicles if scope == "Поточна вибірка" else list_vehicles(engine, include_archived=True)
    )
    export_key = hashlib.sha256(
        repr((scope, [v.model_dump() for v in selected])).encode()
    ).hexdigest()
    if st.button("Підготувати повний експорт"):
        st.session_state.export_bytes = (
            export_key,
            export_full(engine, actor, vehicles=vehicles, all_records=scope != "Поточна вибірка"),
        )
    stored = st.session_state.get("export_bytes")
    if stored and stored[0] == export_key:
        st.download_button("Завантажити повний експорт", stored[1], file_name="AUTO-full.xlsx")


def import_page(engine, actor):
    from auto import ui

    st.header("Excel: імпорт із попереднім переглядом")
    st.download_button("Шаблон AUTO", import_template(), file_name="AUTO-template.xlsx")
    st.caption(
        "Довідники мають містити потрібні активні значення. "
        "Невідомі марки/моделі виправте в перегляді або додайте у довідники."
    )
    if actor.role == Role.VIEWER:
        st.info("Переглядач не імпортує дані.")
        return
    upload = st.file_uploader("XLSX для імпорту", type=["xlsx"], key="import_upload")
    if upload:
        content = upload.getvalue()
        key = hashlib.sha256(content).hexdigest()
        try:
            info = workbook_info(content)
            st.dataframe(pd.DataFrame(info), hide_index=True)
            sheet = st.selectbox("Аркуш", [i["sheet"] for i in info], key="import_sheet_" + key)
            head = st.number_input("Рядок заголовків", min_value=1, max_value=50, value=1)
            cols = workbook_headers(content, sheet, int(head))
            with st.expander("Зіставлення колонок", expanded=True):
                mapping = {}
                for field in INPUT_FIELDS:
                    default = next(
                        (
                            c
                            for c, name in cols.items()
                            if name
                            in (field, HEADERS.get(field), "id" if field == "vehicle_id" else "")
                        ),
                        "",
                    )
                    ids = [""] + list(cols)
                    chosen = st.selectbox(
                        HEADERS.get(field, ui.LABELS.get(field, field)),
                        ids,
                        index=ids.index(default),
                        format_func=lambda x: cols.get(x, "Не переносити"),
                        key=f"map_{key}_{sheet}_{head}_{field}",
                    )
                    if chosen:
                        mapping[field] = chosen
            use_cache = st.checkbox("Явно використати кешовані значення формул (без перерахунку)")
            st.caption(
                "Числові й неоднозначні ідентифікатори потребують ручного виправлення. "
                "Координати не мають автоматичного зіставлення."
            )
            if st.button("Побудувати попередній перегляд"):
                st.session_state.import_preview = preview_import(
                    engine,
                    content,
                    mapping,
                    actor,
                    sheet=sheet,
                    header_row=int(head),
                    use_cached=use_cache,
                    source_name=upload.name,
                )
                st.session_state.import_confirmed = False
            p = st.session_state.get("import_preview")
            matches = (
                p
                and p["source_hash"] == key
                and all(
                    p["settings"][k] == value
                    for k, value in {
                        "mapping": mapping,
                        "sheet": sheet,
                        "header_row": int(head),
                        "use_cached": use_cache,
                    }.items()
                )
            )
            if p and not matches:
                st.warning("Налаштування змінилися. Побудуйте новий попередній перегляд.")
            if matches:
                rows = [
                    {
                        "Рядок": r["row"],
                        "Помилки": " | ".join(r["errors"]),
                        "Збіги": str(r["duplicates"] + r["incoming"]),
                        "Типові значення (не факти джерела)": str(r["applied_defaults"]),
                        "ID не перевірено": str(r["duplicate_unchecked_fields"]),
                    }
                    for r in p["rows"]
                ]
                st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
                with st.expander("Ручні виправлення кандидатів"):
                    identifiers = {}
                    for row in p["rows"]:
                        for field in IDENTIFIERS:
                            original = row["input"].get(field)
                            if original is not None and not isinstance(original, str):
                                value = st.text_input(
                                    f"Рядок {row['row']}: уточніть {HEADERS.get(field, field)} "
                                    f"як текст (у файлі {original!r})",
                                    key=f"id_text_{p['token']}_{row['row']}_{field}",
                                )
                                if value:
                                    identifiers.setdefault(str(row["row"]), {})[field] = value
                    edit = pd.DataFrame([{"row": r["row"], **r["input"]} for r in p["rows"]])
                    edited = st.data_editor(
                        edit, disabled=["row"], key="import_editor_" + p["token"], num_rows="fixed"
                    )
                    if st.button("Перевірити виправлені рядки"):
                        overrides = {
                            str(int(r["row"])): {k: v for k, v in r.items() if k != "row"}
                            for r in edited.to_dict("records")
                        }
                        for row, fields in identifiers.items():
                            overrides[row].update(fields)
                        st.session_state.import_preview = preview_import(
                            engine,
                            content,
                            p["settings"]["mapping"],
                            actor,
                            **{
                                k: v
                                for k, v in p["settings"].items()
                                if k not in ("mapping", "overrides")
                            },
                            overrides=overrides,
                        )
                        st.session_state.import_confirmed = False
                        st.rerun()
                approvals = {}
                for row in p["rows"]:
                    fields = {d["field"] for d in row["duplicates"] if d["approval_required"]} | {
                        d["field"] for d in row["incoming"]
                    }
                    if fields:
                        with st.expander(f"Погодження повторів рядка {row['row']}"):
                            values = {}
                            for field in fields:
                                permitted = field == "military_number" or actor.role == Role.ADMIN
                                yes = st.checkbox(
                                    "Погоджую " + field,
                                    key=f"import_approve_{key}_{row['row']}_{field}",
                                    disabled=not permitted,
                                )
                                reason = st.text_input(
                                    "Причина " + field,
                                    key=f"import_reason_{key}_{row['row']}_{field}",
                                    disabled=not permitted,
                                )
                                values[field] = {"confirmed": yes, "reason": reason}
                            approvals[str(row["row"])] = values
                confirmed = st.checkbox(
                    "Підтверджую імпорт перевірених рядків; помилкові буде пропущено",
                    key="import_confirmed",
                )
                if st.button("Виконати підтверджений імпорт", type="primary"):
                    result = apply_import(
                        engine, p, actor, confirmed=confirmed, approvals=approvals
                    )
                    st.session_state.import_result = result
                    st.session_state.pop("import_preview", None)
                    st.rerun()
        except ui.ERRORS as exc:
            st.error(ui.friendly_error(exc))
    if result := st.session_state.get("import_result"):
        st.dataframe(pd.DataFrame(result), hide_index=True, width="stretch")
        st.download_button(
            "Звіт останнього імпорту",
            workbook_bytes(result, ["row", "status", "reason", "vehicle_id"]),
            file_name="AUTO-import-report.xlsx",
        )
    with st.expander("Журнал імпортів"):
        for batch in import_log(engine, actor):
            st.caption(
                f"{batch.created_at.isoformat()} UTC · {batch.user_id} · {batch.source_name}"
            )
            st.dataframe(pd.DataFrame(batch.results), hide_index=True, width="stretch")
