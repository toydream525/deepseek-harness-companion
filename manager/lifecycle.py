"""Small Windows Job Object wrapper for the manager window's model ownership."""

import ctypes
import calendar
import datetime
from ctypes import wintypes


JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
JOB_OBJECT_ASSIGN_PROCESS = 0x0001
JOB_OBJECT_QUERY = 0x0004
PROCESS_TERMINATE = 0x0001
PROCESS_SET_QUOTA = 0x0100
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
SYNCHRONIZE = 0x00100000
TH32CS_SNAPTHREAD = 0x00000004
THREAD_SUSPEND_RESUME = 0x0002
CREATE_SUSPENDED = 0x00000004


class LARGE_INTEGER(ctypes.Structure):
    _fields_ = [("QuadPart", ctypes.c_longlong)]


class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", LARGE_INTEGER),
        ("PerJobUserTimeLimit", LARGE_INTEGER),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class IO_COUNTERS(ctypes.Structure):
    _fields_ = [(name, ctypes.c_ulonglong) for name in (
        "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
        "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]


class JOBOBJECT_EXTENDED_LIMITS(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
        ("IoInfo", IO_COUNTERS),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class THREADENTRY32(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
        ("th32ThreadID", wintypes.DWORD), ("th32OwnerProcessID", wintypes.DWORD),
        ("tpBasePri", wintypes.LONG), ("tpDeltaPri", wintypes.LONG),
        ("dwFlags", wintypes.DWORD),
    ]


class FILETIME(ctypes.Structure):
    _fields_ = [("dwLowDateTime", wintypes.DWORD), ("dwHighDateTime", wintypes.DWORD)]

    @property
    def ticks(self) -> int:
        return (self.dwHighDateTime << 32) | self.dwLowDateTime


kernel = ctypes.WinDLL("kernel32", use_last_error=True)
kernel.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
kernel.CreateJobObjectW.restype = wintypes.HANDLE
kernel.OpenJobObjectW.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR)
kernel.OpenJobObjectW.restype = wintypes.HANDLE
kernel.SetInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD)
kernel.SetInformationJobObject.restype = wintypes.BOOL
kernel.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
kernel.AssignProcessToJobObject.restype = wintypes.BOOL
kernel.IsProcessInJob.argtypes = (wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL))
kernel.IsProcessInJob.restype = wintypes.BOOL
kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
kernel.OpenProcess.restype = wintypes.HANDLE
kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
kernel.CloseHandle.restype = wintypes.BOOL
kernel.CreateToolhelp32Snapshot.argtypes = (wintypes.DWORD, wintypes.DWORD)
kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
kernel.Thread32First.argtypes = (wintypes.HANDLE, ctypes.POINTER(THREADENTRY32))
kernel.Thread32First.restype = wintypes.BOOL
kernel.Thread32Next.argtypes = (wintypes.HANDLE, ctypes.POINTER(THREADENTRY32))
kernel.Thread32Next.restype = wintypes.BOOL
kernel.OpenThread.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
kernel.OpenThread.restype = wintypes.HANDLE
kernel.ResumeThread.argtypes = (wintypes.HANDLE,)
kernel.ResumeThread.restype = wintypes.DWORD
kernel.GetProcessTimes.argtypes = (wintypes.HANDLE, ctypes.POINTER(FILETIME), ctypes.POINTER(FILETIME),
                                   ctypes.POINTER(FILETIME), ctypes.POINTER(FILETIME))
kernel.GetProcessTimes.restype = wintypes.BOOL
kernel.TerminateProcess.argtypes = (wintypes.HANDLE, wintypes.UINT)
kernel.TerminateProcess.restype = wintypes.BOOL
kernel.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
kernel.WaitForSingleObject.restype = wintypes.DWORD


def _raise_windows(message: str) -> None:
    raise OSError(ctypes.get_last_error(), message)


def create_job(name: str) -> int:
    ctypes.set_last_error(0)
    handle = kernel.CreateJobObjectW(None, name)
    if not handle:
        _raise_windows("无法创建模型作业对象")
    if ctypes.get_last_error() == 183:
        kernel.CloseHandle(handle)
        raise RuntimeError("已有管理窗口持有模型作业对象")
    limits = JOBOBJECT_EXTENDED_LIMITS()
    limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not kernel.SetInformationJobObject(handle, JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
                                          ctypes.byref(limits), ctypes.sizeof(limits)):
        kernel.CloseHandle(handle)
        _raise_windows("无法设置模型异常退出回收")
    return int(handle)


def open_job(name: str) -> int:
    handle = kernel.OpenJobObjectW(JOB_OBJECT_ASSIGN_PROCESS | JOB_OBJECT_QUERY, False, name)
    if not handle:
        _raise_windows("无法打开管理窗口的模型作业对象")
    return int(handle)


def close_handle(handle: int) -> None:
    if handle:
        kernel.CloseHandle(handle)


def assign_process(job_handle: int, pid: int) -> None:
    process = kernel.OpenProcess(PROCESS_TERMINATE | PROCESS_SET_QUOTA, False, pid)
    if not process:
        _raise_windows("无法打开模型进程以设置归属")
    try:
        if not kernel.AssignProcessToJobObject(job_handle, process):
            _raise_windows("无法将模型进程加入管理窗口作业对象")
    finally:
        kernel.CloseHandle(process)


def is_process_in_job(job_handle: int, pid: int) -> bool:
    process = kernel.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not process:
        return False
    try:
        result = wintypes.BOOL()
        if not kernel.IsProcessInJob(process, job_handle, ctypes.byref(result)):
            return False
        return bool(result.value)
    finally:
        kernel.CloseHandle(process)


def resume_process(pid: int) -> None:
    snapshot = kernel.CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0)
    if snapshot == ctypes.c_void_p(-1).value:
        _raise_windows("无法枚举挂起的模型线程")
    resumed = 0
    try:
        entry = THREADENTRY32()
        entry.dwSize = ctypes.sizeof(entry)
        found = kernel.Thread32First(snapshot, ctypes.byref(entry))
        while found:
            if entry.th32OwnerProcessID == pid:
                thread = kernel.OpenThread(THREAD_SUSPEND_RESUME, False, entry.th32ThreadID)
                if thread:
                    try:
                        if kernel.ResumeThread(thread) != 0xFFFFFFFF:
                            resumed += 1
                    finally:
                        kernel.CloseHandle(thread)
            found = kernel.Thread32Next(snapshot, ctypes.byref(entry))
    finally:
        kernel.CloseHandle(snapshot)
    if not resumed:
        raise RuntimeError("无法恢复挂起的模型进程")


def _iso_creation_ticks(value: str) -> int:
    date_part, time_part = value.rstrip("Z").split("T", 1)
    whole, _, fraction = time_part.partition(".")
    moment = datetime.datetime.strptime(date_part + "T" + whole, "%Y-%m-%dT%H:%M:%S")
    seconds = calendar.timegm(moment.timetuple())
    return 116444736000000000 + seconds * 10_000_000 + int((fraction + "0000000")[:7])


def terminate_exact_process(pid: int, created_at: str, timeout_ms: int = 15000) -> bool:
    """Bind identity to one kernel handle before ending it; never act on reused PID."""
    process = kernel.OpenProcess(PROCESS_TERMINATE | PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE,
                                 False, pid)
    if not process:
        return False
    try:
        created, exited, kernel_time, user_time = FILETIME(), FILETIME(), FILETIME(), FILETIME()
        if not kernel.GetProcessTimes(process, ctypes.byref(created), ctypes.byref(exited),
                                      ctypes.byref(kernel_time), ctypes.byref(user_time)):
            return False
        # CIM creation timestamps are truncated to microseconds on this host.
        if created.ticks // 10 != _iso_creation_ticks(created_at) // 10:
            return False
        if not kernel.TerminateProcess(process, 1):
            return kernel.WaitForSingleObject(process, 0) == 0
        return kernel.WaitForSingleObject(process, timeout_ms) == 0
    finally:
        kernel.CloseHandle(process)
