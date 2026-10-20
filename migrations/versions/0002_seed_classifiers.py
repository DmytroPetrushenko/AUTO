"""Seed only agreed classifiers and protect append-only audit."""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    services = sa.table(
        "support_services",
        sa.column("name", sa.String),
        sa.column("normalized_name", sa.String),
        sa.column("is_active", sa.Boolean),
    )
    op.bulk_insert(
        services,
        [
            {"name": "СВТ", "normalized_name": "свт", "is_active": True},
            {"name": "ЕТС", "normalized_name": "етс", "is_active": True},
        ],
    )
    types = sa.table(
        "vehicle_types",
        sa.column("name", sa.String),
        sa.column("normalized_name", sa.String),
        sa.column("is_active", sa.Boolean),
        sa.column("suggested_work_unit", sa.String),
    )
    names = (
        "спеціальний",
        "вантажний автофургон",
        "причіп",
        "автофургон броньований",
        "причіпне шасі",
        "легковий",
        "квадроцикл",
        "автофургон",
        "вантажний",
        "пасажирський",
        "Motoleader ML900ATV",
        "легковий (PickUp)",
        "бортовий",
    )
    op.bulk_insert(
        types,
        [
            {
                "name": name,
                "normalized_name": name.casefold(),
                "is_active": True,
                "suggested_work_unit": "без лічильника"
                if name in ("причіп", "причіпне шасі")
                else "км",
            }
            for name in names
        ],
    )
    for action in ("UPDATE", "DELETE"):
        op.execute(f"""
            CREATE TRIGGER audit_no_{action.lower()}
            BEFORE {action} ON audit_events
            BEGIN SELECT RAISE(ABORT, 'audit_events is append-only'); END
        """)


def downgrade():
    raise RuntimeError("Destructive downgrades are disabled; restore a verified backup instead")
