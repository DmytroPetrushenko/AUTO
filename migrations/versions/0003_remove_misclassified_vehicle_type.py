"""Remove the erroneous model entry from vehicle types."""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    # Keep existing references intact on databases where this value was used.
    # Unused entries disappear from all lists, including inactive classifiers.
    parameters = {"name": "motoleader ml900atv"}
    connection.execute(
        sa.text(
            "DELETE FROM vehicle_types WHERE normalized_name = :name "
            "AND NOT EXISTS (SELECT 1 FROM vehicles WHERE vehicle_type_id = vehicle_types.id)"
        ),
        parameters,
    )
    connection.execute(
        sa.text("UPDATE vehicle_types SET is_active = 0 WHERE normalized_name = :name"), parameters
    )


def downgrade():
    raise RuntimeError("Destructive downgrades are disabled; restore a verified backup instead")
