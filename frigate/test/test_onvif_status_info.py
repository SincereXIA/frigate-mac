"""Tests for the camera PTZ status response."""

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from frigate.ptz.onvif import OnvifController


class TestOnvifStatusInfo(unittest.IsolatedAsyncioTestCase):
    def _controller(self, status=None, error=None):
        controller = object.__new__(OnvifController)
        ptz = SimpleNamespace(GetStatus=AsyncMock(return_value=status))
        if error is not None:
            ptz.GetStatus.side_effect = error
        controller.cams = {
            "camera": {
                "init": True,
                "ptz": ptz,
                "status_request": object(),
            }
        }
        controller.status_locks = {"camera": asyncio.Lock()}
        return controller

    async def test_returns_movement_and_position(self):
        status = SimpleNamespace(
            MoveStatus=SimpleNamespace(PanTilt="MOVING", Zoom="IDLE"),
            Position=SimpleNamespace(
                PanTilt=SimpleNamespace(x=0.25, y=-0.5),
                Zoom=SimpleNamespace(x=0.75),
            ),
        )
        controller = self._controller(status=status)

        result = await controller.get_camera_status_info("camera")

        self.assertEqual(
            result,
            {
                "camera": "camera",
                "available": True,
                "moving": True,
                "pan_tilt_status": "MOVING",
                "zoom_status": "IDLE",
                "position": {"pan": 0.25, "tilt": -0.5, "zoom": 0.75},
            },
        )

    async def test_accepts_scalar_idle_status_without_position(self):
        status = SimpleNamespace(MoveStatus="IDLE", Position=None)
        controller = self._controller(status=status)

        result = await controller.get_camera_status_info("camera")

        self.assertTrue(result["available"])
        self.assertFalse(result["moving"])
        self.assertIsNone(result["position"])

    async def test_status_failure_is_reported_without_error_details(self):
        controller = self._controller(error=RuntimeError("camera secret"))

        result = await controller.get_camera_status_info("camera")

        self.assertEqual(
            result,
            {
                "camera": "camera",
                "available": False,
                "moving": None,
                "pan_tilt_status": None,
                "zoom_status": None,
                "position": None,
            },
        )


if __name__ == "__main__":
    unittest.main()
