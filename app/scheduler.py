import dataclasses
import selectors
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

    selector: selectors.SelectSelector = selectors.DefaultSelector()

    _logger = get_logger("scheduler")

    def run(self) -> None:
        while self.ready_tasks or self.selector.get_map():
            self._logger.debug("Ready tasks", ready_tasks=self.ready_tasks, selectors_map=dict(self.selector.get_map()))
            if not self.ready_tasks:
                self._poll_events()

            task = self._get_next_ready_task()

            self._resume_task(task)

    def create_task(self, handler: Handler) -> None:
        try:
            event = None #next(handler)
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

        try:
            self.selector.get_key(task.event.socket)
            self.selector.modify(
                fileobj=task.event.socket,
                events=task.event.type.selector_type,
                data=task,
            )
        except KeyError:
            self.selector.register(
                fileobj=task.event.socket,
                events=task.event.type.selector_type,
                data=task,
            )

    def _unregister_task(self, task: Task) -> None:
        self._logger.debug("Unregister task", task=task)
        self.selector.unregister(fileobj=task.event.socket)

    def _poll_events(self) -> None:
        for key, mask in self.selector.select():
            self._logger.debug("Selector event", selector_key=key, selector_mask=mask)
            task: Task = key.data
            self._add_ready_task(task)
            self._unregister_task(task)

    def _resume_task(self, task: Task) -> None:
        self._logger.debug("Resume task", task=task)
        try:
            event = next(task.handler)
            self._logger.debug("Receive event from task", task=task, event_handler=event)
            new_task = Task(event=event, handler=task.handler)
            self._register_task(new_task)
        except StopIteration:
            pass
