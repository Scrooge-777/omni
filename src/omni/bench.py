"""Benchmarks and stress tests.

Upgrades over ubat 1.x:
- CPU multi-core runs in separate processes (Python threads share one GIL, so the old
  "multi-thread" score was lower than single-thread);
- memory uses native memcpy/memset instead of Python loops;
- disk uses unbuffered, write-through I/O so the Windows file cache can't inflate results;
- GPU reports real FP32 throughput (TFLOPS), checks every CUDA call, and releases the GPU;
- stress tests detect throttling from measured clocks and NVML reasons, not guesses.
"""

import ctypes
import math
import multiprocessing as mp
import os
import random
import tempfile
import threading
import time
from ctypes import byref, c_int, c_uint, c_ulonglong, c_void_p, wintypes

import psutil

from omni import win


class BenchError(Exception):
    pass


def _noop(*_args, **_kwargs):
    pass


# --------------------------------------------------------------------------- CPU

def _cpu_chunk(n: int) -> float:
    """A fixed mixed integer/float workload. Returns a value so the work can't be skipped."""
    acc = 0.0
    x = 1
    for i in range(n):
        x = (x * 1103515245 + 12345) & 0x7FFFFFFF
        acc += math.sqrt(x) * 1e-6
    return acc


CHUNK = 20_000


def _cpu_worker(barrier, seconds: float, out, index: int, stop=None):
    """Wait until every worker has started, then count work chunks for `seconds`.

    The barrier matters: spawning 24 Python processes takes seconds, and a worker that
    starts late would otherwise run for less time and drag the score down.
    """
    if barrier is not None:
        barrier.wait()
    started = time.perf_counter()
    chunks = 0
    while time.perf_counter() - started < seconds and not (stop is not None and stop.is_set()):
        _cpu_chunk(CHUNK)
        chunks += 1
    if out is not None:
        out[index] = chunks / (time.perf_counter() - started)


def cpu_benchmark(seconds: float = 5.0, progress=_noop, stop: threading.Event | None = None) -> dict:
    progress(0.0, "Single-core test")
    started = time.perf_counter()
    chunks = 0
    while time.perf_counter() - started < seconds and not (stop and stop.is_set()):
        _cpu_chunk(CHUNK)
        chunks += 1
    single = chunks / (time.perf_counter() - started)

    threads = psutil.cpu_count(logical=True) or 1
    progress(0.5, f"Multi-core test ({threads} processes)")
    ctx = mp.get_context("spawn")
    rates = ctx.Array("d", threads)
    barrier = ctx.Barrier(threads)
    procs = [ctx.Process(target=_cpu_worker, args=(barrier, seconds, rates, i, None), daemon=True)
             for i in range(threads)]
    for proc in procs:
        proc.start()
    for proc in procs:
        proc.join(seconds + 60)
    multi = sum(rates)  # chunks per second, summed over all workers
    progress(1.0, "Done")

    # Score = thousands of workload iterations per second.
    return {
        "single_score": round(single * CHUNK / 1000),
        "multi_score": round(multi * CHUNK / 1000),
        "scaling": round(multi / single, 1) if single else None,
        "threads": threads,
        "cores": psutil.cpu_count(logical=False),
        "seconds": seconds,
        "unit": "k iterations/s",
    }


# --------------------------------------------------------------------------- memory

def memory_benchmark(size_mb: int = 512, rounds: int = 8, progress=_noop, stop=None) -> dict:
    size = size_mb * 1024 * 1024
    src = (ctypes.c_char * size)()
    dst = (ctypes.c_char * size)()
    ctypes.memset(src, 1, size)
    ctypes.memset(dst, 0, size)  # touch every page first so page faults aren't measured

    best_copy = best_write = 0.0
    for i in range(rounds):
        if stop and stop.is_set():
            break
        progress(i / rounds, "Measuring memory bandwidth")
        t = time.perf_counter()
        ctypes.memmove(dst, src, size)
        best_copy = max(best_copy, 2 * size / (time.perf_counter() - t))  # read + write
        t = time.perf_counter()
        ctypes.memset(dst, i & 0xFF, size)
        best_write = max(best_write, size / (time.perf_counter() - t))
    progress(1.0, "Done")
    return {"copy_bps": round(best_copy), "write_bps": round(best_write), "buffer_bytes": size,
            "note": "Copy counts bytes read + written (STREAM convention). Best of rounds."}


# --------------------------------------------------------------------------- disk

GENERIC_READ, GENERIC_WRITE = 0x80000000, 0x40000000
CREATE_ALWAYS, OPEN_EXISTING = 2, 3
FILE_FLAG_NO_BUFFERING, FILE_FLAG_WRITE_THROUGH = 0x20000000, 0x80000000
MEM_COMMIT_RESERVE, MEM_RELEASE, PAGE_READWRITE = 0x3000, 0x8000, 0x04
INVALID_HANDLE = c_void_p(-1).value


class _Win32File:
    """Unbuffered file I/O. Buffers come from VirtualAlloc so they are sector-aligned."""

    def __init__(self, path: str, write: bool):
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateFileW.restype = wintypes.HANDLE
        k32.VirtualAlloc.restype = c_void_p
        k32.VirtualAlloc.argtypes = [c_void_p, ctypes.c_size_t, wintypes.DWORD, wintypes.DWORD]
        k32.VirtualFree.argtypes = [c_void_p, ctypes.c_size_t, wintypes.DWORD]
        k32.ReadFile.argtypes = [wintypes.HANDLE, c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), c_void_p]
        k32.WriteFile.argtypes = k32.ReadFile.argtypes
        k32.SetFilePointerEx.argtypes = [wintypes.HANDLE, ctypes.c_longlong, c_void_p, wintypes.DWORD]
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        self.k32 = k32
        flags = FILE_FLAG_NO_BUFFERING | (FILE_FLAG_WRITE_THROUGH if write else 0)
        self.handle = k32.CreateFileW(path, GENERIC_WRITE if write else GENERIC_READ, 0, None,
                                      CREATE_ALWAYS if write else OPEN_EXISTING, flags, None)
        if self.handle in (None, INVALID_HANDLE):
            raise BenchError(f"Could not open test file (error {ctypes.get_last_error()})")

    def alloc(self, size: int) -> int:
        buf = self.k32.VirtualAlloc(None, size, MEM_COMMIT_RESERVE, PAGE_READWRITE)
        if not buf:
            raise BenchError("Could not allocate an aligned buffer")
        return buf

    def free(self, buf: int):
        self.k32.VirtualFree(buf, 0, MEM_RELEASE)

    def seek(self, offset: int):
        if not self.k32.SetFilePointerEx(self.handle, offset, None, 0):
            raise BenchError("Seek failed")

    def io(self, buf: int, size: int, write: bool) -> int:
        done = wintypes.DWORD()
        func = self.k32.WriteFile if write else self.k32.ReadFile
        if not func(self.handle, buf, size, byref(done), None):
            raise BenchError(f"{'Write' if write else 'Read'} failed (error {ctypes.get_last_error()})")
        return done.value

    def close(self):
        self.k32.CloseHandle(self.handle)


def disk_benchmark(folder: str | None = None, size_mb: int = 1024, random_seconds: float = 5.0,
                   progress=_noop, stop=None) -> dict:
    if not win.IS_WINDOWS:
        raise BenchError("The disk benchmark uses Windows unbuffered I/O")
    folder = folder or tempfile.gettempdir()
    free = psutil.disk_usage(folder).free
    if free < size_mb * 1024 * 1024 * 2:
        raise BenchError(f"Not enough free space in {folder} for a {size_mb} MB test file")
    path = os.path.join(folder, f"omni-disk-test-{os.getpid()}.bin")
    block = 1024 * 1024
    blocks = size_mb
    result = {"folder": folder, "file_mb": size_mb}
    try:
        f = _Win32File(path, write=True)
        buf = f.alloc(block)
        try:
            ctypes.memmove(buf, os.urandom(block), block)  # random data: no compression shortcuts
            t = time.perf_counter()
            for i in range(blocks):
                if stop and stop.is_set():
                    raise BenchError("Stopped")
                f.io(buf, block, write=True)
                if i % 64 == 0:
                    progress(0.4 * i / blocks, "Sequential write (1 MiB blocks)")
            result["seq_write_bps"] = round(blocks * block / (time.perf_counter() - t))
        finally:
            f.free(buf)
            f.close()

        f = _Win32File(path, write=False)
        buf = f.alloc(block)
        try:
            t = time.perf_counter()
            for i in range(blocks):
                if stop and stop.is_set():
                    raise BenchError("Stopped")
                f.io(buf, block, write=False)
                if i % 64 == 0:
                    progress(0.4 + 0.3 * i / blocks, "Sequential read (1 MiB blocks)")
            result["seq_read_bps"] = round(blocks * block / (time.perf_counter() - t))

            sector = 4096
            slots = blocks * block // sector
            ops = 0
            latencies = []
            t = time.perf_counter()
            while time.perf_counter() - t < random_seconds and not (stop and stop.is_set()):
                f.seek(random.randrange(slots) * sector)
                op = time.perf_counter()
                f.io(buf, sector, write=False)
                latencies.append(time.perf_counter() - op)
                ops += 1
                if ops % 2000 == 0:
                    progress(0.7 + 0.3 * (time.perf_counter() - t) / random_seconds, "Random 4 KiB reads")
            elapsed = time.perf_counter() - t
            latencies.sort()
            result.update({
                "rand4k_iops": round(ops / elapsed),
                "rand4k_bps": round(ops * sector / elapsed),
                "rand4k_latency_ms_avg": round(sum(latencies) / len(latencies) * 1000, 3) if latencies else None,
                "rand4k_latency_ms_p99": round(latencies[int(len(latencies) * 0.99)] * 1000, 3) if latencies else None,
                "queue_depth": 1,
            })
        finally:
            f.free(buf)
            f.close()
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
    progress(1.0, "Done")
    return result


# --------------------------------------------------------------------------- GPU (CUDA driver API)

PTX_FMA = b"""
.version 7.0
.target sm_50
.address_size 64

.visible .entry fma_kernel(.param .u32 iterations, .param .u64 d_out) {
    .reg .u32 %r<4>;
    .reg .f32 %f<8>;
    .reg .pred %p<2>;
    .reg .u64 %rd<3>;

    ld.param.u32 %r0, [iterations];
    ld.param.u64 %rd0, [d_out];
    mov.u32 %r1, 0;
    mov.f32 %f0, 1.0001;
    mov.f32 %f1, 0.9999;
    mov.f32 %f2, 1.0002;
    mov.f32 %f3, 0.9998;
    mov.f32 %f4, 0.5;
LOOP:
    setp.ge.u32 %p0, %r1, %r0;
    @%p0 bra DONE;
    fma.rn.f32 %f0, %f0, %f4, %f1;
    fma.rn.f32 %f1, %f1, %f4, %f2;
    fma.rn.f32 %f2, %f2, %f4, %f3;
    fma.rn.f32 %f3, %f3, %f4, %f0;
    fma.rn.f32 %f0, %f0, %f4, %f1;
    fma.rn.f32 %f1, %f1, %f4, %f2;
    fma.rn.f32 %f2, %f2, %f4, %f3;
    fma.rn.f32 %f3, %f3, %f4, %f0;
    add.u32 %r1, %r1, 1;
    bra LOOP;
DONE:
    add.f32 %f5, %f0, %f1;
    add.f32 %f6, %f2, %f3;
    add.f32 %f7, %f5, %f6;
    cvta.to.global.u64 %rd1, %rd0;
    st.global.f32 [%rd1], %f7;
    ret;
}
"""
FMA_PER_ITERATION = 8
GRID, BLOCK = 4096, 256


class CudaFma:
    """Minimal CUDA driver-API runner for the FMA kernel. Use as a context manager."""

    def __init__(self, iterations: int = 20_000):
        try:
            self.cu = ctypes.WinDLL("nvcuda.dll")
        except (OSError, AttributeError):
            raise BenchError("NVIDIA CUDA driver (nvcuda.dll) not found") from None
        self.iterations = iterations
        self.dev = c_int()
        self.ctx = c_void_p()
        self.module = c_void_p()
        self.func = c_void_p()
        self.mem = c_ulonglong()

    def _check(self, status, what):
        if status != 0:
            raise BenchError(f"CUDA {what} failed (error {status})")

    def __enter__(self):
        cu = self.cu
        self._check(cu.cuInit(0), "init")
        self._check(cu.cuDeviceGet(byref(self.dev), 0), "device lookup")
        name = (ctypes.c_char * 100)()
        cu.cuDeviceGetName(name, 100, self.dev)
        self.name = name.value.decode(errors="replace")
        self._check(cu.cuDevicePrimaryCtxRetain(byref(self.ctx), self.dev), "context")
        self._check(cu.cuCtxSetCurrent(self.ctx), "set context")
        self._check(cu.cuModuleLoadData(byref(self.module), PTX_FMA), "kernel load (PTX JIT)")
        self._check(cu.cuModuleGetFunction(byref(self.func), self.module, b"fma_kernel"), "kernel lookup")
        self._check(cu.cuMemAlloc_v2(byref(self.mem), 64), "memory allocation")
        self._iters = c_uint(self.iterations)
        self._args = (c_void_p * 2)(ctypes.cast(byref(self._iters), c_void_p), ctypes.cast(byref(self.mem), c_void_p))
        return self

    def bind_thread(self):
        self._check(self.cu.cuCtxSetCurrent(self.ctx), "set context")

    def launch(self):
        self._check(self.cu.cuLaunchKernel(self.func, GRID, 1, 1, BLOCK, 1, 1, 0, None, self._args, None), "launch")
        self._check(self.cu.cuCtxSynchronize(), "synchronise")

    @property
    def flops_per_launch(self) -> int:
        return GRID * BLOCK * self.iterations * FMA_PER_ITERATION * 2

    def __exit__(self, *exc):
        cu = self.cu
        if self.mem.value:
            cu.cuMemFree_v2(self.mem)
        if self.module.value:
            cu.cuModuleUnload(self.module)
        if self.ctx.value:
            cu.cuDevicePrimaryCtxRelease_v2(self.dev)
        return False


def gpu_benchmark(seconds: float = 6.0, progress=_noop, stop=None) -> dict:
    with CudaFma() as cuda:
        cuda.launch()  # warm-up: JIT compile and clock ramp
        launches = 0
        t = time.perf_counter()
        while time.perf_counter() - t < seconds and not (stop and stop.is_set()):
            cuda.launch()
            launches += 1
            progress((time.perf_counter() - t) / seconds, "FP32 FMA throughput")
        elapsed = time.perf_counter() - t
        progress(1.0, "Done")
        return {"device": cuda.name, "fp32_tflops": round(launches * cuda.flops_per_launch / elapsed / 1e12, 2),
                "launches": launches, "seconds": round(elapsed, 1)}


# --------------------------------------------------------------------------- stress tests

def _cpu_stress_procs(stop_event):
    ctx = mp.get_context("spawn")
    threads = psutil.cpu_count(logical=True) or 1
    procs = [ctx.Process(target=_cpu_worker, args=(None, 10**9, None, i, stop_event), daemon=True)
             for i in range(threads)]
    for proc in procs:
        proc.start()
    return procs


def stress_test(target: str, seconds: float, telemetry, progress=_noop, stop: threading.Event | None = None) -> dict:
    """Load the CPU, GPU or both while sampling telemetry() (the collector's latest snapshot) every second."""
    if target not in ("cpu", "gpu", "both"):
        raise BenchError("target must be cpu, gpu or both")
    stop = stop or threading.Event()  # set by the caller to end early; never set here
    done = threading.Event()  # internal: tells the GPU thread to finish
    ctx = mp.get_context("spawn")
    proc_stop = ctx.Event()
    procs, gpu_thread, gpu_error = [], None, []
    cuda = None

    if target in ("gpu", "both"):
        cuda = CudaFma(iterations=20_000).__enter__()

        def gpu_loop():
            try:
                cuda.bind_thread()
                while not done.is_set():
                    cuda.launch()
            except BenchError as exc:
                gpu_error.append(str(exc))

        gpu_thread = threading.Thread(target=gpu_loop, daemon=True)
        gpu_thread.start()
    if target in ("cpu", "both"):
        procs = _cpu_stress_procs(proc_stop)

    samples = []
    started = time.time()
    try:
        while not stop.is_set() and time.time() - started < seconds:
            time.sleep(1.0)
            snap = telemetry() or {}
            cpu, gpu = snap.get("cpu", {}), snap.get("gpu", {})
            samples.append({
                "t": round(time.time() - started, 1),
                "cpu_temp_c": cpu.get("temp_c"), "cpu_mhz": cpu.get("clock_mhz"), "cpu_pct": cpu.get("usage_pct"),
                "gpu_temp_c": gpu.get("temp_c"), "gpu_mhz": gpu.get("clock_mhz"), "gpu_w": gpu.get("power_w"),
                "gpu_pct": gpu.get("util_pct"), "gpu_limits": gpu.get("limits", []),
            })
            progress(min(1.0, (time.time() - started) / seconds), f"Stressing {target.upper()}")
            if gpu_error:
                break
    finally:
        done.set()
        proc_stop.set()
        for proc in procs:
            proc.join(5)
            if proc.is_alive():
                proc.terminate()
        if gpu_thread:
            gpu_thread.join(10)
        if cuda:
            cuda.__exit__(None, None, None)
    if gpu_error:
        raise BenchError(gpu_error[0])
    return summarize_stress(target, samples)


def summarize_stress(target: str, samples: list[dict]) -> dict:
    def series(key):
        return [s[key] for s in samples if s.get(key) is not None]

    def peak(key):
        values = series(key)
        return max(values) if values else None

    summary = {"target": target, "seconds": samples[-1]["t"] if samples else 0, "samples": samples,
               "cpu_temp_peak_c": peak("cpu_temp_c"), "gpu_temp_peak_c": peak("gpu_temp_c"),
               "gpu_power_peak_w": peak("gpu_w"), "findings": []}

    clocks = series("cpu_mhz")
    if target in ("cpu", "both") and len(clocks) >= 10:
        early = sum(clocks[: len(clocks) // 4]) / (len(clocks) // 4)
        late = sum(clocks[-len(clocks) // 4:]) / (len(clocks) // 4)
        summary["cpu_clock_early_mhz"], summary["cpu_clock_late_mhz"] = round(early), round(late)
        drop = (early - late) / early * 100 if early else 0
        if drop >= 10:
            summary["findings"].append(f"CPU clock fell {drop:.0f} % during the test (heat or power limits).")
        else:
            summary["findings"].append("CPU clock held steady under load.")
    gpu_limits = sorted({reason for s in samples for reason in s.get("gpu_limits", [])})
    if target in ("gpu", "both"):
        summary["gpu_limits_seen"] = gpu_limits
        if gpu_limits:
            summary["findings"].append("GPU was limited by: " + ", ".join(gpu_limits) + ".")
        else:
            summary["findings"].append("GPU ran without hitting power or thermal limits.")
    return summary
