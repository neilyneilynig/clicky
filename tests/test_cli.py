import os
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from clicky.cli import already_running


class SingleInstanceTests(unittest.TestCase):
    def test_detects_a_live_socket(self):
        with tempfile.TemporaryDirectory() as runtime:
            server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            server.bind(os.path.join(runtime, "clicky.sock"))
            server.listen()
            self.addCleanup(server.close)

            with patch.dict(os.environ, {"XDG_RUNTIME_DIR": runtime}):
                self.assertTrue(already_running())

    def test_a_stale_socket_file_does_not_count(self):
        with tempfile.TemporaryDirectory() as runtime:
            Path(runtime, "clicky.sock").touch()

            with patch.dict(os.environ, {"XDG_RUNTIME_DIR": runtime}):
                self.assertFalse(already_running())
