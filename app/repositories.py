from app.entities import Client, Chat


class ChatRepository:
    __storage = set()

    def get_current_chat_by_client(self, client: Client) -> Chat:
        ...

    def get_waiting_approve_chats_by_client(self, client: Client) -> list[Chat]:
        ...


class ClientsRepository:
    __storage = set()

    def get_current_chat_by_client(self, client: Client) -> Chat:
        ...

    def get_waiting_approve_chats_by_client(self, client: Client) -> list[Chat]:
        ...
