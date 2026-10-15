"""Windows-only click-guard tests; all user32 calls are mocked."""
from __future__ import annotations

import ctypes as C
from pathlib import Path
import sys
import unittest
from unittest.mock import patch


SCRIPTS = Path(__file__).resolve().parents[1] / "skill" / "copilot-consult" / "scripts"
sys.path.insert(0, str(SCRIPTS))

native_uia = None
if sys.platform == "win32":
    try:
        import native_uia  # noqa: E402
    except OSError:
        # The click tests need the native Windows DLLs to import the binding.
        native_uia = None


WINDOW_HANDLE = 100
CHILD_HANDLE = 200
OTHER_HANDLE = 300
EXPECTED_POINT = (110, 205)


class FakeWindow:
    def prop(self, key):
        if key != "handle":
            raise AssertionError(f"Unexpected window property: {key}")
        return WINDOW_HANDLE


class FakeElement:
    def ready(self):
        pass

    def bounds(self):
        return (100.0, 200.0, 20.0, 10.0)


class FakeUser:
    def __init__(
        self,
        *,
        foreground=(WINDOW_HANDLE, WINDOW_HANDLE),
        set_cursor_result=True,
        get_cursor_result=True,
        cursor_offset=(0, 0),
        root_handle=WINDOW_HANDLE,
        send_result=2,
    ):
        self.foregrounds = list(foreground)
        self.set_cursor_result = set_cursor_result
        self.get_cursor_result = get_cursor_result
        self.cursor_offset = cursor_offset
        self.root_handle = root_handle
        self.send_result = send_result
        self.foreground_calls = []
        self.position_calls = []
        self.cursor_reads = 0
        self.window_points = []
        self.send_calls = []
        self.position = None

    def SetForegroundWindow(self, handle):
        self.foreground_calls.append(handle)
        return 1

    def GetForegroundWindow(self):
        if self.foregrounds:
            result = self.foregrounds.pop(0)
        else:
            result = WINDOW_HANDLE
        return result

    def SetCursorPos(self, x, y):
        self.position_calls.append((x, y))
        if self.set_cursor_result:
            self.position = (x, y)
        return int(self.set_cursor_result)

    def GetCursorPos(self, pointer):
        self.cursor_reads += 1
        if not self.get_cursor_result:
            return 0
        x, y = self.position
        x += self.cursor_offset[0]
        y += self.cursor_offset[1]
        point = C.cast(pointer, C.POINTER(native_uia.W.POINT)).contents
        point.x = x
        point.y = y
        return 1

    def WindowFromPoint(self, point):
        self.window_points.append((point.x, point.y))
        return CHILD_HANDLE

    def GetAncestor(self, handle, flag):
        if flag != 2:
            raise AssertionError(f"Unexpected GetAncestor flag: {flag}")
        return self.root_handle

    def SendInput(self, count, inputs, size):
        self.send_calls.append((count, size))
        return self.send_result


@unittest.skipIf(native_uia is None, "Windows UI Automation DLLs are unavailable")
class ClickControlTests(unittest.TestCase):
    def click(self, fake_user):
        automation = native_uia.Automation.__new__(native_uia.Automation)
        with patch.object(native_uia, "user", fake_user):
            automation.click_control(FakeWindow(), FakeElement())

    def test_cursor_api_signatures_use_windows_bool_and_point(self):
        self.assertEqual(
            native_uia.user.SetCursorPos.argtypes,
            [native_uia.W.INT, native_uia.W.INT],
        )
        self.assertIs(native_uia.user.SetCursorPos.restype, native_uia.W.BOOL)
        self.assertEqual(
            native_uia.user.GetCursorPos.argtypes,
            [C.POINTER(native_uia.W.POINT)],
        )
        self.assertIs(native_uia.user.GetCursorPos.restype, native_uia.W.BOOL)

    def test_set_cursor_failure_blocks_click(self):
        user = FakeUser(set_cursor_result=False)
        with self.assertRaisesRegex(OSError, "Could not position cursor"):
            self.click(user)
        self.assertEqual(user.position_calls, [EXPECTED_POINT])
        self.assertEqual(user.cursor_reads, 0)
        self.assertEqual(user.send_calls, [])

    def test_cursor_read_failure_or_mismatch_blocks_click(self):
        cases = (
            (FakeUser(get_cursor_result=False), "Could not verify cursor position"),
            (FakeUser(cursor_offset=(1, 0)), "Cursor did not reach the target point"),
        )
        for user, message in cases:
            with self.subTest(message=message):
                with self.assertRaisesRegex(OSError, message):
                    self.click(user)
                self.assertEqual(user.send_calls, [])

    def test_foreground_drift_blocks_click(self):
        user = FakeUser(foreground=(WINDOW_HANDLE, OTHER_HANDLE))
        with self.assertRaisesRegex(OSError, "lost foreground"):
            self.click(user)
        self.assertEqual(user.send_calls, [])

    def test_success_sends_one_click(self):
        user = FakeUser()
        self.click(user)
        self.assertEqual(user.position_calls, [EXPECTED_POINT])
        self.assertEqual(user.window_points, [EXPECTED_POINT])
        self.assertEqual(len(user.send_calls), 1)
        self.assertEqual(user.send_calls[0][0], 2)

    def test_partial_sendinput_raises_without_retry(self):
        user = FakeUser(send_result=1)
        with self.assertRaisesRegex(OSError, "outcome unknown"):
            self.click(user)
        self.assertEqual(len(user.send_calls), 1)


if __name__ == "__main__":
    unittest.main()
