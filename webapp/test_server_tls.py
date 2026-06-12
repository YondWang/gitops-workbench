from __future__ import annotations

import os
import unittest
from unittest import mock

import server


class ConfigureTlsTest(unittest.TestCase):
    def test_wraps_server_socket_when_cert_and_key_are_configured(self) -> None:
        httpd = mock.Mock()
        original_socket = object()
        httpd.socket = original_socket
        context = mock.Mock()
        context.wrap_socket.return_value = "wrapped-socket"

        with mock.patch.dict(
            os.environ,
            {
                "GITOPS_TLS_CERT": "/certs/fullchain.pem",
                "GITOPS_TLS_KEY": "/certs/privkey.pem",
            },
            clear=False,
        ), mock.patch("ssl.SSLContext", return_value=context) as ssl_context:
            result = server.configure_tls(httpd)

        self.assertTrue(result)
        ssl_context.assert_called_once()
        context.load_cert_chain.assert_called_once_with("/certs/fullchain.pem", "/certs/privkey.pem")
        context.wrap_socket.assert_called_once_with(original_socket, server_side=True)
        self.assertEqual(httpd.socket, "wrapped-socket")


if __name__ == "__main__":
    unittest.main()
