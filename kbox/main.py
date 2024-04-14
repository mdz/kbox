import logging
from .config import Config
from .server import Server

logging.basicConfig(level=logging.DEBUG)

config = Config()
server = Server(config)
server.run()