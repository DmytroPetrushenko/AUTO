import pytest

from auto.db import make_engine, migrate
from auto.services import Actor, Role, list_lookup, save_lookup


@pytest.fixture
def actor():
    return Actor("test-operator", Role.OPERATOR)


@pytest.fixture
def engine(tmp_path):
    path = tmp_path / "test.sqlite3"
    migrate(path)
    engine = make_engine(path)
    yield engine
    engine.dispose()


@pytest.fixture
def minimum(engine, actor):
    brand = save_lookup(engine, "vehicle_brands", "Тестова марка", actor)
    model = save_lookup(engine, "vehicle_models", "Тестова модель", actor, brand_id=brand.id)
    return dict(
        brand_id=brand.id,
        model_id=model.id,
        vehicle_type_id=list_lookup(engine, "vehicle_types")[0].id,
        support_service_id=list_lookup(engine, "support_services")[0].id,
        work_unit="км",
    )
