import enum
import selectors

from app.constants import COMMAND_PREFIX


class Commands(str, enum.Enum):
    GET_CLIENTS = "clients", None, "Get client list for connection"
    CONNECT_TO_CLIENT = "connect", ["username"], "Connect to another client"
    DISCONNECT_FROM_DIALOG = "disconnect", None, "Disconnect from current dialog"
    DIALOG = "dialog", None, "Show username of current dialogue partner"
    APPROVE_CHAT = "approve", ["username"], "Start chat with <username>"
    DECLINE_CHAT = "decline", ["username"], "Decline chat with <username>"
    REQUESTS = "requests", None, "Get all chat requests"
    HELP = "help", None, "Commands list."

    def __new__(cls, value: str, args: list[str] = None, description: str = None):
        member = str.__new__(cls, value)
        member._value_ = value
        member._args_ = args or []
        member._description_ = description or value
        return member

    @property
    def args(self) -> list:
        return self._args_

    @property
    def description(self) -> str:
        return self._description_

    @property
    def display(self) -> str:
        args = " " + ", ".join([f"<{arg}>" for arg in self._args_]) if self._args_ else ""
        return f"{COMMAND_PREFIX}{self._value_}{args} - {self._description_}"


class EventType(int, enum.Enum):
    READ = enum.auto()
    WRITE = enum.auto()

    @property
    def selector_type(self) -> int:
        return selectors.EVENT_READ if self == EventType.READ else selectors.EVENT_WRITE
