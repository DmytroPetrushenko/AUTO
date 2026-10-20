from alembic import context

from auto.models import SQLModel

config = context.config
target_metadata = SQLModel.metadata


def run_migrations():
    connection = config.attributes.get("connection")
    if connection is None:
        from sqlalchemy import engine_from_config

        engine = engine_from_config(config.get_section(config.config_ini_section))
        with engine.connect() as connection:
            connection.exec_driver_sql("PRAGMA foreign_keys=ON")
            connection.commit()
            configure(connection)
        engine.dispose()
    else:
        configure(connection)


def configure(connection):
    context.configure(connection=connection, target_metadata=target_metadata, render_as_batch=True)
    with context.begin_transaction():
        context.run_migrations()


run_migrations()
