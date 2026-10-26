"""Напоминание о встрече по умолчанию — за час, а не за 15 минут.

Меняется умолчание users.remind_before_min и значение у тех, у кого стоит прежнее умолчание 15:
своё число, выбранное в настройках, не трогаем. Уже запланированные напоминания встреч не
пересчитываются — новое значение действует для встреч, созданных или перенесённых после.

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-26
"""

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("users", "remind_before_min", server_default="60")
    op.execute("UPDATE users SET remind_before_min = 60 WHERE remind_before_min = 15")


def downgrade() -> None:
    op.execute("UPDATE users SET remind_before_min = 15 WHERE remind_before_min = 60")
    op.alter_column("users", "remind_before_min", server_default="15")
