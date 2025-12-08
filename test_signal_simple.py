#!/usr/bin/env python3
"""
Simple test: uvicorn in thread, main thread blocked (simulating NSRunLoop).
"""

import sys
import signal
import threading
import logging
import time
from fastapi import FastAPI
import uvicorn

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

shutdown_event = threading.Event()

def signal_handler(signum, frame):
    logger.info(f"Signal {signum} received!")
    shutdown_event.set()

signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

app = FastAPI()

@app.get("/")
def root():
    return {"message": "Test server"}

config = uvicorn.Config(app, host="0.0.0.0", port=8000, log_level="info")
server = uvicorn.Server(config)

def run_server():
    try:
        server.run()
    except Exception as e:
        if not shutdown_event.is_set():
            logger.error(f"Server error: {e}")

server_thread = threading.Thread(target=run_server, daemon=False)
server_thread.start()

logger.info("Server started. Press Ctrl+C to stop.")
logger.info("Main thread will block (simulating NSRunLoop)...")

try:
    # Simulate blocking main thread (like NSRunLoop)
    while not shutdown_event.is_set() and server_thread.is_alive():
        shutdown_event.wait(timeout=0.1)
        if shutdown_event.is_set():
            logger.info("Shutdown event set, stopping server...")
            server.should_exit = True
            break
except KeyboardInterrupt:
    logger.info("KeyboardInterrupt caught!")
    shutdown_event.set()
    server.should_exit = True

logger.info("Waiting for server thread...")
server_thread.join(timeout=2.0)
logger.info("Done!")


