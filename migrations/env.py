from alembic import context
from sqlalchemy import create_engine

from app import models  # noqa: F401  imported for its side effect: registers tables
from app.db import DATABASE_URL, Base

target_metadata = Base.metadata

if context.is_offline_mode():
    context.configure(url=DATABASE_URL, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    with create_engine(DATABASE_URL).connect() as connection:
        # render_as_batch lets SQLite emulate ALTER TABLE for future migrations.
        context.configure(connection=connection, target_metadata=target_metadata,
                          render_as_batch=True)
        with context.begin_transaction():
            context.run_migrations()
