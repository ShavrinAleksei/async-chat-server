import dataclasses
import select
import socket
import typing
from collections import deque

from app.enums import EventType
from app.logging import get_logger


@dataclasses.dataclass
class Event:
    _socket: socket.socket = dataclasses.field(repr=False)
    type: EventType

    @property
    def socket(self) -> socket.socket:
        return self._socket
Handler = typing.Generator[Event, None, None]


@dataclasses.dataclass
class Task:
    event: Event
    handler: Handler


@dataclasses.dataclass()
class Scheduler:
    ready_tasks: deque[Task] = dataclasses.field(default_factory=deque)
    tasks_waiting_for_read: dict[socket.socket, Task] = dataclasses.field(default_factory=dict)
    tasks_waiting_for_write: dict[socket.socket, Task] = dataclasses.field(default_factory=dict)

    _logger = get_logger("scheduler")

    def run(self) -> None:
        while any([self.ready_tasks, self.tasks_waiting_for_read, self.tasks_waiting_for_write]):
            self._logger.debug(
                "Ready tasks",
                ready_tasks=self.ready_tasks,
                tasks_waiting_for_read=self.tasks_waiting_for_read,
                tasks_waiting_for_write=self.tasks_waiting_for_write
            )
            if not self.ready_tasks:
                self._poll_events()

            task = self._get_next_ready_task()

            self._resume_task(task)

    def create_task(self, handler: Handler) -> None:
        try:
            event = None
            task = Task(event=event, handler=handler)
            self._add_ready_task(task)
        except StopIteration:
            return

    def _add_ready_task(self, task: Task) -> None:
        self.ready_tasks.append(task)
    
    def _get_next_ready_task(self) -> Task:
        task = self.ready_tasks.popleft()
        return task

    def _register_task(self, task: Task) -> None:
        self._logger.debug("Register task", task=task)
        if task.event.type == EventType.READ:
            self.tasks_waiting_for_read[task.event.socket] = task
        elif task.event.type == EventType.WRITE:
            self.tasks_waiting_for_write[task.event.socket] = task
        else:
            raise RuntimeError

    def _poll_events(self) -> None:
        ready_to_read, ready_to_write, _ = select.select(
            self.tasks_waiting_for_read,
            self.tasks_waiting_for_write,
            []
        )
        for ready_task_socket in ready_to_read:
            self._add_ready_task(
                self.tasks_waiting_for_read.pop(ready_task_socket)
            )
        for ready_task_socket in ready_to_write:
            self._add_ready_task(
                self.tasks_waiting_for_write.pop(ready_task_socket)
            )

    def _resume_task(self, task: Task) -> None:
        self._logger.debug("Resume task", task=task)
        try:
            event = next(task.handler)
            self._logger.debug("Receive event from task", task=task, event_handler=event)
            new_task = Task(event=event, handler=task.handler)
            self._register_task(new_task)
        except StopIteration:
            pass
