"""Owned static analyzer processes, bounded output and cancellation cleanup."""

import ctypes
import os
import signal
import subprocess
import tempfile
import time
from pathlib import Path

from .domain import Fault


class WindowsJob:
    """Assign a suspended process before it can create descendants."""

    def __init__(self, process):
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel.CreateJobObjectW.restype = wintypes.HANDLE
        kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        kernel.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        kernel.QueryInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                                    wintypes.DWORD, ctypes.c_void_p]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel, self.handle = kernel, kernel.CreateJobObjectW(None, None)
        if not self.handle:
            raise Fault("REA_SUPERVISION", "Cannot create analyzer process job")
        # JOBOBJECT_EXTENDED_LIMIT_INFORMATION: explicit ABI layout on 64-bit Windows.
        class Basic(ctypes.Structure):
            _fields_ = [("process_time", ctypes.c_int64), ("job_time", ctypes.c_int64),
                        ("flags", wintypes.DWORD), ("min_ws", ctypes.c_size_t), ("max_ws", ctypes.c_size_t),
                        ("active", wintypes.DWORD), ("affinity", ctypes.c_size_t),
                        ("priority", wintypes.DWORD), ("scheduling", wintypes.DWORD)]

        class Extended(ctypes.Structure):
            _fields_ = [("basic", Basic), ("io", ctypes.c_uint64 * 6),
                        ("process_memory", ctypes.c_size_t), ("job_memory", ctypes.c_size_t),
                        ("peak_process", ctypes.c_size_t), ("peak_job", ctypes.c_size_t)]

        info = Extended()
        info.basic.flags = 0x2000 | 0x200 | 0x8  # kill-on-close, job-memory, active-process limits
        info.basic.active, info.job_memory = 8, 1024 * 1024 * 1024
        try:
            if not kernel.SetInformationJobObject(self.handle, 9, ctypes.byref(info), ctypes.sizeof(info)):
                raise Fault("REA_SUPERVISION", "Cannot bound analyzer process family")
            if not kernel.AssignProcessToJobObject(self.handle, int(process._handle)):
                raise Fault("REA_SUPERVISION", "Cannot establish analyzer process ownership")
            resume = ctypes.WinDLL("ntdll").NtResumeProcess
            resume.argtypes, resume.restype = [wintypes.HANDLE], wintypes.LONG
            if resume(int(process._handle)) != 0:
                raise Fault("REA_SUPERVISION", "Cannot resume owned analyzer")
        except BaseException:
            self.close()
            raise

    def close(self):
        if self.handle:
            from ctypes import wintypes

            class Accounting(ctypes.Structure):
                _fields_ = [("times", ctypes.c_int64 * 4), ("faults", wintypes.DWORD),
                            ("total", wintypes.DWORD), ("active", wintypes.DWORD), ("terminated", wintypes.DWORD)]

            try:
                if not self.kernel.TerminateJobObject(self.handle, 1):
                    raise Fault("REA_SUPERVISION", "Cannot terminate owned analyzer family")
                deadline = time.monotonic() + 5
                info = Accounting()
                while True:
                    if not self.kernel.QueryInformationJobObject(self.handle, 1, ctypes.byref(info), ctypes.sizeof(info), None):
                        raise Fault("REA_SUPERVISION", "Cannot confirm analyzer family exit")
                    if info.active == 0:
                        break
                    if time.monotonic() >= deadline:
                        raise Fault("REA_SUPERVISION", "Analyzer family did not exit before cleanup deadline")
                    time.sleep(.01)
            finally:
                self.kernel.CloseHandle(self.handle)
                self.handle = None


def run(argv, directory: Path, timeout, maximum, cancelled, env):
    """No shell or ambient credentials; caller supplies the reviewed fixed argv."""
    job, process = None, None
    started = time.monotonic()
    with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
        try:
            if cancelled.is_set():
                raise Fault("REA_CANCELLED", "Analyzer cancelled before process creation")
            process = subprocess.Popen(argv, cwd=directory, env=env, stdin=subprocess.DEVNULL,
                stdout=output, stderr=errors, start_new_session=os.name != "nt",
                creationflags=0x4 | 0x08000000 if os.name == "nt" else 0)
            if os.name == "nt":
                job = WindowsJob(process)
            while process.poll() is None:
                if cancelled.is_set():
                    raise Fault("REA_CANCELLED", "Analyzer cancelled at an owned process boundary")
                if time.monotonic() - started >= timeout:
                    raise Fault("REA_TIMEOUT", "Static analysis deadline exceeded; runtime behavior remains unknown")
                if os.fstat(output.fileno()).st_size > maximum or os.fstat(errors.fileno()).st_size > 1024 * 1024:
                    raise Fault("REA_OUTPUT_LIMIT", "Analyzer output exceeded the evidence budget")
                cancelled.wait(0.05)
            output.seek(0)
            data = output.read(maximum + 1)
            if len(data) > maximum:
                raise Fault("REA_OUTPUT_LIMIT", "Complete Evidence exceeds the bounded object channel")
            if process.returncode:
                # Do not turn stderr, paths or target text into trusted instructions.
                raise Fault("REA_FAILED", "REA CLI failed; target behavior remains unknown")
            return data
        finally:
            if job:
                job.close()  # kills all remaining descendants even if the leader exited
            elif process and os.name != "nt":
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            if process and process.poll() is None:
                process.kill()
            if process:
                process.wait()
