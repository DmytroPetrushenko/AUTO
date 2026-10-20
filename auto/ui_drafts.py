"""Session drafts for independent card operations; no writes before explicit save."""

from copy import deepcopy

import streamlit as st


def _upgrade(name, record):
    # Keep active drafts if Streamlit reloads an older editor implementation.
    if "generation" not in record:
        for field in record["draft"]:
            record["draft"][field] = st.session_state.get(
                f"aux_field_{name}_{field}", record["draft"][field]
            )
        record["generation"] = st.session_state.setdefault("aux_generations", {}).get(name, 0)
    return record


def clear():
    if st.session_state.pop("bundle_review", None):
        st.session_state.card_preview = None
    generations = st.session_state.setdefault("aux_generations", {})
    for name in st.session_state.get("aux_editors", {}):
        generations[name] = generations.get(name, 0) + 1
    for key in list(st.session_state):
        if key.startswith("aux_field_") or key.startswith("norm_review_"):
            del st.session_state[key]
    st.session_state.aux_editors = {}


def collect(vehicle):
    """Capture changed editors, including the latest callback values."""
    children = []
    for name, record in st.session_state.get("aux_editors", {}).items():
        _upgrade(name, record)
        values = {
            f: st.session_state.get(
                f"aux_field_{name}_{record['generation']}_{f}", record["draft"][f]
            )
            for f in record["initial"]
        }
        if values == record["initial"]:
            continue
        parts = name.split("_", 2)
        if len(parts) < 2 or parts[1] != vehicle.id:
            raise ValueError("Чернетка не належить цій картці")
        kind = parts[0]
        item = {
            "kind": kind,
            "id": parts[2] if len(parts) == 3 and parts[2] != "None" else None,
            "version": record["version"],
            "values": values,
        }
        if kind == "document":
            item["files"] = [(f.name, f.getvalue()) for f in values.pop("files")]
        elif kind == "reading":
            values["measured_on"] = values.pop("day")
            values["unit"] = vehicle.work_unit
        children.append(item)
    return children


def dirty():
    if any(k.startswith("norm_review_") for k in st.session_state):
        return True
    for name, record in st.session_state.get("aux_editors", {}).items():
        _upgrade(name, record)
        for field, initial in record["initial"].items():
            key = f"aux_field_{name}_{record['generation']}_{field}"
            value = st.session_state.get(key, record["draft"][field])
            if value != initial:
                return True
    return False


class Editor:
    def __init__(self, group, identity, version, initial):
        self.name = f"{group}_{identity}"
        records = st.session_state.setdefault("aux_editors", {})
        self.record = records.setdefault(
            self.name,
            {
                "version": version,
                "generation": st.session_state.setdefault("aux_generations", {}).get(self.name, 0),
                "initial": deepcopy(initial),
                "draft": deepcopy(initial),
            },
        )
        _upgrade(self.name, self.record)
        self.version = self.record["version"]

    def widget(self, method, field, label, **kwargs):
        key = f"aux_field_{self.name}_{self.record['generation']}_{field}"
        value = self.record["draft"][field]
        if method == "selectbox":
            kwargs["index"] = kwargs["options"].index(value)
        elif method != "file_uploader":
            kwargs["value"] = value
        result = getattr(st, method)(label, key=key, **kwargs)
        self.record["draft"][field] = result
        return result

    def reset(self):
        st.session_state.aux_generations[self.name] = self.record["generation"] + 1
        st.session_state.aux_editors.pop(self.name, None)
        prefix = f"aux_field_{self.name}_"
        for key in list(st.session_state):
            if key.startswith(prefix):
                del st.session_state[key]
