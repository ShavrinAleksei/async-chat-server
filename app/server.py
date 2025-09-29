import logging
import select
import socket
import typing
from collections import deque

from app.constants import COMMAND_PREFIX
from app.entities import Chat, Client, SocketGenerator, Address
from app.enums import Commands, EventType
from app.exceptions import ClientDisconnected
from app.logging import get_logger
from app.repositories import ClientsRepository, ChatRepository
from app.scheduler import Scheduler, Event


class Server:

    def __init__(self, host: str = 'localhost', port: int = 50_000, logger: logging.Logger = None) -> None:
        self._logger = logger or get_logger("app.server")
        self.__host = host
        self.__port = port

        self.__socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.__socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.__socket.bind((self.__host, self.__port))

        self.__clients_repo: ClientsRepository = ClientsRepository()
        self.__chats_repo: ChatRepository = ChatRepository()
        self.__buffer_size: int = 4096
        self.__commands: dict[Commands, typing.Callable] = {
            Commands.GET_CLIENTS: self.__execute_list_clients_command,
            Commands.CONNECT_TO_CLIENT: self.__execute_connect_command,
            Commands.DISCONNECT_FROM_DIALOG: self.__execute_disconnect_command,
            Commands.DIALOG: self.__execute_chat_info_command,
            Commands.APPROVE_CHAT: self.__execute_approve_chat_command,
            Commands.DECLINE_CHAT: self.__execute_decline_chat_command,
            Commands.REQUESTS: self.__execute_requests_command,
            Commands.HELP: self.__execute_help_command,
        }

        self.__scheduler = Scheduler()

    # SERVER METHODS
    def run(self) -> None:
        self.__socket.listen()
        self._logger.info("Running server.", server_host=self.__host, server_port=self.__port)

        self.__scheduler.create_task(self.__handle_client_connection())
        self.__scheduler.run()

    def __handle_client_connection(self) -> typing.Generator[Event]:
        while True:
            yield Event(self.__socket, EventType.READ)
            client_socket, client_address = self.__socket.accept()
            client = self.__clients_repo.add_client(client_socket, client_address, None)
            yield from self.__send_message_to_client(client, "Hi! Write your username.")
            self.__scheduler.create_task(self.__handle_register_client(client))

    def __handle_client_command(self, client: Client, raw_command: str, command_args: list[str]) -> typing.Generator[Event]:
        self._logger.debug(
            "Handle command from the client.",
            client=client,
            raw_command=raw_command,
            command_args=command_args,
        )
        try:
            parsed_command = Commands(raw_command)
            self._logger.debug(
                "Parsed command from the client.",
                client=client,
                parsed_command=parsed_command,
                command_args=command_args,
            )
        except ValueError:
            self._logger.debug(
                "Failed to parse the command from the client.",
                client=client,
                raw_command=raw_command,
                command_args=command_args,
            )
            yield from self.__send_message_to_client(client, f"Unknown command: {raw_command}.")
            return

        if parsed_command not in self.__commands:
            self._logger.debug(
                "The command is not supported by the server.",
                client=client,
                parsed_command=parsed_command
            )
            yield from self.__send_message_to_client(client, f"Command not supported: {raw_command}.")
            return

        handler = self.__commands[parsed_command]
        self._logger.debug("Handler received for the command", handler=handler.__name__, parsed_command=parsed_command)

        if parsed_command.args:
            if command_args and len(command_args) == len(parsed_command.args):
                yield from handler(client, *command_args)
            else:
                self._logger.debug(
                    "Handler received command with invalid args",
                    handler=handler.__name__,
                    parsed_command=parsed_command,
                    command_args=command_args
                )
                yield from self.__send_message_to_client(client, "Invalid command args.")
        else:
            yield from handler(client)

    def __handle_client_message(self, client: Client) -> typing.Generator[Event]:
        while True:
            try:
                yield Event(client.socket, EventType.READ)
                client_message = self.__receive_from_client_safe(client).decode()
            except ClientDisconnected:
                return

            self._logger.debug("Received message from client", client=client, message=client_message)

            if not client_message:
                self.__disconnect_client(client)
                return

            if client_message.startswith(COMMAND_PREFIX):
                raw_command, *command_args = client_message.replace(COMMAND_PREFIX, '').split()
                yield from self.__handle_client_command(client, raw_command, command_args)
                continue

            if self.__chats_repo.get_active_chat_by_client(client) is not None:
                yield from self.__handle_chat_message(client, client_message)
            else:
                yield from self.__send_message_to_client(client, f"You are not consistent with any chat.")

            self._logger.info(f"handle {client} message: {client_message}")

    def __handle_chat_message(self, client: Client, message: str) -> typing.Generator[Event]:
        chat = self.__chats_repo.get_active_chat_by_client(client)
        if not chat:
            return
        second_member = chat.get_second_member(client)

        yield from self.__send_message_to_client(second_member, f"{client.username}: {message.strip()}")

    def __handle_register_client(self, client: Client) -> typing.Generator[Event]:
        while True:
            try:
                yield Event(client.socket, EventType.READ)
                username = self.__receive_from_client_safe(client).decode().strip()
            except ClientDisconnected:
                return

            username_is_used = self.__clients_repo.username_is_used(username)
            if username_is_used:
                self._logger.info(f"Client already exists.", client=client, input_username=username)
                self.__scheduler.create_task(self.__send_message_to_client(client, "Username is already in use, try another one:"))
                continue

            client.set_username(username)

            self._logger.info(f"Registered new client.", client=client)
            yield from self.__execute_help_command(client)
            self.__scheduler.create_task(self.__handle_client_message(client))
            break

    def __execute_help_command(self, client: Client) -> typing.Generator[Event]:
        message = "Available commands:\n"
        for cmd in self.__commands:
            message += cmd.display + '\n'
        yield from self.__send_message_to_client(client, message.strip())

    def __execute_connect_command(self, initiator_client: Client, target_client_username: str) -> typing.Generator[Event]:
        if initiator_client.username == target_client_username:
            yield from self.__send_message_to_client(initiator_client, "Client is trying to connect to itself.")
            return

        active_client_chat = self.__chats_repo.get_active_chat_by_client(initiator_client)
        if active_client_chat is not None:
            second_member = active_client_chat.get_second_member(initiator_client)
            yield from self.__send_message_to_client(initiator_client, f"You already in chat with {second_member.username}.")
            return

        target_client = self.__clients_repo.get_client_by_username(target_client_username)
        if not target_client:
            yield from self.__send_message_to_client(initiator_client, "Client may be disconnected.")
            return

        self.__chats_repo.add_chat(initiator_client, target_client)
        yield from self.__send_message_to_client(target_client, f"{initiator_client.username} wants to start a chat with you.")

    def __execute_list_clients_command(self, client: Client) -> typing.Generator[Event]:
        clients_list_message = "\n".join(
            [
                current_client.username for current_client in filter(
                    lambda cl: cl.username != client.username,
                    self.__clients_repo.get_registered_clients()
                )
            ]
        )
        if not clients_list_message:
            clients_list_message = "No available clients."

        yield from self.__send_message_to_client(client, clients_list_message)

    def __execute_disconnect_command(self, client: Client) -> typing.Generator[Event]:
        current_chat = self.__chats_repo.get_active_chat_by_client(client)

        if current_chat is None:
            yield from self.__send_message_to_client(client, "You have no active chat now.")
            return

        self.__chats_repo.delete_chat(current_chat)

        for member in current_chat.members:
            second_member = current_chat.get_second_member(member)
            yield from self.__send_message_to_client(member, f"Chat with {second_member.username} ended.")

    def __execute_chat_info_command(self, client) -> typing.Generator[Event]:
        chat = self.__chats_repo.get_active_chat_by_client(client)
        if chat is None:
            yield from self.__send_message_to_client(client, "You do not have active chats.")
            return

        second_member = chat.get_second_member(client)
        yield from self.__send_message_to_client(client, f"You have active chat with {second_member.username}.")

    def __execute_approve_chat_command(self, client: Client, username: str) -> typing.Generator[Event]:
        if client.username == username:
            self._logger.debug("Client is trying to approve a chat with himself.", client=client)
            yield from self.__send_message_to_client(client, "You are trying to approve a chat with yourself.")
            return

        active_client_chat = self.__chats_repo.get_active_chat_by_client(client)
        if active_client_chat is not None:
            second_member = active_client_chat.get_second_member(client)
            self._logger.debug("The client already has an active chat.", client=client, chat=active_client_chat)
            yield from self.__send_message_to_client(client, f"You already has an active chat with {second_member.username}.")
            return

        chat_initiator = self.__clients_repo.get_client_by_username(username)
        if not chat_initiator:
            self._logger.debug("Chat initiator not found.", username=username)
            yield from self.__send_message_to_client(client, "Chat initiator may be disconnected.")
            return

        current_initiator_chat = self.__chats_repo.get_active_chat_by_client(chat_initiator)
        if current_initiator_chat is not None:
            self._logger.debug("Chat initiator already has an active chat.", chat_initiator=chat_initiator, chat=current_initiator_chat)
            yield from self.__send_message_to_client(client, f"{chat_initiator.username} already has an active chat.")
            return

        inactive_chat = self.__chats_repo.get_inactive_chat_by_clients(chat_initiator, client)
        if inactive_chat:
            inactive_chat.approve()
            self._logger.info("Chat approved.", chat=inactive_chat)
            yield from self.__send_message_to_client(
                inactive_chat.initiator,
                f"You started a chat with {inactive_chat.target.username}."
            )
            yield from self.__send_message_to_client(
                inactive_chat.target,
                f"You started a chat with {inactive_chat.initiator.username}."
            )
        else:
            self._logger.info("Clients have no inactive chat.", initiator=chat_initiator, target=client, chat=inactive_chat)
            yield from self.__send_message_to_client(
                client,
                f"You have no chat request from {username}."
            )

    def __execute_decline_chat_command(self, client: Client, username: str) -> typing.Generator[Event]:
        if client.username == username:
            self._logger.debug("Client is trying to decline a chat with himself.", client=client)
            yield from self.__send_message_to_client(client, "You are trying to decline a chat with yourself.")
            return

        chat_initiator = self.__clients_repo.get_client_by_username(username)
        if not chat_initiator:
            self._logger.debug("Chat initiator not found.", username=username)
            yield from self.__send_message_to_client(client, "Chat initiator may be disconnected.")
            return

        inactive_chat = self.__chats_repo.get_inactive_chat_by_clients(chat_initiator, client)
        if inactive_chat:
            self.__chats_repo.delete_chat(inactive_chat)
            self._logger.info("Chat declined.", chat=inactive_chat)
            yield from self.__send_message_to_client(
                inactive_chat.target,
                f"You declined a chat request from {inactive_chat.initiator.username}."
            )
            yield from self.__send_message_to_client(
                inactive_chat.initiator,
                f"{inactive_chat.target.username} declined your chat request."
            )
        else:
            self._logger.info("Clients have no inactive chat.", initiator=chat_initiator, target=client, chat=inactive_chat)
            yield from self.__send_message_to_client(
                client,
                f"You have no chat request from {username}."
            )

    def __execute_requests_command(self, client) -> typing.Generator[Event]:
        inactive_chats = self.__chats_repo.get_inactive_chats_by_client(client)
        if not inactive_chats:
            message = "You not have chat requests"
        else:
            message = "Chat requests from:\n"
            for i, inactive_chat in enumerate(inactive_chats, start=1):
                message += f"{i}. {inactive_chat.initiator.username}\n"

        yield from self.__send_message_to_client(client, message.strip())

    # HELP METHODS
    def __disconnect_client(self, client: Client) -> None:
        self._logger.info(f"Dicsonnect client {client}")

        self.__chats_repo.delete_chats_by_client(client)
        self.__clients_repo.delete_client(client)
        self.__scheduler.delete_tasks_by_client(client)

        client.socket.close()

    def __receive_from_client_safe(self, client: Client) -> bytes:
        try:
            client_data = client.socket.recv(self.__buffer_size)
            if not client_data:
                self.__disconnect_client(client)
                self._logger.debug("The client turned off", client=client)
                raise ClientDisconnected(client)
            self._logger.debug(f"Received data from client", client=client, client_data=client_data)
            return client_data
        except ConnectionResetError:
            self.__disconnect_client(client)
            self._logger.debug("Client reset connection.", client=client)
            raise ClientDisconnected(client)

    def __send_message_to_client(self, client: Client, message: str) -> typing.Generator[Event]:
        yield Event(client.socket, EventType.WRITE)
        self._logger.debug("Sending a message to the client.", client=client, message=message)
        try:
            client.socket.send(message.encode() + b"\n")
            self._logger.info("Message sent to the client.", client=client, message=message)
        except ConnectionResetError:
            self.__disconnect_client(client)
