"""Endpoint tests for /v1/templates.

The TemplateService is dependency-overridden — no real DB/Redis is touched.
Tests cover: routing, RBAC (non-manager forbidden / manager allowed), request
validation, and response shape.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from uuid import UUID, uuid4

import httpx

from app.core import deps
from app.core.security import Principal
from app.modules.intake_templates.application.dtos import (
    TemplateCreated,
    TemplatePage,
    TemplateWithVersions,
)
from app.modules.intake_templates.domain.entities import (
    MANDATORY_CORE_FIELDS,
    TEMPLATE_SELECTION_RULES,
    Template,
    TemplateSelectionRule,
    TemplateType,
    TemplateVersion,
    TemplateVersionStatus,
)
from app.modules.intake_templates.domain.errors import (
    DuplicateTemplateNameError,
    SystemTemplateProtectedError,
    TemplateNotFoundError,
)
from app.modules.intake_templates.interface import router as templates_router_module

_TID = UUID("11111111-1111-1111-1111-111111111111")
_TMPL_ID = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
_VER_ID = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
_TS = datetime(2026, 7, 2, 12, 0, tzinfo=UTC)
_ACTOR = UUID("cccccccc-cccc-cccc-cccc-cccccccccccc")

_TEMPLATE = Template(
    id=_TMPL_ID,
    type=TemplateType.CHANGE,
    name="Change Request",
    description=None,
    is_system=False,
    created_at=_TS,
    updated_at=_TS,
)

_VERSION = TemplateVersion(
    id=_VER_ID,
    template_id=_TMPL_ID,
    version_number=1,
    status=TemplateVersionStatus.DRAFT,
    fields=MANDATORY_CORE_FIELDS,
    created_by=_ACTOR,
    created_at=_TS,
    updated_at=_TS,
)


def _principal(*roles: str) -> Principal:
    return Principal(
        subject=str(_ACTOR),
        tenant_id=_TID,
        roles=frozenset(roles),
        raw_claims={},
    )


class _FakeService:
    def __init__(
        self,
        raises: Exception | None = None,
    ) -> None:
        self._raises = raises
        self.calls: list[str] = []

    def _maybe_raise(self) -> None:
        if self._raises is not None:
            raise self._raises

    async def create_template(self, *, command: object, actor_id: object) -> TemplateCreated:
        self.calls.append("create_template")
        self._maybe_raise()
        return TemplateCreated(template=_TEMPLATE, version=_VERSION)

    async def get_template(self, *, template_id: UUID) -> TemplateWithVersions:
        self.calls.append("get_template")
        self._maybe_raise()
        return TemplateWithVersions(template=_TEMPLATE, versions=[_VERSION])

    async def list_templates(self, *, query: object) -> TemplatePage:
        self.calls.append("list_templates")
        self._maybe_raise()
        return TemplatePage(items=[_TEMPLATE], total=1, limit=20, offset=0)

    async def update_draft_fields(
        self, *, template_id: UUID, version_id: UUID, command: object
    ) -> TemplateVersion:
        self.calls.append("update_draft_fields")
        self._maybe_raise()
        return _VERSION

    async def publish_version(self, *, template_id: UUID, version_id: UUID) -> TemplateVersion:
        self.calls.append("publish_version")
        self._maybe_raise()
        return TemplateVersion(
            id=_VER_ID,
            template_id=_TMPL_ID,
            version_number=1,
            status=TemplateVersionStatus.PUBLISHED,
            fields=MANDATORY_CORE_FIELDS,
            created_by=_ACTOR,
            created_at=_TS,
            updated_at=_TS,
        )

    async def archive_version(self, *, template_id: UUID, version_id: UUID) -> TemplateVersion:
        self.calls.append("archive_version")
        self._maybe_raise()
        return TemplateVersion(
            id=_VER_ID,
            template_id=_TMPL_ID,
            version_number=1,
            status=TemplateVersionStatus.ARCHIVED,
            fields=MANDATORY_CORE_FIELDS,
            created_by=_ACTOR,
            created_at=_TS,
            updated_at=_TS,
        )

    async def new_draft_from_published(
        self, *, template_id: UUID, actor_id: UUID
    ) -> TemplateVersion:
        self.calls.append("new_draft_from_published")
        self._maybe_raise()
        return TemplateVersion(
            id=uuid4(),
            template_id=_TMPL_ID,
            version_number=2,
            status=TemplateVersionStatus.DRAFT,
            fields=MANDATORY_CORE_FIELDS,
            created_by=actor_id,
            created_at=_TS,
            updated_at=_TS,
        )

    def get_selection_rules(self) -> tuple[TemplateSelectionRule, ...]:
        self.calls.append("get_selection_rules")
        return TEMPLATE_SELECTION_RULES


async def _make_client(
    *roles: str,
    raises: Exception | None = None,
) -> AsyncIterator[tuple[httpx.AsyncClient, _FakeService]]:
    from app.main import create_app

    app = create_app()
    fake = _FakeService(raises=raises)
    app.dependency_overrides[deps._principal] = lambda: _principal(*roles)
    app.dependency_overrides[templates_router_module._service] = lambda: fake
    app.dependency_overrides[deps.check_rate_limit] = lambda: None
    app.dependency_overrides[deps.check_csrf] = lambda: None
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as ac:
        yield ac, fake


# ---------------------------------------------------------------------------
# GET /v1/templates/selection-rules — any authenticated
# ---------------------------------------------------------------------------


async def test_get_selection_rules_authenticated() -> None:
    async for ac, fake in _make_client("tenant_user"):
        r = await ac.get("/v1/templates/selection-rules")
        assert r.status_code == 200, r.text
        body = r.json()
        assert isinstance(body["data"], list)
        assert len(body["data"]) >= 6
        assert fake.calls == ["get_selection_rules"]


# ---------------------------------------------------------------------------
# GET /v1/templates — any authenticated
# ---------------------------------------------------------------------------


async def test_list_templates_authenticated() -> None:
    async for ac, fake in _make_client("tenant_user"):
        r = await ac.get("/v1/templates")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["data"][0]["name"] == "Change Request"
        assert body["meta"]["total"] == 1
        assert fake.calls == ["list_templates"]


async def test_list_templates_pagination_params() -> None:
    async for ac, _ in _make_client("tenant_user"):
        r = await ac.get("/v1/templates", params={"limit": 5, "offset": 10})
        assert r.status_code == 200


# ---------------------------------------------------------------------------
# GET /v1/templates/{template_id} — any authenticated
# ---------------------------------------------------------------------------


async def test_get_template_authenticated() -> None:
    async for ac, fake in _make_client("tenant_user"):
        r = await ac.get(f"/v1/templates/{_TMPL_ID}")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["data"]["id"] == str(_TMPL_ID)
        assert "versions" in body["data"]
        assert fake.calls == ["get_template"]


async def test_get_template_not_found() -> None:
    async for ac, _ in _make_client("tenant_user", raises=TemplateNotFoundError("not found")):
        r = await ac.get(f"/v1/templates/{uuid4()}")
        assert r.status_code == 404
        assert r.json()["error"]["code"] == "TEMPLATE_NOT_FOUND"


# ---------------------------------------------------------------------------
# POST /v1/templates — ba, tenant_admin, or platform_admin
# ---------------------------------------------------------------------------


async def test_create_template_ba_role() -> None:
    async for ac, fake in _make_client("ba"):
        r = await ac.post(
            "/v1/templates",
            json={"type": "change", "name": "Change Request"},
        )
        assert r.status_code == 201, r.text
        assert r.json()["data"]["name"] == "Change Request"
        assert fake.calls == ["create_template"]


async def test_create_template_tenant_admin_role() -> None:
    async for ac, fake in _make_client("tenant_admin"):
        r = await ac.post(
            "/v1/templates",
            json={"type": "feature_request", "name": "Feature"},
        )
        assert r.status_code == 201, r.text
        assert fake.calls == ["create_template"]


async def test_create_template_platform_admin_role() -> None:
    """platform_admin must be allowed — it is in _MANAGE_ROLES."""
    async for ac, fake in _make_client("platform_admin"):
        r = await ac.post(
            "/v1/templates",
            json={"type": "change", "name": "Ops Template"},
        )
        assert r.status_code == 201, r.text
        assert fake.calls == ["create_template"]


async def test_create_template_tenant_user_forbidden() -> None:
    async for ac, fake in _make_client("tenant_user"):
        r = await ac.post(
            "/v1/templates",
            json={"type": "change", "name": "X"},
        )
        assert r.status_code == 403
        assert fake.calls == []


async def test_create_template_duplicate_name_conflict() -> None:
    err = DuplicateTemplateNameError("already exists")
    async for ac, _ in _make_client("ba", raises=err):
        r = await ac.post("/v1/templates", json={"type": "change", "name": "Same"})
        assert r.status_code == 409
        assert r.json()["error"]["code"] == "DUPLICATE_TEMPLATE_NAME"


async def test_create_template_free_form_type_rejected() -> None:
    """free_form is reserved for the built-in system template — service returns 409."""
    err = SystemTemplateProtectedError("reserved")
    async for ac, _ in _make_client("ba", raises=err):
        r = await ac.post("/v1/templates", json={"type": "free_form", "name": "My Free Form"})
        assert r.status_code == 409
        assert r.json()["error"]["code"] == "SYSTEM_TEMPLATE_PROTECTED"


async def test_create_template_invalid_type() -> None:
    async for ac, _ in _make_client("ba"):
        r = await ac.post("/v1/templates", json={"type": "nonexistent_type", "name": "X"})
        assert r.status_code == 422


async def test_create_template_missing_name() -> None:
    async for ac, _ in _make_client("ba"):
        r = await ac.post("/v1/templates", json={"type": "change"})
        assert r.status_code == 422


async def test_create_template_blank_name_rejected() -> None:
    """Whitespace-only name must be rejected at the schema layer (422, not 409)."""
    async for ac, _ in _make_client("ba"):
        r = await ac.post("/v1/templates", json={"type": "change", "name": "  "})
        assert r.status_code == 422


# ---------------------------------------------------------------------------
# PATCH /v1/templates/{id}/versions/{vid}/fields — ba, tenant_admin, platform_admin
# ---------------------------------------------------------------------------


_VALID_FIELD = {
    "key": "custom",
    "label": "Custom field",
    "kind": "text",
    "required": False,
}

_ALL_CORE_FIELDS = [
    {"key": k, "label": k, "kind": "text", "required": True}
    for k in [
        "problem",
        "expected_result",
        "success_metric",
        "as_is",
        "to_be",
        "affected_systems",
        "urgency_deadline",
        "acceptance_criteria",
        "business_goal",
    ]
]


async def test_update_draft_fields_ba() -> None:
    async for ac, fake in _make_client("ba"):
        r = await ac.patch(
            f"/v1/templates/{_TMPL_ID}/versions/{_VER_ID}/fields",
            json={"fields": _ALL_CORE_FIELDS},
        )
        assert r.status_code == 200, r.text
        assert fake.calls == ["update_draft_fields"]


async def test_update_draft_fields_non_manager_forbidden() -> None:
    async for ac, fake in _make_client("tenant_user"):
        r = await ac.patch(
            f"/v1/templates/{_TMPL_ID}/versions/{_VER_ID}/fields",
            json={"fields": _ALL_CORE_FIELDS},
        )
        assert r.status_code == 403
        assert fake.calls == []


# ---------------------------------------------------------------------------
# POST /v1/templates/{id}/versions/{vid}/publish — ba, tenant_admin, platform_admin
# ---------------------------------------------------------------------------


async def test_publish_version_ba() -> None:
    async for ac, fake in _make_client("ba"):
        r = await ac.post(f"/v1/templates/{_TMPL_ID}/versions/{_VER_ID}/publish")
        assert r.status_code == 200, r.text
        assert r.json()["data"]["status"] == "published"
        assert fake.calls == ["publish_version"]


async def test_publish_version_non_manager_forbidden() -> None:
    async for ac, fake in _make_client("tenant_user"):
        r = await ac.post(f"/v1/templates/{_TMPL_ID}/versions/{_VER_ID}/publish")
        assert r.status_code == 403
        assert fake.calls == []


# ---------------------------------------------------------------------------
# POST /v1/templates/{id}/versions/{vid}/archive — ba, tenant_admin, platform_admin
# ---------------------------------------------------------------------------


async def test_archive_version_ba() -> None:
    async for ac, fake in _make_client("ba"):
        r = await ac.post(f"/v1/templates/{_TMPL_ID}/versions/{_VER_ID}/archive")
        assert r.status_code == 200, r.text
        assert r.json()["data"]["status"] == "archived"
        assert fake.calls == ["archive_version"]


async def test_archive_system_template_conflict() -> None:
    err = SystemTemplateProtectedError("protected")
    async for ac, _ in _make_client("ba", raises=err):
        r = await ac.post(f"/v1/templates/{_TMPL_ID}/versions/{_VER_ID}/archive")
        assert r.status_code == 409
        assert r.json()["error"]["code"] == "SYSTEM_TEMPLATE_PROTECTED"


async def test_archive_version_non_manager_forbidden() -> None:
    async for ac, fake in _make_client("tenant_user"):
        r = await ac.post(f"/v1/templates/{_TMPL_ID}/versions/{_VER_ID}/archive")
        assert r.status_code == 403
        assert fake.calls == []


# ---------------------------------------------------------------------------
# POST /v1/templates/{id}/new-draft — ba, tenant_admin, platform_admin
# ---------------------------------------------------------------------------


async def test_new_draft_from_published_ba() -> None:
    async for ac, fake in _make_client("ba"):
        r = await ac.post(f"/v1/templates/{_TMPL_ID}/new-draft")
        assert r.status_code == 201, r.text
        assert r.json()["data"]["version_number"] == 2
        assert fake.calls == ["new_draft_from_published"]


async def test_new_draft_non_manager_forbidden() -> None:
    async for ac, fake in _make_client("tenant_user"):
        r = await ac.post(f"/v1/templates/{_TMPL_ID}/new-draft")
        assert r.status_code == 403
        assert fake.calls == []


# ---------------------------------------------------------------------------
# Response shape: versions list in detail response
# ---------------------------------------------------------------------------


async def test_get_template_versions_shape() -> None:
    async for ac, _ in _make_client("tenant_user"):
        r = await ac.get(f"/v1/templates/{_TMPL_ID}")
        assert r.status_code == 200
        version = r.json()["data"]["versions"][0]
        assert "id" in version
        assert "version_number" in version
        assert "status" in version
        assert "fields" in version
        assert isinstance(version["fields"], list)
        assert len(version["fields"]) == len(MANDATORY_CORE_FIELDS)
