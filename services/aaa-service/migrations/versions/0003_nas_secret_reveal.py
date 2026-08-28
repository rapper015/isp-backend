"""Add one-time NAS secret reveal records."""
from alembic import op
revision = "0003_nas_secret_reveal"
down_revision = "0002_nas_orchestration"
branch_labels = None
depends_on = None
def upgrade() -> None:
    from app.database import Base
    import app.models  # noqa: F401
    Base.metadata.create_all(bind=op.get_bind())
def downgrade() -> None:
    from app.database import Base
    Base.metadata.tables["nas_secret_reveals"].drop(bind=op.get_bind(), checkfirst=True)
