"""CrawlTask service with transition guards."""

from __future__ import annotations

from sqlalchemy.orm import Session

from ..exceptions import InvalidTaskTransitionError, TaskNotFoundError
from ..models import CrawlTask, TaskStatus


class CrawlTaskService:
    def __init__(self, session: Session):
        self.session = session

    def create_task(self, keywords: str, platform: str = "xhs") -> CrawlTask:
        task = CrawlTask(keywords=keywords, platform=platform, status=TaskStatus.pending)
        self.session.add(task)
        self.session.commit()
        self.session.refresh(task)
        return task

    def get_task(self, task_id: int) -> CrawlTask:
        task = self.session.get(CrawlTask, task_id)
        if task is None:
            raise TaskNotFoundError(f"task {task_id} not found")
        return task

    def start_task(self, task_id: int) -> CrawlTask:
        task = self.get_task(task_id)
        self._ensure_status(task, allowed={TaskStatus.pending, TaskStatus.failed}, to=TaskStatus.running)
        task.status = TaskStatus.running
        task.error_message = None
        self.session.commit()
        self.session.refresh(task)
        return task

    def complete_task(self, task_id: int, note_count: int) -> CrawlTask:
        task = self.get_task(task_id)
        self._ensure_status(task, allowed={TaskStatus.running}, to=TaskStatus.done)
        task.status = TaskStatus.done
        task.note_count = note_count
        task.error_message = None
        self.session.commit()
        self.session.refresh(task)
        return task

    def fail_task(self, task_id: int, error_message: str) -> CrawlTask:
        task = self.get_task(task_id)
        self._ensure_status(task, allowed={TaskStatus.running}, to=TaskStatus.failed)
        task.status = TaskStatus.failed
        task.error_message = self._fit_error_message(error_message, max_len=1024)
        self.session.commit()
        self.session.refresh(task)
        return task

    @staticmethod
    def _ensure_status(task: CrawlTask, allowed: set[TaskStatus], to: TaskStatus) -> None:
        if task.status not in allowed:
            allowed_text = ", ".join(sorted(s.value for s in allowed))
            raise InvalidTaskTransitionError(
                f"invalid transition {task.status.value} -> {to.value}; allowed from: {allowed_text}"
            )

    @staticmethod
    def _fit_error_message(error_message: str, *, max_len: int) -> str:
        if len(error_message) <= max_len:
            return error_message
        suffix = "...(truncated)"
        keep = max_len - len(suffix)
        if keep <= 0:
            return error_message[:max_len]
        return f"{error_message[:keep]}{suffix}"
