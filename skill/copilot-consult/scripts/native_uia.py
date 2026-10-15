"""Small ctypes binding to the public Windows UI Automation COM ABI.

No PowerShell, Node, injected browser code, fixed coordinates or clipboard.
ABI reference: Microsoft's Windows SDK UIAutomationClient.h (see README).
Only interfaces used by this application are wrapped.
"""
import ctypes as C
from ctypes import wintypes as W
import sys
import uuid
import time

if sys.platform != "win32":
    raise ImportError("Native UI Automation requires Windows")

P = C.c_void_p
I = C.c_int32
H = C.c_int32
ole = C.WinDLL("ole32", use_last_error=True)
aut = C.WinDLL("oleaut32", use_last_error=True)
user = C.WinDLL("user32", use_last_error=True)


class GUID(C.Structure):
    _fields_ = [("data", C.c_ubyte * 16)]

    def __init__(self, value):
        super().__init__((C.c_ubyte * 16).from_buffer_copy(uuid.UUID(value).bytes_le))


class Record(C.Structure):
    _fields_ = [("record", P), ("info", P)]


class Data(C.Union):
    _fields_ = [("integer", I), ("boolean", C.c_int16), ("pointer", P),
                ("int64", C.c_int64), ("record", Record)]


class Variant(C.Structure):
    _fields_ = [("vt", C.c_ushort), ("r1", C.c_ushort), ("r2", C.c_ushort),
                ("r3", C.c_ushort), ("data", Data)]


class Keyboard(C.Structure):
    _fields_=[("vk",W.WORD),("scan",W.WORD),("flags",W.DWORD),("time",W.DWORD),("extra",C.c_size_t)]


class Mouse(C.Structure):
    _fields_=[("dx",W.LONG),("dy",W.LONG),("data",W.DWORD),("flags",W.DWORD),("time",W.DWORD),("extra",C.c_size_t)]


class InputUnion(C.Union):
    _fields_=[("keyboard",Keyboard),("mouse",Mouse)]


class Input(C.Structure):
    _anonymous_=("data",)
    _fields_=[("type",W.DWORD),("data",InputUnion)]


user.SendInput.argtypes=[W.UINT,C.POINTER(Input),I]
user.SendInput.restype=W.UINT
user.SetForegroundWindow.argtypes=[W.HWND]
user.SetForegroundWindow.restype=W.BOOL
user.GetForegroundWindow.restype=W.HWND
user.WindowFromPoint.argtypes=[W.POINT]
user.WindowFromPoint.restype=W.HWND
user.GetAncestor.argtypes=[W.HWND,W.UINT]
user.GetAncestor.restype=W.HWND
user.SetCursorPos.argtypes=[W.INT,W.INT]
user.SetCursorPos.restype=W.BOOL
user.GetCursorPos.argtypes=[C.POINTER(W.POINT)]
user.GetCursorPos.restype=W.BOOL


ole.CoInitializeEx.argtypes = [P, W.DWORD]
ole.CoInitializeEx.restype = H
ole.CoCreateInstance.argtypes = [C.POINTER(GUID), P, W.DWORD, C.POINTER(GUID), C.POINTER(P)]
ole.CoCreateInstance.restype = H
aut.VariantClear.argtypes = [C.POINTER(Variant)]
aut.SysFreeString.argtypes = [P]
aut.SysStringLen.argtypes = [P]
aut.SysStringLen.restype = W.UINT
aut.SysAllocString.argtypes = [W.LPCWSTR]
aut.SysAllocString.restype = P
aut.SafeArrayAccessData.argtypes=[P,C.POINTER(P)]
aut.SafeArrayAccessData.restype=H
aut.SafeArrayUnaccessData.argtypes=[P]
aut.SafeArrayGetDim.argtypes=[P]
aut.SafeArrayGetDim.restype=W.UINT
aut.SafeArrayGetLBound.argtypes=[P,W.UINT,C.POINTER(I)]
aut.SafeArrayGetUBound.argtypes=[P,W.UINT,C.POINTER(I)]


def check(hr):
    if hr < 0:
        raise OSError(f"UI Automation HRESULT 0x{hr & 0xffffffff:08X}")


class Com:
    def __init__(self, pointer):
        self.ptr = pointer if isinstance(pointer, P) else P(pointer)
        if not self.ptr:
            raise OSError("UI Automation returned a null interface")

    def call(self, slot, types=(), args=()):
        table = C.cast(self.ptr, C.POINTER(C.POINTER(P))).contents
        function = C.WINFUNCTYPE(H, P, *types)(table[slot])
        check(function(self.ptr, *args))

    def out(self, slot, types=(), args=(), kind=P):
        value = kind()
        self.call(slot, (*types, C.POINTER(kind)), (*args, C.byref(value)))
        return value

    def bstr(self, slot, types=(), args=()):
        value = self.out(slot, types, args)
        try:
            return C.wstring_at(value, aut.SysStringLen(value)) if value else ""
        finally:
            if value:
                aut.SysFreeString(value)

    def close(self):
        if self.ptr:
            table = C.cast(self.ptr, C.POINTER(C.POINTER(P))).contents
            C.WINFUNCTYPE(W.ULONG, P)(table[2])(self.ptr)
            self.ptr = P()

    def __del__(self):
        self.close()


TYPES = {50000:"Button",50002:"CheckBox",50003:"ComboBox",50004:"Edit",50005:"Hyperlink",
         50007:"ListItem",50008:"List",50009:"Menu",50010:"MenuBar",50011:"MenuItem",
         50013:"RadioButton",50018:"Tab",50019:"TabItem",50020:"Text",50021:"ToolBar",
         50025:"Custom",50026:"Group",50030:"Document",50032:"Window",50033:"Pane"}
PROPS = {"pid":30002,"type":30003,"name":30005,"enabled":30010,"id":30011,
         "class":30012,"offscreen":30022,"handle":30020}
PATTERNS = {
    "Invoke":(10000,"fb377fbe-8ea6-46d5-9c73-6499642d3059"),
    "Value":(10002,"a94cd8b1-0844-4cd6-9d2d-640537ab39e9"),
    "ExpandCollapse":(10005,"619be086-1f4e-4ee4-bafa-210128738730"),
    "Text":(10014,"32eba289-3583-42c9-9c59-3b6d9a1e9b6a"),
    "ScrollItem":(10017,"b488300f-d015-4f19-9c29-bb595e3645ef"),
}


class Element(Com):
    def bounds(self):
        value=Variant()
        self.call(10,(I,C.POINTER(Variant)),(30001,C.byref(value)))
        try:
            if value.vt!=0x2005 or aut.SafeArrayGetDim(value.data.pointer)!=1:
                raise OSError("No accessible bounding rectangle")
            low=I();high=I()
            check(aut.SafeArrayGetLBound(value.data.pointer,1,C.byref(low)))
            check(aut.SafeArrayGetUBound(value.data.pointer,1,C.byref(high)))
            if high.value-low.value!=3:
                raise OSError("Invalid bounding rectangle")
            data=P();check(aut.SafeArrayAccessData(value.data.pointer,C.byref(data)))
            try:
                return tuple(C.cast(data,C.POINTER(C.c_double))[i] for i in range(4))
            finally:
                aut.SafeArrayUnaccessData(value.data.pointer)
        finally:
            aut.VariantClear(C.byref(value))

    def prop(self, key):
        value = Variant()
        self.call(10, (I,C.POINTER(Variant)), (PROPS[key],C.byref(value)))
        try:
            if value.vt == 8:
                return C.wstring_at(value.data.pointer, aut.SysStringLen(value.data.pointer)) if value.data.pointer else ""
            if value.vt in (3,22):
                return value.data.integer
            if value.vt == 11:
                return bool(value.data.boolean)
            return None
        finally:
            aut.VariantClear(C.byref(value))

    def pattern(self, name):
        number, iid = PATTERNS[name]
        guid = GUID(iid)
        return Com(self.out(14, (I,C.POINTER(GUID)), (number,C.byref(guid))))

    def describe(self):
        data = {key:self.prop(key) for key in PROPS}
        data["type"] = TYPES.get(data["type"], str(data["type"]))
        return data

    def value(self):
        return self.pattern("Value").bstr(4)

    def text(self):
        pattern = self.pattern("Text")
        range_ = Com(pattern.out(7))
        return range_.bstr(12, (I,), (-1,))

    def range_text(self, child):
        pattern = self.pattern("Text")
        range_ = Com(pattern.out(4,(P,),(child.ptr,)))
        return range_.bstr(12,(I,),(-1,))

    def invoke(self):
        self.ready()
        self.pattern("Invoke").call(3)

    def expand(self):
        self.ready()
        self.pattern("ExpandCollapse").call(3)

    def collapse(self):
        self.pattern("ExpandCollapse").call(4)

    def scroll_into_view(self):
        self.pattern("ScrollItem").call(3)

    def ready(self):
        if not self.prop("enabled") or self.prop("offscreen"):
            raise OSError("Control disabled or offscreen")

    def set_value(self, text):
        self.ready()
        pattern = self.pattern("Value")
        if pattern.out(5, kind=I).value or pattern.bstr(4) not in ("",):
            raise OSError("Composer read-only or contains draft; preserved")
        if "\0" in text:
            raise ValueError("NUL is not permitted in UI text")
        value = aut.SysAllocString(text)
        if not value:
            raise MemoryError("Cannot allocate BSTR")
        try:
            pattern.call(3, (P,), (value,))
        finally:
            aut.SysFreeString(value)


class Automation:
    def __init__(self):
        user.SetThreadDpiAwarenessContext.argtypes=[P]
        user.SetThreadDpiAwarenessContext(P(-4))
        check(ole.CoInitializeEx(None, 0))  # MTA, isolated Python process/thread.
        pointer = P()
        clsid = GUID("ff48dba4-60ef-4201-aa87-54103eef594e")
        iid = GUID("30cbe57d-d9d0-452a-ab13-7ac5ac4825ee")
        check(ole.CoCreateInstance(C.byref(clsid),None,1,C.byref(iid),C.byref(pointer)))
        self.com = Com(pointer)
        self.condition = Com(self.com.out(21))  # CreateTrueCondition

    def root(self):
        return Element(self.com.out(5))

    def from_handle(self, handle):
        return Element(self.com.out(6, (P,), (P(handle),)))

    def all(self, element, scope=4):
        collection = Com(element.out(6,(I,P),(scope,self.condition.ptr)))
        length = collection.out(3, kind=I).value
        return [Element(collection.out(4,(I,),(i,))) for i in range(length)]

    def snapshot(self, element):
        for attempt in range(3):
            try:
                return [e.describe() for e in self.all(element)]
            except OSError as exc:
                if attempt == 2 or "0x80040201" not in str(exc):
                    raise
                time.sleep(.6)

    def exact(self, root, *, id=None, name=None, type=None, visible=True):
        # Read-only recovery: a WebView can invalidate elements during rendering.
        # No action is retried here or elsewhere.
        for attempt in range(3):
            try:
                return self._exact(root, id=id, name=name, type=type, visible=visible)
            except OSError as exc:
                if attempt == 2 or not ("0x80040201" in str(exc) or "found 0" in str(exc)):
                    raise
                time.sleep(.6)

    def _exact(self, root, *, id=None, name=None, type=None, visible=True):
        if not id and not name:
            raise ValueError("Exact id or name is required")
        matches=[]
        for e in self.all(root):
            if id is not None and e.prop("id") != id:
                continue
            if name is not None and e.prop("name") != name:
                continue
            if type is not None and TYPES.get(e.prop("type")) != type:
                continue
            if visible and e.prop("offscreen"):
                continue
            matches.append(e)
        if len(matches) != 1:
            raise OSError(f"Expected exactly one accessible control, found {len(matches)}")
        return matches[0]

    def copilot(self):
        windows = [e for e in self.all(self.root(),2) if e.prop("name") == "Microsoft 365 Copilot"
                   and e.prop("class") == "Microsoft 365 Copilot Host"]
        if len(windows) != 1:
            raise OSError(f"Expected one installed Microsoft 365 Copilot window, found {len(windows)}")
        return windows[0]

    def click_control(self, window, element):
        """Click one exact, fresh UIA control after verifying foreground and cursor."""
        element.ready()
        handle=window.prop("handle")
        user.SetForegroundWindow(handle)
        if user.GetForegroundWindow()!=handle:
            raise OSError("Copilot is not foreground; no click")
        x,y,width,height=element.bounds()
        if width<=0 or height<=0:
            raise OSError("Control has no visible bounds")
        point=W.POINT(round(x+width/2),round(y+height/2))
        if not user.SetCursorPos(point.x,point.y):
            raise OSError("Could not position cursor; no click")
        actual=W.POINT()
        if not user.GetCursorPos(C.byref(actual)):
            raise OSError("Could not verify cursor position; no click")
        if (actual.x,actual.y)!=(point.x,point.y):
            raise OSError("Cursor did not reach the target point; no click")
        if user.GetForegroundWindow()!=handle:
            raise OSError("Copilot lost foreground before click; no click")
        hit=user.WindowFromPoint(actual)
        if user.GetAncestor(hit,2)!=handle:
            raise OSError("Another window covers the target control; no click")
        clicks=(Input*2)(Input(0,InputUnion(mouse=Mouse(0,0,0,2,0,0))),Input(0,InputUnion(mouse=Mouse(0,0,0,4,0,0))))
        if user.SendInput(2,clicks,C.sizeof(Input))!=2:
            raise OSError("Click outcome unknown; reobserve without repeating")

    def clear_owned_test_draft(self, window, element, expected):
        """Test cleanup only: remove an exact known synthetic draft, never arbitrary text."""
        if not expected.startswith("[CODEX:") or element.value() not in (expected,expected+"\n"):
            raise OSError("Draft identity mismatch; preserve it")
        self.click_control(window,element)
        time.sleep(.15)
        focused=Element(self.com.out(8))
        if focused.prop("id")!="m365-chat-editor-target-element" or user.GetForegroundWindow()!=window.prop("handle"):
            raise OSError("Editor focus not confirmed")
        keys=[Keyboard(0x11,0,0,0,0),Keyboard(0x41,0,0,0,0),Keyboard(0x41,0,2,0,0),
              Keyboard(0x11,0,2,0,0),Keyboard(0x08,0,0,0,0),Keyboard(0x08,0,2,0,0)]
        array=(Input*len(keys))(*(Input(1,InputUnion(keyboard=k)) for k in keys))
        if user.SendInput(len(array),array,C.sizeof(Input))!=len(array):
            raise OSError("Cleanup outcome unknown; reobserve")

    def type_text(self, window, element, text):
        """Literal input to a verified editable control, never clipboard/paste.

        Newlines use Shift+Enter, never unmodified Enter (which can submit).
        Partial SendInput is uncertain and MUST NOT be repeated automatically.
        """
        element.ready()
        if element.prop("type") != 50004 or element.prop("id") != "m365-chat-editor-target-element":
            raise OSError("Input target is not the observed Copilot editor")
        if element.value() not in ("", "\n"):
            raise OSError("Existing editor draft preserved")
        if "\0" in text or len(text) > 24000:
            raise ValueError("Input contains NUL or exceeds the local 24000-character guard")
        text=text.replace("\r\n","\n").replace("\r","\n")
        handle=window.prop("handle")
        user.SetForegroundWindow(handle)
        element.call(3)  # SetFocus, then re-observe actual focus.
        # WebView's UIA SetFocus can report focus without creating a caret,
        # especially when an attachment chip is present. Use a single click
        # derived from this exact Edit's fresh UIA rectangle, never fixed pixels.
        self.click_control(window,element)
        time.sleep(.15)
        focused=Element(self.com.out(8))
        if user.GetForegroundWindow() != handle or focused.prop("id") != element.prop("id") or focused.prop("pid") != element.prop("pid"):
            raise OSError("Copilot editor focus could not be verified")
        inputs=[]
        def key(vk,scan=0,flags=0):
            inputs.append(Input(1,InputUnion(keyboard=Keyboard(vk,scan,flags,0,0))))
        for line_index,line in enumerate(text.split("\n")):
            if line_index:
                key(0x10);key(0x0d);key(0x0d,flags=2);key(0x10,flags=2)
            encoded=line.encode("utf-16-le")
            for offset in range(0,len(encoded),2):
                code=int.from_bytes(encoded[offset:offset+2],"little")
                key(0,code,4);key(0,code,6)
        if not inputs:
            raise ValueError("Empty text")
        array=(Input*len(inputs))(*inputs)
        focused=Element(self.com.out(8))
        if user.GetForegroundWindow() != handle or focused.prop("id") != element.prop("id") or focused.prop("pid") != element.prop("pid"):
            raise OSError("Focus changed before input; no text sent")
        sent=user.SendInput(len(array),array,C.sizeof(Input))
        if sent != len(array):
            raise OSError(f"Partial input ({sent}/{len(array)}); reobserve, do not type again")
