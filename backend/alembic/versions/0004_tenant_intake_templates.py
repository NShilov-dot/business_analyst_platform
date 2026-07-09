"""tenant: intake_templates and template_versions tables + free_form seed

Revision ID: 0004_tenant_intake_templates
Revises: 0003_tenant_departments
Create Date: 2026-07-02

Runs inside the current tenant schema (search_path is set by env.py).
No schema= here on purpose — keeps the migration replayable for every new
tenant.

DATA SEED
---------
Inserts the built-in free_form system template (fixed UUID
10000000-0000-0000-0000-000000000001) with a published version 1 (fixed UUID
20000000-0000-0000-0000-000000000001) containing exactly the nine mandatory-
core fields (§5 + §6.3 of AI_Business_Analyst_System.md).

The fixed UUIDs guarantee every tenant schema gets the same seed IDs after
provisioning — the tickets module can reference them by constant without a DB
lookup.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision: str = "0004_tenant_intake_templates"
down_revision: str | Sequence[str] | None = "0003_tenant_departments"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# ---------------------------------------------------------------------------
# Seed constants — must match domain/entities.py FREE_FORM_TEMPLATE_ID /
# FREE_FORM_VERSION_ID and MANDATORY_CORE_FIELDS.
# ---------------------------------------------------------------------------

_FREE_FORM_TEMPLATE_ID = "10000000-0000-0000-0000-000000000001"
_FREE_FORM_VERSION_ID = "20000000-0000-0000-0000-000000000001"

# Sentinel actor UUID for system-seeded rows (no real user).
_SYSTEM_ACTOR_ID = "00000000-0000-0000-0000-000000000000"

_MANDATORY_CORE_FIELDS = json.dumps(
    [
        {
            "key": "problem",
            "label": "Проблема / потребность",
            "kind": "textarea",
            "required": True,
            "config": {},
        },
        {
            "key": "expected_result",
            "label": "Ожидаемый результат",
            "kind": "textarea",
            "required": True,
            "config": {},
        },
        {
            "key": "success_metric",
            "label": "Метрика успеха (числовая)",
            "kind": "text",
            "required": True,
            "config": {},
        },
        {
            "key": "as_is",
            "label": "Текущее состояние (AS-IS)",
            "kind": "textarea",
            "required": True,
            "config": {},
        },
        {
            "key": "to_be",
            "label": "Целевое состояние (TO-BE)",
            "kind": "textarea",
            "required": True,
            "config": {},
        },
        {
            "key": "affected_systems",
            "label": "Затронутые системы и стейкхолдеры",
            "kind": "textarea",
            "required": True,
            "config": {},
        },
        {
            "key": "urgency_deadline",
            "label": "Срочность и дедлайн",
            "kind": "text",
            "required": True,
            "config": {},
        },
        {
            "key": "acceptance_criteria",
            "label": "Критерии приёмки (AC)",
            "kind": "textarea",
            "required": True,
            "config": {},
        },
        {
            "key": "business_goal",
            "label": "Цель бизнеса (BG)",
            "kind": "text",
            "required": True,
            "config": {},
        },
    ]
)

_NOW = "now()"


def upgrade() -> None:
    op.create_table(
        "templates",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("type", sa.String(length=30), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False, unique=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "is_system",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "type IN ('feature_request', 'change', 'defect', 'integration_data',"
            " 'analytics_request', 'free_form')",
            name="templates_type_chk",
        ),
        sa.CheckConstraint(
            "char_length(name) >= 1",
            name="templates_name_nonempty_chk",
        ),
    )
    op.create_index("ix_templates_type", "templates", ["type"])

    op.create_table(
        "template_versions",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "template_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column(
            "fields",
            sa.dialects.postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "created_by",
            sa.dialects.postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'published', 'archived')",
            name="template_versions_status_chk",
        ),
        sa.ForeignKeyConstraint(
            ["template_id"],
            ["templates.id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "template_id",
            "version_number",
            name="template_versions_tmpl_ver_uq",
        ),
    )
    op.create_index(
        "ix_template_versions_template_id", "template_versions", ["template_id"]
    )
    op.create_index(
        "ix_template_versions_status", "template_versions", ["status"]
    )

    # ------------------------------------------------------------------
    # Seed: built-in free_form system template with published version 1.
    # Every tenant schema gets these deterministic rows after provisioning.
    # ------------------------------------------------------------------
    op.execute(
        sa.text(
            "INSERT INTO templates (id, type, name, description, is_system, created_at, updated_at)"
            " VALUES (:id ::uuid, 'free_form', 'Свободная форма', "
            "  'Нетиповой запрос — используйте эту форму, когда ни один из шаблонов"
            " не подходит. Содержит обязательное ядро из §5 брифа.', "
            "  true, now(), now())"
        ).bindparams(id=_FREE_FORM_TEMPLATE_ID)
    )
    op.execute(
        sa.text(
            "INSERT INTO template_versions"
            "  (id, template_id, version_number, status, fields, created_by,"
            "   created_at, updated_at)"
            " VALUES (:id ::uuid, :template_id ::uuid, 1, 'published', :fields ::jsonb,"
            "         :created_by ::uuid, now(), now())"
        ).bindparams(
            id=_FREE_FORM_VERSION_ID,
            template_id=_FREE_FORM_TEMPLATE_ID,
            fields=_MANDATORY_CORE_FIELDS,
            created_by=_SYSTEM_ACTOR_ID,
        )
    )


def downgrade() -> None:
    op.drop_index("ix_template_versions_status", table_name="template_versions")
    op.drop_index("ix_template_versions_template_id", table_name="template_versions")
    op.drop_table("template_versions")
    op.drop_index("ix_templates_type", table_name="templates")
    op.drop_table("templates")
