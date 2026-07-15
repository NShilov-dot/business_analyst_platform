from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.modules.tasks.domain.entities import Task, TaskPriority, TaskStatus
from app.modules.tasks.domain.errors import TaskTransitionError, TaskValidationError


def _make_task(**overrides) -> Task:
    return Task.create(
        owner_id=overrides.pop("owner_id", uuid4()),
        title=overrides.pop("title", "Write tests"),
        **overrides,
    )


class TestTaskCreate:
    def test_defaults_open_and_medium(self) -> None:
        task = _make_task()
        assert task.status is TaskStatus.OPEN
        assert task.priority is TaskPriority.MEDIUM
        assert task.completed_at is None
        assert task.created_at == task.updated_at

    def test_trims_title(self) -> None:
        task = _make_task(title="   ship it   ")
        assert task.title == "ship it"

    @pytest.mark.parametrize("bad", ["", "   ", "\n\t"])
    def test_rejects_blank_title(self, bad: str) -> None:
        with pytest.raises(TaskValidationError):
            _make_task(title=bad)

    def test_rejects_title_too_long(self) -> None:
        with pytest.raises(TaskValidationError):
            _make_task(title="x" * 201)

    def test_rejects_naive_due_at(self) -> None:
        with pytest.raises(TaskValidationError):
            _make_task(due_at=datetime(2030, 1, 1))  # naive datetime is the point of the test

    def test_accepts_tz_aware_due_at(self) -> None:
        due = datetime.now(UTC) + timedelta(days=1)
        task = _make_task(due_at=due)
        assert task.due_at == due


class TestTransitions:
    def test_start_open_to_in_progress(self) -> None:
        task = _make_task()
        before = task.updated_at
        task.start(now=before + timedelta(seconds=1))
        assert task.status is TaskStatus.IN_PROGRESS
        assert task.updated_at > before

    def test_start_already_started_is_forbidden(self) -> None:
        task = _make_task()
        task.start()
        with pytest.raises(TaskTransitionError):
            task.start()

    def test_complete_sets_completed_at(self) -> None:
        task = _make_task()
        ts = datetime.now(UTC)
        task.complete(now=ts)
        assert task.status is TaskStatus.DONE
        assert task.completed_at == ts
        assert task.updated_at == ts

    def test_complete_done_is_forbidden(self) -> None:
        task = _make_task()
        task.complete()
        with pytest.raises(TaskTransitionError):
            task.complete()

    def test_cancel_open(self) -> None:
        task = _make_task()
        task.cancel()
        assert task.status is TaskStatus.CANCELLED
        assert task.completed_at is None

    def test_cancel_after_done_is_forbidden(self) -> None:
        task = _make_task()
        task.complete()
        with pytest.raises(TaskTransitionError):
            task.cancel()


class TestUpdateDetails:
    def test_partial_update_only_touches_provided_fields(self) -> None:
        task = _make_task(description="initial")
        task.update_details(title="new title")
        assert task.title == "new title"
        assert task.description == "initial"

    def test_nullable_field_requires_set_flag_to_clear(self) -> None:
        task = _make_task(description="will stay")
        task.update_details(description=None, set_description=False)
        assert task.description == "will stay"

        task.update_details(description=None, set_description=True)
        assert task.description is None

    def test_cannot_edit_done_task(self) -> None:
        task = _make_task()
        task.complete()
        with pytest.raises(TaskTransitionError):
            task.update_details(title="too late")

    def test_cannot_edit_cancelled_task(self) -> None:
        task = _make_task()
        task.cancel()
        with pytest.raises(TaskTransitionError):
            task.update_details(priority=TaskPriority.HIGH)
