"""per-source visit clock

Revision ID: c4a1d9e02b17
Revises: 0f3de7537fad
Create Date: 2026-09-05 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "c4a1d9e02b17"
down_revision: Union[str, Sequence[str], None] = "0f3de7537fad"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add the per-source visit clock and seed it from the existing one.

    An existing user's single ``last_open_at`` is carried onto the source they
    are currently on. Copying it onto both would invent a visit to a source
    they have never opened, and the brief would then describe a window the
    person was never away for.
    """
    op.create_table(
        "user_source_visit",
        sa.Column("user_id", sa.String(length=32), nullable=False),
        sa.Column("source", sa.String(length=8), nullable=False),
        sa.Column("last_open_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", "source"),
    )
    op.execute(
        "INSERT INTO user_source_visit (user_id, source, last_open_at) "
        "SELECT id, data_source, last_open_at FROM users WHERE last_open_at IS NOT NULL"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("user_source_visit")
