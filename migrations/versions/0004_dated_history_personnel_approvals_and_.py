"""dated history personnel approvals and document guards"""

import sqlalchemy as sa
import sqlmodel
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    seen = set()
    for person in connection.execute(
        sa.text("SELECT id, personal_number FROM personnel")
    ).mappings():
        number = " ".join((person["personal_number"] or "").split()).casefold()
        if number and number in seen:
            raise RuntimeError("Duplicate personnel numbers: resolve on a copy before migration")
        if number:
            seen.add(number)

    op.create_table(
        "duplicate_approvals",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("vehicle_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("field", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("normalized_value", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("matched_vehicle_ids", sa.JSON(), nullable=False),
        sa.Column("reason", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("approved_by", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("approved_role", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("approved_at", sqlmodel.sql.sqltypes.UTCDateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["vehicle_id"],
            ["vehicles.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("duplicate_approvals", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_duplicate_approvals_normalized_value"),
            ["normalized_value"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_duplicate_approvals_vehicle_id"), ["vehicle_id"], unique=False
        )

    op.create_table(
        "vehicle_document_locks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("vehicle_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("document_reference", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("document_date", sa.Date(), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("registered_at", sqlmodel.sql.sqltypes.UTCDateTime(), nullable=False),
        sa.Column("registered_by", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.ForeignKeyConstraint(
            ["vehicle_id"],
            ["vehicles.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("document_reference"),
    )
    with op.batch_alter_table("vehicle_document_locks", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_vehicle_document_locks_document_date"), ["document_date"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_vehicle_document_locks_vehicle_id"), ["vehicle_id"], unique=False
        )

    op.create_table(
        "vehicle_history",
        sa.Column("id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("vehicle_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("kind", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("valid_from", sa.Date(), nullable=True),
        sa.Column("valid_to", sa.Date(), nullable=True),
        sa.Column("value", sa.JSON(), nullable=False),
        sa.Column("unit_id", sa.Integer(), nullable=True),
        sa.Column("support_service_id", sa.Integer(), nullable=True),
        sa.Column("person_id", sa.Integer(), nullable=True),
        sa.Column("recorded_at", sqlmodel.sql.sqltypes.UTCDateTime(), nullable=False),
        sa.Column("recorded_by", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("updated_at", sqlmodel.sql.sqltypes.UTCDateTime(), nullable=False),
        sa.Column("updated_by", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("is_initial", sa.Boolean(), nullable=False),
        sa.CheckConstraint(
            "kind IN ('unit','service','mvo','status','military_number',"
            "'vin','chassis_number','serial_number')",
            name="ck_history_kind",
        ),
        sa.CheckConstraint(
            "valid_to IS NULL OR valid_from IS NULL OR valid_to > valid_from",
            name="ck_history_interval",
        ),
        sa.ForeignKeyConstraint(
            ["person_id"],
            ["personnel.id"],
        ),
        sa.ForeignKeyConstraint(
            ["support_service_id"],
            ["support_services.id"],
        ),
        sa.ForeignKeyConstraint(
            ["unit_id"],
            ["units.id"],
        ),
        sa.ForeignKeyConstraint(
            ["vehicle_id"],
            ["vehicles.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("vehicle_id", "kind", "valid_from"),
    )
    with op.batch_alter_table("vehicle_history", schema=None) as batch_op:
        batch_op.create_index(
            "ix_history_unknown_start",
            ["vehicle_id", "kind"],
            unique=True,
            sqlite_where=sa.text("valid_from IS NULL"),
        )
        batch_op.create_index(batch_op.f("ix_vehicle_history_kind"), ["kind"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_vehicle_history_valid_from"), ["valid_from"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_vehicle_history_valid_to"), ["valid_to"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_vehicle_history_vehicle_id"), ["vehicle_id"], unique=False
        )

    # Add nullable references in-place; no SQLite table recreation or FK disabling.
    op.execute("ALTER TABLE vehicles ADD COLUMN mvo_id INTEGER REFERENCES personnel(id)")
    op.create_index("ix_vehicles_mvo_id", "vehicles", ["mvo_id"])
    op.add_column("personnel", sa.Column("personal_number_normalized", sa.String(), nullable=True))
    op.add_column(
        "personnel", sa.Column("version", sa.Integer(), nullable=False, server_default="1")
    )
    op.create_index(
        "ix_personnel_personal_number_normalized",
        "personnel",
        ["personal_number_normalized"],
        unique=True,
    )
    connection = op.get_bind()
    for person in connection.execute(
        sa.text("SELECT id, personal_number FROM personnel")
    ).mappings():
        number = " ".join((person["personal_number"] or "").split()).casefold() or None
        connection.execute(
            sa.text("UPDATE personnel SET personal_number_normalized=:number WHERE id=:id"),
            {"number": number, "id": person["id"]},
        )
    history = sa.table(
        "vehicle_history",
        sa.column("id", sa.String),
        sa.column("vehicle_id", sa.String),
        sa.column("kind", sa.String),
        sa.column("valid_from", sa.Date),
        sa.column("valid_to", sa.Date),
        sa.column("value", sa.JSON),
        sa.column("unit_id", sa.Integer),
        sa.column("support_service_id", sa.Integer),
        sa.column("person_id", sa.Integer),
        sa.column("recorded_at", sa.DateTime),
        sa.column("recorded_by", sa.String),
        sa.column("updated_at", sa.DateTime),
        sa.column("updated_by", sa.String),
        sa.column("is_initial", sa.Boolean),
    )
    fields = {
        "unit": ("unit_id",),
        "service": ("support_service_id",),
        "mvo": ("mvo_id",),
        "status": ("accounting_status", "technical_condition", "technical_category", "is_operated"),
        "military_number": ("military_number",),
        "vin": ("vin", "vin_explanation"),
        "chassis_number": ("chassis_number",),
        "serial_number": ("serial_number",),
    }
    from datetime import datetime, timezone
    from uuid import uuid4

    now = datetime.now(timezone.utc)
    for vehicle in connection.execute(sa.text("SELECT * FROM vehicles")).mappings():
        op.bulk_insert(
            history,
            [
                dict(
                    id=str(uuid4()),
                    vehicle_id=vehicle["id"],
                    kind=kind,
                    valid_from=None,
                    valid_to=None,
                    value={f: vehicle[f] for f in names},
                    unit_id=vehicle["unit_id"] if kind == "unit" else None,
                    support_service_id=vehicle["support_service_id"] if kind == "service" else None,
                    person_id=None,
                    recorded_at=now,
                    updated_at=now,
                    recorded_by="migration:0004",
                    updated_by="migration:0004",
                    is_initial=True,
                )
                for kind, names in fields.items()
            ],
        )
    for table in ("vehicle_document_locks", "duplicate_approvals"):
        for action in ("UPDATE", "DELETE"):
            op.execute(
                f"CREATE TRIGGER {table}_no_{action.lower()} BEFORE {action} ON {table} "
                f"BEGIN SELECT RAISE(ABORT, '{table} is immutable'); END"
            )

    # ### end Alembic commands ###


def downgrade():
    raise RuntimeError("Destructive downgrades are disabled; restore a verified backup instead")
