from app.entities import Client


class ClientDisconnected(Exception):

    def __init__(self, client: Client) -> None:
        self.client = client
