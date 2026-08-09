"""Tests for runtime-relative Frigate IPC endpoints."""

import unittest

from frigate.comms.config_updater import SOCKET_PUB_SUB as CONFIG_ENDPOINT
from frigate.comms.embeddings_updater import SOCKET_REP_REQ as EMBEDDINGS_ENDPOINT
from frigate.comms.inter_process import SOCKET_REP_REQ as COMMS_ENDPOINT
from frigate.comms.object_detector_signaler import (
    SOCKET_PUB as DETECTOR_PUBLISH_ENDPOINT,
)
from frigate.comms.object_detector_signaler import (
    SOCKET_SUB as DETECTOR_SUBSCRIBE_ENDPOINT,
)
from frigate.comms.zmq_proxy import SOCKET_PUB as PROXY_PUBLISH_ENDPOINT
from frigate.comms.zmq_proxy import SOCKET_SUB as PROXY_SUBSCRIBE_ENDPOINT
from frigate.const import RUNTIME_PATHS
from frigate.detectors.plugins.zmq_ipc import ZmqDetectorConfig


class TestNativeMacOSIpc(unittest.TestCase):
    """Verify every internal endpoint uses the configured runtime directory."""

    def test_internal_endpoints_are_runtime_relative(self) -> None:
        expected = {
            "config": CONFIG_ENDPOINT,
            "embeddings": EMBEDDINGS_ENDPOINT,
            "comms": COMMS_ENDPOINT,
            "detector_pub": DETECTOR_PUBLISH_ENDPOINT,
            "detector_sub": DETECTOR_SUBSCRIBE_ENDPOINT,
            "proxy_pub": PROXY_PUBLISH_ENDPOINT,
            "proxy_sub": PROXY_SUBSCRIBE_ENDPOINT,
        }

        for name, endpoint in expected.items():
            self.assertEqual(endpoint, RUNTIME_PATHS.ipc_endpoint(name))

    def test_external_detector_ipc_default_is_runtime_relative(self) -> None:
        endpoint = ZmqDetectorConfig.model_fields["endpoint"].default

        self.assertEqual(endpoint, RUNTIME_PATHS.ipc_endpoint("zmq_detector"))


if __name__ == "__main__":
    unittest.main()
