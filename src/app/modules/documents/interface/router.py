"""FastAPI router for /v1/documents — the tenant's document library.

Any authenticated tenant member may upload and browse their own documents;
ba/tenant_admin/platform_admin see the whole tenant library for triage
support (DocumentService owns exactly who sees/deletes what).

Body-size limiting: this route is exempted from the global
LimitBodySizeMiddleware cap (see core/security_headers.py + main.py) and
enforces its own MAX_FILE_SIZE_BYTES while streaming the multipart upload —
one source of truth per concern, no unbounded-body hole.
"""

from __future__ import annotations

import asyncio
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Query, Response, UploadFile, status

from app.config import get_settings
from app.core.deps import (
    ObjectStoreOptionalDep,
    PrincipalDep,
    SessionDep,
    TenantDep,
    check_csrf,
    check_rate_limit,
)
from app.core.security import AuthError, Principal
from app.core.tenancy import bucket_for
from app.modules.documents.application.services import DocumentService
from app.modules.documents.domain.entities import MAX_FILE_SIZE_BYTES
from app.modules.documents.domain.errors import DocumentTooLargeError
from app.modules.documents.infrastructure.repositories import SqlAlchemyDocumentRepository
from app.modules.documents.infrastructure.text_extraction import extract_text
from app.modules.documents.interface.schemas import DocumentResponse, Envelope, PagedEnvelope

router = APIRouter(
    prefix="/documents",
    tags=["documents"],
    dependencies=[Depends(check_rate_limit), Depends(check_csrf)],
)

_UPLOAD_CHUNK_SIZE = 1024 * 1024  # 1 MiB — read granularity for the streaming size cap


def _actor_id(principal: Principal) -> UUID:
    try:
        return UUID(principal.subject)
    except ValueError as exc:
        raise AuthError("Token 'sub' is not a UUID") from exc


async def _extract_text_async(content_type: str, filename: str, data: bytes) -> str:
    # pypdf / python-docx are sync + CPU-bound — keep them off the event loop.
    return await asyncio.to_thread(extract_text, content_type, filename, data)


async def _read_capped(file: UploadFile, max_bytes: int) -> bytes:
    """Read the upload stream in chunks, rejecting it as soon as it exceeds
    max_bytes — this is the actual enforcement point (this route is exempt
    from the global body-size middleware)."""
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(_UPLOAD_CHUNK_SIZE)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise DocumentTooLargeError(
                f"File exceeds the {max_bytes // (1024 * 1024)} MiB limit"
            )
        chunks.append(chunk)
    return b"".join(chunks)


async def _service(
    session: SessionDep, tenant: TenantDep, store: ObjectStoreOptionalDep
) -> DocumentService:
    return DocumentService(
        repo=SqlAlchemyDocumentRepository(session),
        store=store,
        bucket=bucket_for(get_settings(), tenant.slug),
        extract_text=_extract_text_async,
    )


ServiceDep = Annotated[DocumentService, Depends(_service)]


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=Envelope[DocumentResponse],
    summary="Upload a document (PDF, DOCX, TXT/MD) to the tenant library",
)
async def upload_document(
    principal: PrincipalDep,
    service: ServiceDep,
    file: Annotated[UploadFile, File()],
) -> Envelope[DocumentResponse]:
    data = await _read_capped(file, MAX_FILE_SIZE_BYTES)
    document = await service.upload(
        owner_id=_actor_id(principal),
        filename=file.filename or "",
        content_type=file.content_type or "",
        data=data,
    )
    return Envelope(data=DocumentResponse.from_entity(document))


@router.get(
    "",
    response_model=PagedEnvelope[DocumentResponse],
    summary="List documents visible to the caller",
)
async def list_documents(
    principal: PrincipalDep,
    service: ServiceDep,
    mine: Annotated[bool, Query(description="Only my own uploads")] = False,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> PagedEnvelope[DocumentResponse]:
    page = await service.list(
        actor_id=_actor_id(principal),
        roles=principal.roles,
        mine=mine,
        limit=limit,
        offset=offset,
    )
    return PagedEnvelope.from_page(page)


@router.get(
    "/{document_id}",
    response_model=Envelope[DocumentResponse],
    summary="Get a document's metadata (owner or ba/tenant_admin/platform_admin)",
)
async def get_document(
    document_id: UUID, principal: PrincipalDep, service: ServiceDep
) -> Envelope[DocumentResponse]:
    document = await service.get(
        document_id=document_id, actor_id=_actor_id(principal), roles=principal.roles
    )
    return Envelope(data=DocumentResponse.from_entity(document))


@router.delete(
    "/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a document (owner or tenant_admin/platform_admin)",
)
async def delete_document(
    document_id: UUID, principal: PrincipalDep, service: ServiceDep
) -> Response:
    await service.delete(
        document_id=document_id, actor_id=_actor_id(principal), roles=principal.roles
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
