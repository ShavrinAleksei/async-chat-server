import logging
import select
import socket
import typing
from collections import deque

from app.constants import COMMAND_PREFIX
from app.entities import Chat, Client, SocketGenerator, Address
from app.enums import Commands, TypeTask
from app.exceptions import ClientDisconnected
from app.logging import get_logger


class Server:

    def __init__(self, host: str = 'localhost', port: int = 50_000, logger: logging.Logger = None) -> None:
        self.__logger = logger or get_logger("app.server")
        self.__host = host
        self.__port = port

        self.__socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.__socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.__socket.bind((self.__host, self.__port))

        self.__clients: set[Client] = set()
        self.__chats: set[Chat] = set()
        self.__buffer_size: int = 4096
        self.__commands: dict[Commands, typing.Callable] = {
            Commands.GET_CLIENTS: self.__execute_list_clients_command,
            Commands.CONNECT_TO_CLIENT: self.__execute_connect_command,
            Commands.DISCONNECT_FROM_DIALOG: self.__execute_disconnect_command,
            Commands.DIALOG: self.__execute_chat_info_command,
            Commands.APPROVE_CHAT: self.__execute_approve_chat_command,
            Commands.DECLINE_CHAT: self.__execute_decline_chat_command,
            Commands.REQUESTS: self.__execue_requests_command,
            Commands.HELP: self.__execute_help_command,
        }

        self.__ready_tasks_queue: deque[SocketGenerator] = deque()
        self.__tasks_waiting_for_read: dict[socket.socket, SocketGenerator] = dict()
        self.__tasks_waiting_for_write: dict[socket.socket, SocketGenerator] = dict()

    # SERVER METHODS
    def run(self) -> None:
        self.__socket.listen()
        self.__logger.info("Running server.", server_host=self.__host, server_port=self.__port)

        self.__register_task(self.__handle_client_connection())
        self.__event_loop()

    def __handle_client_connection(self) -> SocketGenerator:
        while True:
            yield TypeTask.READ, self.__socket
            client_socket, client_address = self.__socket.accept()
            client = self.__add_client(client_socket, client_address, None)
            yield from self.__send_message_to_client(client, "Hi! Write your username.")
            self.__register_task(self.__handle_register_client(client))

    def __handle_client_command(self, client: Client, raw_command: str, command_args: list[str]) -> SocketGenerator:
        self.__logger.debug(
            "Handle command from the client.",
            client=client,
            raw_command=raw_command,
            command_args=command_args,
        )
        try:
            parsed_command = Commands(raw_command)
            self.__logger.debug(
                "Parsed command from the client.",
                client=client,
                parsed_command=parsed_command,
                command_args=command_args,
            )
        except ValueError:
            self.__logger.debug(
                "Failed to parse the command from the client.",
                client=client,
                raw_command=raw_command,
                command_args=command_args,
            )
            yield from self.__send_message_to_client(client, f"Unknown command: {raw_command}.")
            return

        if parsed_command not in self.__commands:
            self.__logger.debug(
                "The command is not supported by the server.",
                client=client,
                parsed_command=parsed_command
            )
            yield from self.__send_message_to_client(client, f"Command not supported: {raw_command}.")

        handler = self.__commands[parsed_command]
        self.__logger.debug("Handler received for the command", handler=handler.__name__, parsed_command=parsed_command)

        if parsed_command.args and command_args and len(command_args) == len(parsed_command.args):
            yield from handler(client, *command_args)
        else:
            yield from handler(client)

    def __handle_client_message(self, client: Client) -> SocketGenerator:
        while True:
            yield TypeTask.READ, client.socket
            client_message = self.__receive_from_client_safe(client).decode()
            self.__logger.debug("Received message from client", client=client, message=client_message)

            if not client_message:
                self.__disconnect_client(client)
                return

            if client_message.startswith(COMMAND_PREFIX):
                raw_command, *command_args = client_message.replace(COMMAND_PREFIX, '').split()
                yield from self.__handle_client_command(client, raw_command, command_args)
                continue

            if self.__get_active_chat_by_client(client) is not None:
                yield from self.__handle_chat_message(client, client_message)
            else:
                yield from self.__send_message_to_client(client, f"You are not consistent with any chat.")

            self.__logger.info(f"handle {client} message: {client_message}")

    def __handle_chat_message(self, client: Client, message: str) -> SocketGenerator:
        chat = self.__get_active_chat_by_client(client)
        if not chat:
            return
        second_member = chat.get_second_member(client)

        yield from self.__send_message_to_client(second_member, f"{client.username}: {message.strip()}")

    def __handle_register_client(self, client: Client) -> SocketGenerator:
        while True:
            yield TypeTask.READ, client.socket
            username = self.__receive_from_client_safe(client).decode().strip()
            username_is_used = username in [client.username for client in self.__clients]
            if username_is_used:
                self.__logger.info(f"Client already exists.", client=client, input_username=username)
                yield from self.__send_message_to_client(client, "Username is already in use, try another one:")
                continue

            client.set_username(username)

            self.__logger.info(f"Registered new client.", client=client)
            self.__register_task(self.__handle_client_message(client))
            yield from self.__execute_help_command(client)
            break

    def __execute_help_command(self, client: Client) -> SocketGenerator:
        message = "Avaliable commands:\n"
        for cmd in self.__commands:
            message += cmd.display + '\n'
        yield from self.__send_message_to_client(client, message.strip())

    def __execute_connect_command(self, initiator_client: Client, target_client_username: str) -> SocketGenerator:
        if initiator_client.username == target_client_username:
            yield from self.__send_message_to_client(initiator_client, "Client is trying to connect to itself.")
            return

        active_client_chat = self.__get_active_chat_by_client(initiator_client)
        if active_client_chat is not None:
            second_member = active_client_chat.get_second_member(initiator_client)
            yield from self.__send_message_to_client(initiator_client, f"You already in chat with {second_member.username}.")
            return

        target_client = self.__get_client_by_username(target_client_username)
        if not target_client:
            yield from self.__send_message_to_client(initiator_client, "Client may be disconnected.")
            return

        self.__add_chat(initiator_client, target_client)
        yield from self.__send_message_to_client(target_client, f"{initiator_client.username} wants to start a chat with you.")

    def __execute_list_clients_command(self, client: Client) -> SocketGenerator:
        clients_list_message = "\n".join(
            [
                current_client.username for current_client in filter(
                    lambda cl: cl.is_registered and cl.username != client.username,
                    self.__clients
                )
            ]
        )
        if not clients_list_message:
            clients_list_message = "No available clients."

        yield from self.__send_message_to_client(client, clients_list_message)

    def __execute_disconnect_command(self, client: Client) -> SocketGenerator:
        current_chat = self.__get_active_chat_by_client(client)

        if current_chat is None:
            yield from self.__send_message_to_client(client, "You have no active chat now.")
            return

        self.__delete_chat(current_chat)

        for member in current_chat.members:
            second_member = current_chat.get_second_member(member)
            yield from self.__send_message_to_client(member, f"Chat with {second_member.username} ended.")

    def __execute_chat_info_command(self, client) -> SocketGenerator:
        chat = self.__get_active_chat_by_client(client)
        if chat is None:
            yield from self.__send_message_to_client(client, "You do not have active chats.")
            return

        second_member = chat.get_second_member(client)
        yield from self.__send_message_to_client(client, f"You have active chat with {second_member.username}.")

    def __execute_approve_chat_command(self, client: Client, username: str) -> SocketGenerator:
        if client.username == username:
            self.__logger.debug("Client is trying to approve a chat with himself.", client=client)
            yield from self.__send_message_to_client(client, "You are trying to approve a chat with yourself.")
            return

        active_client_chat = self.__get_active_chat_by_client(client)
        if active_client_chat is not None:
            second_member = active_client_chat.get_second_member(client)
            self.__logger.debug("The client already has an active chat.", client=client, chat=active_client_chat)
            yield from self.__send_message_to_client(client, f"You already has an active chat with {second_member.username}.")
            return

        chat_initiator = self.__get_client_by_username(username)
        if not chat_initiator:
            self.__logger.debug("Chat initiator not found.", username=username)
            yield from self.__send_message_to_client(client, "Chat initiator may be disconnected.")
            return

        current_initiator_chat = self.__get_active_chat_by_client(chat_initiator)
        if current_initiator_chat is not None:
            self.__logger.debug("Chat initiator already has an active chat.", chat_initiator=chat_initiator, chat=current_initiator_chat)
            yield from self.__send_message_to_client(client, f"{chat_initiator.username} already has an active chat.")
            return

        inactive_chat = self.__get_inactive_chat_by_clients(chat_initiator, client)
        if inactive_chat:
            inactive_chat.approve()
            self.__logger.info("Chat approved.", chat=inactive_chat)
            yield from self.__send_message_to_client(
                inactive_chat.initiator,
                f"You started a chat with {inactive_chat.target.username}."
            )
            yield from self.__send_message_to_client(
                inactive_chat.target,
                f"You started a chat with {inactive_chat.initiator.username}."
            )
        else:
            self.__logger.info("Clients have no inactive chat.", initiator=chat_initiator, target=client, chat=inactive_chat)
            yield from self.__send_message_to_client(
                client,
                f"You have no chat request from {username}."
            )

    def __execute_decline_chat_command(self, client: Client, username: str) -> SocketGenerator:
        if client.username == username:
            self.__logger.debug("Client is trying to decline a chat with himself.", client=client)
            yield from self.__send_message_to_client(client, "You are trying to decline a chat with yourself.")
            return

        chat_initiator = self.__get_client_by_username(username)
        if not chat_initiator:
            self.__logger.debug("Chat initiator not found.", username=username)
            yield from self.__send_message_to_client(client, "Chat initiator may be disconnected.")
            return

        inactive_chat = self.__get_inactive_chat_by_clients(chat_initiator, client)
        if inactive_chat:
            self.__delete_chat(inactive_chat)
            self.__logger.info("Chat declined.", chat=inactive_chat)
            yield from self.__send_message_to_client(
                inactive_chat.target,
                f"You declined a chat request from {inactive_chat.initiator.username}."
            )
            yield from self.__send_message_to_client(
                inactive_chat.initiator,
                f"{inactive_chat.target.username} declined your chat request."
            )
        else:
            self.__logger.info("Clients have no inactive chat.", initiator=chat_initiator, target=client, chat=inactive_chat)
            yield from self.__send_message_to_client(
                client,
                f"You have no chat request from {username}."
            )

    def __execue_requests_command(self, client) -> SocketGenerator:
        inactive_chats = self.__get_inactive_chats_by_client(client)
        if not inactive_chats:
            message = "You not have chat requests"
        else:
            message = "Chat requests from:\n"
            for i, inactive_chat in enumerate(inactive_chats, start=1):
                message += f"{i}. {inactive_chat.initiator.username}\n"

        yield from self.__send_message_to_client(client, message.strip())

    # SCHEDULER METHODS
    def __register_task(self, gen: SocketGenerator) -> None:
        self.__ready_tasks_queue.append(gen)
        self.__logger.debug("Registered task.", task=gen.__name__)

    def __get_next_task(self) -> SocketGenerator:
        task = self.__ready_tasks_queue.popleft()
        self.__logger.debug("Recieved next ready task.", task=task.__name__)
        return task

    def __run_task(self, task: SocketGenerator) -> None:
        self.__logger.debug("Run task.", task=task.__name__)

        try:
            task_type, _socket = next(task)
        except StopIteration:
            return None
        except ClientDisconnected:
            return None

        if task_type == TypeTask.READ:
            self.__tasks_waiting_for_read[_socket] = task
        elif task_type == TypeTask.WRITE:
            self.__tasks_waiting_for_write[_socket] = task
        else:
            raise RuntimeError
        self.__logger.debug("Moved task to awaiting", task=task.__name__, task_type=task_type)

    def __event_loop(self) -> None:
        while any([self.__ready_tasks_queue, self.__tasks_waiting_for_read, self.__tasks_waiting_for_write]):
            while not self.__ready_tasks_queue:
                ready_to_read, ready_to_write, _ = select.select(
                    self.__tasks_waiting_for_read,
                    self.__tasks_waiting_for_write,
                    []
                )
                for ready_task_socket in ready_to_read:
                    self.__register_task(
                        self.__tasks_waiting_for_read.pop(ready_task_socket)
                    )
                for ready_task_socket in ready_to_write:
                    self.__register_task(
                        self.__tasks_waiting_for_write.pop(ready_task_socket)
                    )

            task = self.__get_next_task()
            self.__run_task(task)

    def __delete_tasks_by_client(self, client: Client) -> None:
        self.__tasks_waiting_for_read.pop(client.socket, None)
        self.__tasks_waiting_for_write.pop(client.socket, None)
        self.__logger.info(f"Deleted client tasks from queue")

    # CLIENTS REPOSITORY METHODS
    def __add_client(self, client_socket: socket.socket, client_address: Address, username: str | None = None) -> Client:
        client = Client(client_socket, client_address)
        self.__clients.add(client)
        self.__logger.info(
            "Created client.",
            client=client
        )
        return client

    def __get_client_by_socket(self, client_socket: socket.socket) -> Client | None:
        client = next((client for client in self.__clients if client.socket == client_socket), None)
        self.__logger.debug(
            "Received client by socket.",
            client=client,
            socket=client_socket
        )
        return client

    def __get_client_by_username(self, username: str) -> Client | None:
        client = next((c for c in self.__clients if c.username == username), None)
        self.__logger.debug(
            "Received client by username.",
            client=client,
            username=username
        )
        return client

    def __delete_client(self, client: Client) -> None:
        self.__clients.discard(client)
        self.__logger.info(f"Deleted client from storage.", client=client)

    # CHAT REPOSITORY METHODS
    def __add_chat(self, initiator_client: Client, target_client: Client) -> Chat:
        chat = Chat(initiator=initiator_client, target=target_client)
        self.__chats.add(chat)
        self.__logger.info(
            "Created chat between clients.",
            initiator_client=initiator_client,
            target_client=target_client,
            chat=chat
        )
        return chat

    def __delete_chat(self, chat: Chat) -> None:
        self.__chats.discard(chat)
        self.__logger.info(
            "Chat removed from storage.",
            chat=chat
        )

    def __get_active_chat_by_client(self, client: Client) -> Chat | None:
        active_chat = next((chat for chat in self.__chats if client in chat.members and chat.is_approved), None)
        self.__logger.debug("Received active chat from storage by client.", client=client, chat=active_chat)
        return active_chat

    def __get_inactive_chat_by_clients(self, initiator: Client, target: Client) -> Chat | None:
        inactive_chat = next(
            (chat for chat in self.__chats if target == chat.target and initiator == chat.initiator and not chat.is_approved),
            None
        )
        self.__logger.debug("Received inactive chat from storage by clients.", initiator=initiator, target=target, chat=inactive_chat)
        return inactive_chat

    def __get_inactive_chats_by_client(self, client) -> list[Chat] | None:
        inactive_chats = list(filter(lambda v: v.target == client, self.__chats))
        self.__logger.debug(
            "Received inactive chatsd by client",
            client=client,
            inactive_chats=inactive_chats
        )
        return inactive_chats

    def __delete_chats_by_client(self, client: Client):
        client_chats = [chat for chat in self.__chats if client in chat.members]
        for chat in client_chats:
            self.__delete_chat(chat)

    # HELP METHODS
    def __disconnect_client(self, client: Client) -> None:
        self.__logger.info(f"Dicsonnect client {client}")
        self.__delete_chats_by_client(client)
        self.__delete_client(client)

        self.__delete_tasks_by_client(client)

        client.socket.close()

    def __receive_from_client_safe(self, client: Client) -> bytes:
        try:
            client_data = client.socket.recv(self.__buffer_size)
            if not client_data:
                self.__disconnect_client(client)
                self.__logger.debug("The client turned off", client=client)
                raise ClientDisconnected(client)
            self.__logger.debug(f"Received data from client", client=client, client_data=client_data)
            return client_data
        except ConnectionResetError:
            self.__disconnect_client(client)
            self.__logger.debug("Client reset connection.", client=client)
            raise ClientDisconnected(client)

    def __send_message_to_client(self, client: Client, message: str) -> SocketGenerator:
        yield TypeTask.WRITE, client.socket
        self.__logger.debug("Sending a message to the client.", client=client, message=message)
        try:
            client.socket.send(message.encode() + b"\n")
            self.__logger.info("Message sent to the client.", client=client, message=message)
        except ConnectionResetError:
            self.__disconnect_client(client)




