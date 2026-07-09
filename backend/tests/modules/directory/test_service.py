"""Unit tests for DirectoryService and KeycloakDirectoryReader.

No live Keycloak required — both the DirectoryReader port and the Keycloak
admin client are replaced with fakes.

Coverage:
1. DirectoryService.list_users delegates to the reader with (tenant_id, search,
   limit) and returns whatever the reader returns unchanged.
2. KeycloakDirectoryReader.list_tenant_users:
   - maps id/username/firstName/lastName/email → subject/username/first_name/
     last_name/email fields of DirectoryUser
   - full_name = "First Last" when both names present
   - full_name = username when both first_name and last_name are missing/empty
   - service-account users (username prefix "service-account-") are excluded
   - calls search_users with q="tenant_id:<tenant_id>" and the given search/limit
"""

from __future__ import annotations

from typing import Any

from app.modules.directory.application.dtos import DirectoryUser, ListDirectoryUsersQuery
from app.modules.directory.application.services import DirectoryService
from app.modules.directory.infrastructure.adapters import KeycloakDirectoryReader

_TENANT_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeDirectoryReader:
    """Records the arguments it receives and returns a preset list."""

    def __init__(self, users: list[DirectoryUser]) -> None:
        self._users = users
        self.calls: list[dict[str, Any]] = []

    async def list_tenant_users(
        self,
        *,
        tenant_id: str,
        search: str | None,
        limit: int,
    ) -> list[DirectoryUser]:
        self.calls.append({"tenant_id": tenant_id, "search": search, "limit": limit})
        return list(self._users)


class FakeAdminClient:
    """Minimal fake for KeycloakAdminClient: records search_users kwargs."""

    def __init__(self, raw_users: list[dict[str, Any]]) -> None:
        self._raw_users = raw_users
        self.calls: list[dict[str, Any]] = []

    async def search_users(self, **kwargs: Any) -> list[dict[str, Any]]:
        self.calls.append(dict(kwargs))
        return list(self._raw_users)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ALICE = DirectoryUser(
    subject="uuid-alice",
    username="alice",
    first_name="Alice",
    last_name="Smith",
    full_name="Alice Smith",
    email="alice@example.com",
)
_BOB = DirectoryUser(
    subject="uuid-bob",
    username="bob",
    first_name=None,
    last_name=None,
    full_name="bob",
    email=None,
)


def _make_service(users: list[DirectoryUser]) -> tuple[DirectoryService, FakeDirectoryReader]:
    reader = FakeDirectoryReader(users)
    service = DirectoryService(reader=reader)  # type: ignore[arg-type]
    return service, reader


# ---------------------------------------------------------------------------
# 1. DirectoryService delegation tests
# ---------------------------------------------------------------------------


async def test_list_users_delegates_and_returns_reader_result() -> None:
    service, reader = _make_service([_ALICE, _BOB])
    query = ListDirectoryUsersQuery(search="ali", limit=10)

    result = await service.list_users(tenant_id=_TENANT_ID, query=query)

    assert result == [_ALICE, _BOB]
    assert len(reader.calls) == 1
    call = reader.calls[0]
    assert call["tenant_id"] == _TENANT_ID
    assert call["search"] == "ali"
    assert call["limit"] == 10


async def test_list_users_passes_none_search() -> None:
    service, reader = _make_service([])
    query = ListDirectoryUsersQuery(search=None, limit=25)

    result = await service.list_users(tenant_id=_TENANT_ID, query=query)

    assert result == []
    assert reader.calls[0]["search"] is None
    assert reader.calls[0]["limit"] == 25


async def test_list_users_empty_result_is_propagated() -> None:
    service, _reader = _make_service([])
    query = ListDirectoryUsersQuery(search=None, limit=50)

    result = await service.list_users(tenant_id=_TENANT_ID, query=query)

    assert result == []


# ---------------------------------------------------------------------------
# 2. KeycloakDirectoryReader field-mapping tests
# ---------------------------------------------------------------------------


async def test_keycloak_reader_maps_all_fields() -> None:
    raw = [
        {
            "id": "kc-uuid-1",
            "username": "alice",
            "firstName": "Alice",
            "lastName": "Smith",
            "email": "alice@example.com",
        }
    ]
    client = FakeAdminClient(raw)
    reader = KeycloakDirectoryReader(client=client)  # type: ignore[arg-type]

    users = await reader.list_tenant_users(tenant_id=_TENANT_ID, search=None, limit=20)

    assert len(users) == 1
    u = users[0]
    assert u.subject == "kc-uuid-1"
    assert u.username == "alice"
    assert u.first_name == "Alice"
    assert u.last_name == "Smith"
    assert u.email == "alice@example.com"


async def test_keycloak_reader_full_name_first_and_last() -> None:
    raw = [
        {
            "id": "kc-uuid-2",
            "username": "jdoe",
            "firstName": "John",
            "lastName": "Doe",
            "email": "jdoe@example.com",
        }
    ]
    client = FakeAdminClient(raw)
    reader = KeycloakDirectoryReader(client=client)  # type: ignore[arg-type]

    users = await reader.list_tenant_users(tenant_id=_TENANT_ID, search=None, limit=20)

    assert users[0].full_name == "John Doe"


async def test_keycloak_reader_full_name_fallback_to_username_when_both_names_missing() -> None:
    raw = [
        {
            "id": "kc-uuid-3",
            "username": "ghost",
        }
    ]
    client = FakeAdminClient(raw)
    reader = KeycloakDirectoryReader(client=client)  # type: ignore[arg-type]

    users = await reader.list_tenant_users(tenant_id=_TENANT_ID, search=None, limit=20)

    assert users[0].full_name == "ghost"
    assert users[0].first_name is None
    assert users[0].last_name is None


async def test_keycloak_reader_full_name_fallback_to_username_when_names_empty_strings() -> None:
    raw = [
        {
            "id": "kc-uuid-4",
            "username": "emptynames",
            "firstName": "",
            "lastName": "",
            "email": None,
        }
    ]
    client = FakeAdminClient(raw)
    reader = KeycloakDirectoryReader(client=client)  # type: ignore[arg-type]

    users = await reader.list_tenant_users(tenant_id=_TENANT_ID, search=None, limit=20)

    assert users[0].full_name == "emptynames"


async def test_keycloak_reader_excludes_service_accounts() -> None:
    raw = [
        {
            "id": "kc-uuid-5",
            "username": "service-account-saas-backend",
            "firstName": "Service",
            "lastName": "Account",
            "email": "sa@example.com",
        },
        {
            "id": "kc-uuid-6",
            "username": "real-user",
            "firstName": "Real",
            "lastName": "User",
            "email": "real@example.com",
        },
    ]
    client = FakeAdminClient(raw)
    reader = KeycloakDirectoryReader(client=client)  # type: ignore[arg-type]

    users = await reader.list_tenant_users(tenant_id=_TENANT_ID, search=None, limit=20)

    assert len(users) == 1
    assert users[0].username == "real-user"


async def test_keycloak_reader_all_service_accounts_excluded_yields_empty() -> None:
    raw = [
        {"id": "kc-uuid-7", "username": "service-account-admin"},
        {"id": "kc-uuid-8", "username": "service-account-internal"},
    ]
    client = FakeAdminClient(raw)
    reader = KeycloakDirectoryReader(client=client)  # type: ignore[arg-type]

    users = await reader.list_tenant_users(tenant_id=_TENANT_ID, search=None, limit=20)

    assert users == []


async def test_keycloak_reader_calls_search_users_with_tenant_q_param() -> None:
    client = FakeAdminClient([])
    reader = KeycloakDirectoryReader(client=client)  # type: ignore[arg-type]

    await reader.list_tenant_users(tenant_id=_TENANT_ID, search=None, limit=15)

    assert len(client.calls) == 1
    assert client.calls[0]["q"] == f"tenant_id:{_TENANT_ID}"
    assert client.calls[0]["max_results"] == 15


async def test_keycloak_reader_passes_search_string_to_client() -> None:
    client = FakeAdminClient([])
    reader = KeycloakDirectoryReader(client=client)  # type: ignore[arg-type]

    await reader.list_tenant_users(tenant_id=_TENANT_ID, search="ali", limit=5)

    assert client.calls[0]["search"] == "ali"
    assert client.calls[0]["q"] == f"tenant_id:{_TENANT_ID}"
    assert client.calls[0]["max_results"] == 5


async def test_keycloak_reader_omits_search_param_when_none() -> None:
    """When search is None the adapter passes None (Keycloak client handles omission)."""
    client = FakeAdminClient([])
    reader = KeycloakDirectoryReader(client=client)  # type: ignore[arg-type]

    await reader.list_tenant_users(tenant_id=_TENANT_ID, search=None, limit=10)

    # The adapter calls search_users with search=None (falsy); KeycloakAdminClient
    # itself omits the param when None — but the adapter call itself uses `or None`
    # so we verify it passes None explicitly.
    assert client.calls[0].get("search") is None
