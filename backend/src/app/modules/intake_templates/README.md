# intake_templates module

Full hexagonal bounded context — Pre-MVP (Phase 1).

## Scope

Versioned catalog of intake forms for business requests. Six template types
(`feature_request`, `change`, `defect`, `integration_data`, `analytics_request`,
`free_form`) plus a static code-level routing table (§6.4). The `free_form`
type is the built-in system template seeded at migration time.

## Mandatory-core contract

Every **published** template version must contain all nine mandatory-core fields
with `required=True`. These keys are:

| Key | Label | Kind |
|---|---|---|
| `problem` | Проблема / потребность | textarea |
| `expected_result` | Ожидаемый результат | textarea |
| `success_metric` | Метрика успеха (числовая) | text |
| `as_is` | Текущее состояние (AS-IS) | textarea |
| `to_be` | Целевое состояние (TO-BE) | textarea |
| `affected_systems` | Затронутые системы и стейкхолдеры | textarea |
| `urgency_deadline` | Срочность и дедлайн | text |
| `acceptance_criteria` | Критерии приёмки (AC) | textarea |
| `business_goal` | Цель бизнеса (BG) | text |

Source: §5 + §6.3 of `AI_Business_Analyst_System.md`.

**`business_goal` note (Phase 1):** captured as a plain required text field.
In Phase 2 (MVP) a nullable `bg_item_id` FK on `requirements` artifacts will
link to a formal `BG-XX` item, superseding this free-text capture. The field
key is kept stable so no migration is needed.

## Selection rules provenance (§6.4)

`TEMPLATE_SELECTION_RULES` in `domain/entities.py` mirrors the §6.4 table
from `AI_Business_Analyst_System.md` row-for-row (6 rows): a business
situation maps to a recommended documentation template (BRD / SRS (+ BRD) /
Use Case / Change Specification / SRS / free form + mandatory core) and a
requirements level. Each row additionally carries `intake_template_type` —
the §5 intake form the BA triage feature should suggest; that column is a
convenience mapping, not part of §6.4. The `defect`, `integration_data` and
`analytics_request` intake types exist in the catalog (§5) but have no §6.4
selection row; adding dedicated rows for them is an open PO question.
The BA triage feature (Phase 1, `tickets` module) will consume this via
`TemplateService.get_selection_rules()`.

## Built-in free_form system template

Seeded deterministically in migration `0004_tenant_intake_templates`:
- Template UUID: `10000000-0000-0000-0000-000000000001`
- Version UUID: `20000000-0000-0000-0000-000000000001`
- Version 1 is seeded as `published` with exactly the nine mandatory-core fields.

The `is_system=True` flag protects this template:
- Its published version cannot be archived (`SystemTemplateProtectedError`).
- System templates cannot be deleted or renamed via the API (no such endpoints
  exist in Phase 1).

## Immutability rule

Once a `TemplateVersion` is published, its `fields` tuple is immutable.
Edits must create a new draft via `POST /v1/templates/{id}/new-draft`, which
copies the fields of the latest published version into a new draft.

## API surface (Phase 1)

| Method | Path | Roles | Description |
|---|---|---|---|
| GET | `/v1/templates` | any authenticated | Paginated list |
| GET | `/v1/templates/selection-rules` | any authenticated | Static §6.4 rules |
| GET | `/v1/templates/{id}` | any authenticated | Template + all versions |
| POST | `/v1/templates` | `ba`, `tenant_admin` | Create template (seeds draft v1) |
| PATCH | `/v1/templates/{id}/versions/{vid}/fields` | `ba`, `tenant_admin` | Replace draft fields |
| POST | `/v1/templates/{id}/versions/{vid}/publish` | `ba`, `tenant_admin` | Draft → published |
| POST | `/v1/templates/{id}/versions/{vid}/archive` | `ba`, `tenant_admin` | Published → archived |
| POST | `/v1/templates/{id}/new-draft` | `ba`, `tenant_admin` | New draft from latest published |

`validate_submission(template_version_id, payload)` is a service-layer method
only — consumed by the `tickets` module (SubmissionValidator port) in Phase 2,
not exposed as an HTTP endpoint in Phase 1.
