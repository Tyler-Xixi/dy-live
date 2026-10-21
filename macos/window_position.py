"""Place client dialogs centrally without changing task or network behavior."""
import os


def centered_position(size, anchor, bounds):
    width,height=size
    left,top,available_width,available_height=bounds
    x=anchor[0]+(anchor[2]-width)//2
    y=anchor[1]+(anchor[3]-height)//2
    return (max(left,min(x,left+max(0,available_width-width))),
            max(top,min(y,top+max(0,available_height-height))))


def center_window(window, parent=None):
    window.update_idletasks()
    parent=parent if parent is not None and parent.winfo_exists() and parent.winfo_viewable() else None
    if os.name=='nt':
        import ctypes
        from ctypes import wintypes as wt
        class MonitorInfo(ctypes.Structure):
            _fields_=[('cbSize',wt.DWORD),('monitor',wt.RECT),('work',wt.RECT),('flags',wt.DWORD)]
        api=ctypes.WinDLL('user32',use_last_error=True)
        api.GetAncestor.argtypes=[wt.HWND,wt.UINT]; api.GetAncestor.restype=wt.HWND
        api.GetWindowRect.argtypes=[wt.HWND,ctypes.POINTER(wt.RECT)]
        api.MonitorFromWindow.argtypes=[wt.HWND,wt.DWORD]; api.MonitorFromWindow.restype=wt.HANDLE
        api.GetMonitorInfoW.argtypes=[wt.HANDLE,ctypes.POINTER(MonitorInfo)]
        api.SetWindowPos.argtypes=[wt.HWND,wt.HWND,ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_int,wt.UINT]
        handle=api.GetAncestor(window.winfo_id(),2)
        owner=api.GetAncestor(parent.winfo_id(),2) if parent is not None else handle
        info=MonitorInfo(); info.cbSize=ctypes.sizeof(info)
        def rectangle(hwnd):
            rect=wt.RECT()
            if not api.GetWindowRect(hwnd,ctypes.byref(rect)): raise ctypes.WinError(ctypes.get_last_error())
            return rect.left,rect.top,rect.right-rect.left,rect.bottom-rect.top
        if api.GetMonitorInfoW(api.MonitorFromWindow(owner,2),ctypes.byref(info)):
            bounds=(info.work.left,info.work.top,info.work.right-info.work.left,info.work.bottom-info.work.top)
            rect=rectangle(handle)
            if rect[2]>bounds[2] or rect[3]>bounds[3]:
                width=max(1,window.winfo_width()-max(0,rect[2]-bounds[2]))
                height=max(1,window.winfo_height()-max(0,rect[3]-bounds[3]))
                minimum=window.minsize(); window.minsize(min(minimum[0],width),min(minimum[1],height))
                window.geometry(f'{width}x{height}'); window.update_idletasks(); rect=rectangle(handle)
            anchor=rectangle(owner) if parent is not None else bounds
            x,y=centered_position(rect[2:],anchor,bounds)
            if api.SetWindowPos(handle,None,x,y,0,0,0x15): return
    bounds=(window.winfo_vrootx(),window.winfo_vrooty(),window.winfo_vrootwidth(),window.winfo_vrootheight())
    anchor=(parent.winfo_rootx(),parent.winfo_rooty(),parent.winfo_width(),parent.winfo_height()) if parent is not None else bounds
    x,y=centered_position((window.winfo_width(),window.winfo_height()),anchor,bounds)
    window.geometry(f'{x:+d}{y:+d}')
