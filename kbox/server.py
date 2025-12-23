import logging
import signal
import threading

from .streaming import StreamingController


class Server:
    def __init__(self, config):
        self.config = config
        self.logger = logging.getLogger(__name__)
        self.streaming_controller = StreamingController(config, self)
        signal.signal(signal.SIGINT, self.stop)
        signal.signal(signal.SIGTERM, self.stop)

    def set_pitch_shift(self, semitones):
        self.streaming_controller.set_pitch_shift(semitones)

    def run(self):
        logging.debug("Starting server...")
        streaming_thread = threading.Thread(target=self.streaming_controller.run)
        streaming_thread.start()
        logging.info("Server started")
        streaming_thread.join()
        logging.info("Server stopped")

    def signal_handler(self, _signum, _frame):
        self.logger.debug("Received signal %s", _signum)
        if _signum in (signal.SIGINT, signal.SIGTERM):
            self.stop()

    def stop(self, _signum, _frame):
        self.logger.info("Stopping server...")
        self.streaming_controller.stop()
