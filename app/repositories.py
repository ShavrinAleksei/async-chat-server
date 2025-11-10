import socket

from app.entities import Client, Chat, Address
from app.logging import get_logger


class ChatRepository:
    __storage: set[Chat] = set()
    __logger = get_logger("chat_repository")

    def add_chat(self, initiator_client: Client, target_client: Client) -> Chat:
        chat = Chat(initiator=initiator_client, target=target_client)
        self.__storage.add(chat)
        self.__logger.info(
            "Created chat between clients.",
            initiator_client=initiator_client,
            target_client=target_client,
            chat=chat
        )
        return chat

    def delete_chat(self, chat: Chat) -> None:
        self.__storage.discard(chat)
        self.__logger.info(
            "Chat removed from storage.",
            chat=chat
        )

    def get_active_chat_by_client(self, client: Client) -> Chat | None:
        active_chat = next((chat for chat in self.__storage if client in chat.members and chat.is_approved), None)
        self.__logger.debug("Received active chat from storage by client.", client=client, chat=active_chat)
        return active_chat

    def get_inactive_chat_by_clients(self, initiator: Client, target: Client) -> Chat | None:
        inactive_chat = next(
            (chat for chat in self.__storage if
             target == chat.target and initiator == chat.initiator and not chat.is_approved),
            None
        )
        self.__logger.debug("Received inactive chat from storage by clients.", initiator=initiator, target=target,
                            chat=inactive_chat)
        return inactive_chat

    def get_inactive_chats_by_client(self, client) -> list[Chat] | None:
        inactive_chats = list(filter(lambda v: v.target == client and not v.is_approved, self.__storage))
        self.__logger.debug(
            "Received inactive chats by client",
            client=client,
            inactive_chats=inactive_chats
        )
        return inactive_chats

    def delete_chats_by_client(self, client: Client):
        client_chats = [chat for chat in self.__storage if client in chat.members]
        for chat in client_chats:
            self.delete_chat(chat)


class ClientsRepository:
    __storage: set[Client] = set()
    __logger = get_logger("clients_repository")

    def add_client(self, client_socket: socket.socket, client_address: Address,
                     username: str | None = None) -> Client:
        client = Client(client_socket, client_address)
        self.__storage.add(client)
        self.__logger.info(
            "Created client.",
            client=client
        )
        return client

    def get_client_by_socket(self, client_socket: socket.socket) -> Client | None:
        client = next((client for client in self.__storage if client.socket == client_socket), None)
        self.__logger.debug(
            "Received client by socket.",
            client=client,
            socket=client_socket
        )
        return client

    def get_client_by_username(self, username: str) -> Client | None:
        client = next((c for c in self.__storage if c.username == username), None)
        self.__logger.debug(
            "Received client by username.",
            client=client,
            username=username
        )
        return client

    def get_registered_clients(self) -> list[Client]:
        return list(
            filter(
                lambda cl: cl.is_registered,
                self.__storage
            )
        )

    def username_is_used(self, username: str) -> bool:
        return username in [client.username for client in self.__storage]

    def delete_client(self, client: Client) -> None:
        self.__storage.discard(client)
        self.__logger.info(f"Deleted client from storage.", client=client)
