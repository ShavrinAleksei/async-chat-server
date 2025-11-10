from app.logging import setup_logging
from app.server import Server


def main():
    server = Server()
    server.run()


if __name__ == "__main__":
    setup_logging()
    main()
