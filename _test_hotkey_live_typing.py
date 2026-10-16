"""Real Windows typing check with the live WH_KEYBOARD_LL hook installed.

Creates a classic Win32 EDIT control (same input path as Notepad), installs the
real PlaybackHotkeyManager hook, and verifies unmatched keys are delivered while
matched shortcuts are consumed only when scoped.
"""
from __future__ import annotations

import ctypes
import os
import sys
import time
from ctypes import wintypes

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

os.environ['HUGE_HOTKEY_ALLOW_INJECTED'] = '1'
os.environ.pop('HUGE_DISABLE_HOTKEYS', None)

if os.name != 'nt':
    print('SKIP - Windows only')
    raise SystemExit(0)

from engine.hotkeys import PlaybackHotkeyManager, default_shortcuts_dict  # noqa: E402

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

# Fix prototypes so 64-bit LPARAMs do not overflow.
LRESULT = ctypes.c_ssize_t
user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.DefWindowProcW.restype = LRESULT
# Leave SendMessageW untyped so string buffers work for WM_GETTEXT/WM_SETTEXT.

WS_OVERLAPPEDWINDOW = 0x00CF0000
WS_VISIBLE = 0x10000000
WS_CHILD = 0x40000000
WS_BORDER = 0x00800000
WS_EX_TOPMOST = 0x00000008
ES_LEFT = 0x0000
ES_AUTOHSCROLL = 0x0080
WM_DESTROY = 0x0002
WM_SETTEXT = 0x000C
WM_GETTEXT = 0x000D
WM_GETTEXTLENGTH = 0x000E
HWND_TOPMOST = -1
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_SHOWWINDOW = 0x0040
INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
VK_SPACE = 0x20
VK_SHIFT = 0x10

WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ('wVk', wintypes.WORD),
        ('wScan', wintypes.WORD),
        ('dwFlags', wintypes.DWORD),
        ('time', wintypes.DWORD),
        ('dwExtraInfo', ctypes.POINTER(ctypes.c_ulong)),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ('uMsg', wintypes.DWORD),
        ('wParamL', wintypes.WORD),
        ('wParamH', wintypes.WORD),
    ]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ('dx', wintypes.LONG),
        ('dy', wintypes.LONG),
        ('mouseData', wintypes.DWORD),
        ('dwFlags', wintypes.DWORD),
        ('time', wintypes.DWORD),
        ('dwExtraInfo', ctypes.POINTER(ctypes.c_ulong)),
    ]


class INPUT_UNION(ctypes.Union):
    _fields_ = [
        ('mi', MOUSEINPUT),
        ('ki', KEYBDINPUT),
        ('hi', HARDWAREINPUT),
    ]


class INPUT(ctypes.Structure):
    # x64 requires 8-byte alignment after type (DWORD + padding).
    _fields_ = [
        ('type', wintypes.DWORD),
        ('union', INPUT_UNION),
    ]
    if ctypes.sizeof(ctypes.c_void_p) == 8:
        _fields_ = [
            ('type', wintypes.DWORD),
            ('_pad', wintypes.DWORD),
            ('union', INPUT_UNION),
        ]


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ('style', wintypes.UINT),
        ('lpfnWndProc', WNDPROC),
        ('cbClsExtra', ctypes.c_int),
        ('cbWndExtra', ctypes.c_int),
        ('hInstance', wintypes.HINSTANCE),
        ('hIcon', wintypes.HICON),
        ('hCursor', wintypes.HANDLE),
        ('hbrBackground', wintypes.HBRUSH),
        ('lpszMenuName', wintypes.LPCWSTR),
        ('lpszClassName', wintypes.LPCWSTR),
    ]


def ok(label, cond, detail=''):
    print(('PASS' if cond else 'FAIL'), '-', label + (f' ({detail})' if detail else ''))
    return bool(cond)


def send_vk(vk, up=False):
    inp = INPUT()
    inp.type = INPUT_KEYBOARD
    inp.union.ki = KEYBDINPUT(
        wVk=int(vk), wScan=0,
        dwFlags=KEYEVENTF_KEYUP if up else 0,
        time=0, dwExtraInfo=None,
    )
    sent = user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))
    if not sent:
        raise OSError(
            f'SendInput failed err={ctypes.GetLastError()} sizeof={ctypes.sizeof(INPUT)}'
        )


def type_text(text: str):
    for ch in text:
        scan = int(user32.VkKeyScanW(ord(ch)))
        vk = scan & 0xFF
        need_shift = bool(scan & 0x100)
        if need_shift:
            send_vk(VK_SHIFT, up=False)
        send_vk(vk, up=False)
        send_vk(vk, up=True)
        if need_shift:
            send_vk(VK_SHIFT, up=True)
        time.sleep(0.03)


def get_edit_text(hwnd):
    length = int(user32.SendMessageW(hwnd, WM_GETTEXTLENGTH, 0, 0) or 0)
    buf = ctypes.create_unicode_buffer(max(0, length) + 4)
    user32.SendMessageW(hwnd, WM_GETTEXT, length + 1, buf)
    return buf.value


def main():
    if os.environ.get('HUGE_SKIP_LIVE_KEYBOARD') == '1':
        print('SKIP')
        return 0

    fired = []
    parent_box = {'hwnd': 0}

    @WNDPROC
    def wndproc(hwnd, msg, wparam, lparam):
        if msg == WM_DESTROY:
            user32.PostQuitMessage(0)
            return 0
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    hInstance = kernel32.GetModuleHandleW(None)
    class_name = 'HugeLiveHookEditHost'
    wc = WNDCLASSW()
    wc.lpfnWndProc = wndproc
    wc.hInstance = hInstance
    wc.hbrBackground = 6  # COLOR_WINDOW
    wc.lpszClassName = class_name
    atom = user32.RegisterClassW(ctypes.byref(wc))
    if not atom and ctypes.GetLastError() not in (0, 1410):  # 1410 = already registered
        print('FAIL - RegisterClassW', ctypes.GetLastError())
        return 1

    parent = user32.CreateWindowExW(
        WS_EX_TOPMOST, class_name, 'Huge Live Hook Typing',
        WS_OVERLAPPEDWINDOW | WS_VISIBLE,
        80, 80, 520, 180,
        None, None, hInstance, None,
    )
    if not parent:
        print('FAIL - CreateWindow parent', ctypes.GetLastError())
        return 1
    parent_box['hwnd'] = int(parent)

    edit = user32.CreateWindowExW(
        0, 'EDIT', '',
        WS_CHILD | WS_VISIBLE | WS_BORDER | ES_LEFT | ES_AUTOHSCROLL,
        16, 50, 470, 32,
        parent, 1001, hInstance, None,
    )
    if not edit:
        print('FAIL - CreateWindow edit', ctypes.GetLastError())
        return 1

    user32.SetWindowPos(parent, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW)
    user32.ShowWindow(parent, 5)
    user32.UpdateWindow(parent)

    mgr = PlaybackHotkeyManager(
        on_action=lambda action, scope='huge', mono=None: fired.append(action),
        get_huge_hwnds=lambda: [parent_box['hwnd']],
        is_text_entry_active=lambda: False,
        find_pot_hwnd=lambda: 0,
    )
    mgr.load_dict(default_shortcuts_dict())
    mgr._allow_injected = True
    mgr.start()
    time.sleep(0.35)

    # Pump a few messages so the window is fully alive.
    msg = wintypes.MSG()
    for _ in range(20):
        while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        time.sleep(0.01)

    def pump(n=25):
        for _ in range(n):
            while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
            time.sleep(0.01)

    def focus_edit():
        fg_thread = user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), None)
        cur = kernel32.GetCurrentThreadId()
        if fg_thread and fg_thread != cur:
            user32.AttachThreadInput(cur, fg_thread, True)
        user32.ShowWindow(parent, 5)
        user32.SetForegroundWindow(parent)
        user32.BringWindowToTop(parent)
        user32.SetActiveWindow(parent)
        user32.SetFocus(edit)
        pump(10)
        if fg_thread and fg_thread != cur:
            user32.AttachThreadInput(cur, fg_thread, False)

    focus_edit()

    # Out of scope first: prove unmatched keys flow with hook installed.
    mgr._cached_huge_roots = frozenset()
    mgr._cached_pot_root = 0

    passed = True
    # Baseline without relying on prior state: clear, focus, type.
    user32.SendMessageW(edit, WM_SETTEXT, 0, '')
    focus_edit()
    # Prove focus with a direct EM_REPLACESEL-style char via WM_CHAR (bypasses hook).
    for ch in 'hi':
        user32.SendMessageW(edit, 0x0102, ord(ch), 0)  # WM_CHAR
    pump(5)
    baseline = get_edit_text(edit)
    passed &= ok('edit accepts WM_CHAR', baseline == 'hi', repr(baseline))

    user32.SendMessageW(edit, WM_SETTEXT, 0, '')
    focus_edit()
    type_text('hello')
    pump(40)
    text1 = get_edit_text(edit)
    passed &= ok('letters with hook live (unscoped)', text1 == 'hello', repr(text1))

    focus_edit()
    type_text('123')
    pump(30)
    text2 = get_edit_text(edit)
    passed &= ok('digits with hook live', '123' in text2, repr(text2))

    # Scoped as Huge: Space should be eaten and fire play_pause
    mgr._cached_huge_roots = frozenset({int(parent), int(edit)})
    user32.SendMessageW(edit, WM_SETTEXT, 0, '')
    focus_edit()
    before = len(fired)
    send_vk(VK_SPACE, False)
    send_vk(VK_SPACE, True)
    pump(30)
    text3 = get_edit_text(edit)
    passed &= ok('Space not typed when Huge-scoped', text3 == '', repr(text3))
    passed &= ok('play_pause fired', 'play_pause' in fired[before:], fired)

    # text entry active → Space types, no new play_pause
    mgr._is_text_entry_active = lambda: True
    before2 = len(fired)
    user32.SendMessageW(edit, WM_SETTEXT, 0, '')
    focus_edit()
    send_vk(VK_SPACE, False)
    send_vk(VK_SPACE, True)
    pump(25)
    text4 = get_edit_text(edit)
    passed &= ok('Space types with text_entry_active', text4 == ' ', repr(text4))
    passed &= ok('no play_pause while typing', 'play_pause' not in fired[before2:], fired[before2:])

    # Letters again while hook still live (after focus proven)
    mgr._is_text_entry_active = lambda: False
    mgr._cached_huge_roots = frozenset()
    user32.SendMessageW(edit, WM_SETTEXT, 0, '')
    focus_edit()
    type_text('abc')
    pump(30)
    text4b = get_edit_text(edit)
    passed &= ok('letters again with hook live', text4b == 'abc', repr(text4b))

    # stop releases hook
    mgr.stop()
    time.sleep(0.25)
    user32.SendMessageW(edit, WM_SETTEXT, 0, '')
    focus_edit()
    type_text('bye')
    pump(25)
    text5 = get_edit_text(edit)
    passed &= ok('typing after stop()', text5 == 'bye', repr(text5))
    passed &= ok('hook cleared', mgr._hook is None)

    user32.DestroyWindow(parent)
    print('\n=== LIVE TYPING SUMMARY ===')
    print('ALL PASS' if passed else 'FAILED')
    return 0 if passed else 1


if __name__ == '__main__':
    raise SystemExit(main())
