import streamlit as st

from auto.registry import PRESET_KEYS, delete_preset, list_presets, save_preset


def save_current(engine, actor):
    from auto import ui

    try:
        save_preset(
            engine,
            st.session_state.preset_name,
            {k: st.session_state[k] for k in PRESET_KEYS if k in st.session_state},
            actor,
        )
        st.session_state.notice = ("success", "Набір фільтрів збережено")
    except ui.ERRORS as exc:
        st.session_state.notice = ("error", ui.friendly_error(exc))


def apply_current(engine, actor):
    rows = list_presets(engine, actor)
    item = next((r for r in rows if r.id == st.session_state.preset_selected), None)
    if item:
        for key, value in item.settings.items():
            st.session_state[key] = value


def remove_current(engine, actor):
    from auto import ui

    try:
        delete_preset(engine, st.session_state.preset_selected, actor)
    except ui.ERRORS as exc:
        st.session_state.notice = ("error", ui.friendly_error(exc))


def controls(engine, actor):
    with st.expander("Мої набори фільтрів"):
        rows = list_presets(engine, actor)
        names = {r.id: r.name for r in rows}
        st.selectbox(
            "Збережений набір",
            list(names),
            index=None,
            format_func=names.get,
            key="preset_selected",
        )
        st.button(
            "Застосувати набір", on_click=apply_current, args=(engine, actor), disabled=not rows
        )
        st.text_input("Назва набору", key="preset_name")
        st.button("Зберегти поточний набір", on_click=save_current, args=(engine, actor))
        st.button(
            "Видалити мій набір", on_click=remove_current, args=(engine, actor), disabled=not rows
        )
