"""Reproduce Streamlit's module eviction in an isolated interpreter."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = r"""
import importlib
import json
import os
import sys
from pathlib import Path

from sqlmodel import Session, SQLModel, select
from streamlit.testing.v1 import AppTest

import auto.models as models
from auto.services import Actor, Role, save_vehicle

mode, database, payload = sys.argv[1:]
os.environ['AUTO_DB_PATH'] = database
os.environ['AUTO_ROLE'] = 'operator'
data = json.loads(payload)
from auto.db import make_engine
engine = make_engine(Path(database))
vehicle = save_vehicle(engine, data, Actor('reload-test', Role.OPERATOR))
old_model = models.Vehicle
old_metadata = old_model.metadata
old_tables = set(old_metadata.tables)

if mode == 'models':
    for _ in range(3):
        models = importlib.reload(models)
        assert set(models.Vehicle.metadata.tables) == old_tables
        assert models.Vehicle.metadata is not old_metadata
        with Session(engine) as session:
            assert session.get(old_model, vehicle.id).version == 1
            assert session.get(models.Vehicle, vehicle.id).version == 1
    engine.dispose()
else:
    app = AppTest.from_file(str(Path.cwd() / 'app.py'), default_timeout=15).run()
    assert not app.exception, str(app.exception)
    for index in range(3):
        # Equivalent to Streamlit LocalSourcesWatcher's pending module eviction.
        for name in list(sys.modules):
            if name == 'auto' or name.startswith('auto.'):
                sys.modules.pop(name, None)
        app.run()
        assert not app.exception, str(app.exception)
        services = importlib.import_module('auto.services')
        current = services.get_vehicle(engine, vehicle.id)
        assert current.version == index + 1
        services.save_vehicle(engine, data | {'color': f'Round {index}'},
                              services.Actor('reload-test', services.Role.OPERATOR),
                              vehicle_id=vehicle.id, expected_version=index + 1)
    engine.dispose()
"""


@pytest.mark.parametrize("mode", ["models", "streamlit"])
def test_model_reload_keeps_registry_schema_and_data(engine, minimum, mode):
    result = subprocess.run(
        [
            sys.executable,
            "-X",
            "utf8",
            "-c",
            SCRIPT,
            mode,
            engine.url.database,
            json.dumps(minimum),
        ],
        cwd=Path(__file__).resolve().parents[1],
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=90,
    )
    assert result.returncode == 0, result.stdout + result.stderr
