import dataclasses
import socket
import typing
import uuid

from app.enums import TypeTask


Address = tuple[str, int]
SocketGenerator = typing.Generator[tuple[TypeTask, socket.socket], None, None]

@dataclasses.dataclass(unsafe_hash=True)
class Client:
    id: uuid.UUID = dataclasses.field(init=False, hash=True, repr=False, default_factory=uuid.uuid4)
    username: str = dataclasses.field(init=False, hash=False, repr=True, default=None)
    __socket: socket.socket = dataclasses.field(hash=False, repr=False)
    address: Address = dataclasses.field(hash=False, repr=True)

    @property
    def socket(self) -> socket.socket:
        return self.__socket

    def set_username(self, username: str) -> None:
        self.username = username

    @property
    def is_registered(self) -> bool:
        return self.username is not None


@dataclasses.dataclass(unsafe_hash=True)
class Chat:
    id: uuid.UUID = dataclasses.field(init=False, hash=True, repr=False, default_factory=uuid.uuid4)
    initiator: Client = dataclasses.field(hash=False, repr=True)
    target: Client = dataclasses.field(hash=False, repr=True)

    is_approved: bool = dataclasses.field(hash=False, repr=True, default=False)

    @property
    def members(self) -> list:
        return [self.initiator, self.target]

    def approve(self) -> None:
        self.is_approved = True

    def get_second_member(self, client: Client) -> Client:
        match client:
            case self.initiator:
                return self.target
            case self.target:
                return self.initiator
            case _:
                raise RuntimeError()

@dataclasses.dataclass(unsafe_hash=True, frozen=True)
class MessageCommand:
    prefix = "/"
    command: str
    description: str
    args: list[str]

    @property
    def display(self) -> str:
        command_args_formatted = ", ".join([f"{command_arg}" for command_arg in self.args]) if self.args else "No args"
        return f"{self.prefix}{self.command} ({command_args_formatted}) - {self.description}"
