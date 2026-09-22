"""后台采集线程:CPU / GPU / 内存 / 磁盘 / 网络 / 温度,1 秒一拍。"""
import json
import os
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass

import psutil
from PySide6.QtCore import QThread, Signal

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0  # 打包成无控制台 exe 后,子进程必须禁止弹窗

try:
    import pynvml
    pynvml.nvmlInit()
    _NVML_HANDLE = pynvml.nvmlDeviceGetHandleByIndex(0)
    GPU_BACKEND = "nvidia"
except Exception:
    try:
        from pyadl import ADLManager
        _AMD_DEVICES = ADLManager.getInstance().getDevices()
        GPU_BACKEND = "amd" if _AMD_DEVICES else None
    except Exception:
        GPU_BACKEND = None
HAS_GPU = GPU_BACKEND is not None

HISTORY_LEN = 300  # 5 分钟 @ 1s
DISK_TEMP_PERIOD = 5.0  # 硬盘温度采样周期(PowerShell 子进程较贵,SSD 温度变化慢)


@dataclass
class Snapshot:
    ts: float
    cpu: float            # CPU 使用率 %
    gpu: float | None     # GPU 使用率 %,无 N 卡为 None
    gpu_mem: float | None  # 显存使用率 %
    mem: float            # 内存使用率 %
    disk: float           # 系统盘容量使用率 %
    disk_write: float     # 磁盘写入 MB/s
    disk_read: float      # 磁盘读取 MB/s
    net_up: float         # 上行 KB/s
    net_down: float       # 下行 KB/s
    temp: float | None     # CPU 温度 °C
    gpu_temp: float | None  # GPU 温度 °C
    mem_temp: float | None  # 内存温度 °C(消费级主板无传感器,恒 None)
    disk_temp: float | None  # 硬盘温度 °C


def _probe_temp():
    """探测 CPU 温度源:优先 LibreHardwareMonitor,回退 ACPI 热区(需管理员)。"""
    try:
        import wmi
    except ImportError:
        return None, None
    for ns, kind in (("root/LibreHardwareMonitor", "lhm"), ("root/wmi", "acpi")):
        try:
            conn = wmi.WMI(namespace=ns)
            if kind == "lhm":
                rows = conn.query(
                    "SELECT Value FROM Sensor WHERE SensorType='Temperature' AND Name LIKE '%CPU%'")
            else:
                rows = conn.query("SELECT CurrentTemperature FROM MSAcpi_ThermalZoneTemperature")
            if rows:
                return kind, conn
        except Exception:
            continue
    return None, None


class Collector(QThread):
    snapshot_ready = Signal(object)

    def __init__(self, interval=1.0, parent=None):
        super().__init__(parent)
        self.interval = interval
        self._running = True
        self._lock = threading.Lock()
        self._history = {k: deque(maxlen=HISTORY_LEN)
                         for k in ("cpu", "gpu", "mem", "disk", "disk_write", "disk_read",
                                   "net_up", "net_down",
                                   "temp_cpu", "temp_gpu", "temp_mem", "temp_disk")}
        self._temp_kind = None
        self._temp_conn = None
        self._disk_temp = None
        self._disk_temp_t = 0.0
        psutil.cpu_percent(interval=None)  # 预热,第一次调用无意义
        self._last_net = psutil.net_io_counters()
        self._last_disk = psutil.disk_io_counters()
        self._last_t = time.monotonic()

    def stop(self):
        self._running = False
        self.wait(3000)

    def history(self, key):
        with self._lock:
            return list(self._history[key])

    def _read_cpu_temp(self):
        if self._temp_kind is None:
            return None
        try:
            if self._temp_kind == "lhm":
                rows = self._temp_conn.query(
                    "SELECT Value FROM Sensor WHERE SensorType='Temperature' AND Name LIKE '%CPU%'")
                vals = [float(r.Value) for r in rows if r.Value is not None]
            else:
                rows = self._temp_conn.query(
                    "SELECT CurrentTemperature FROM MSAcpi_ThermalZoneTemperature")
                vals = [float(r.CurrentTemperature) / 10 - 273.15
                        for r in rows if r.CurrentTemperature]
            return round(max(vals), 1) if vals else None  # 取最热区
        except Exception:
            return None

    def _read_gpu_temp(self):
        if self._temp_kind == "lhm":
            try:
                rows = self._temp_conn.query(
                    "SELECT Value FROM Sensor WHERE SensorType='Temperature' AND Name LIKE '%GPU%'")
                vals = [float(r.Value) for r in rows if r.Value is not None]
                if vals:
                    return round(max(vals), 1)
            except Exception:
                pass
        if GPU_BACKEND == "nvidia":
            try:
                return float(pynvml.nvmlDeviceGetTemperature(
                    _NVML_HANDLE, pynvml.NVML_TEMPERATURE_GPU))
            except Exception:
                pass
        elif GPU_BACKEND == "amd":
            try:
                return float(_AMD_DEVICES[0].getCurrentTemperature())
            except Exception:
                pass
        return None

    def _read_disk_temp(self, now):
        if now - self._disk_temp_t < DISK_TEMP_PERIOD:
            return self._disk_temp  # 节流:沿用上次值
        self._disk_temp_t = now
        try:
            r = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "Get-PhysicalDisk | ForEach-Object { "
                 "($_ | Get-StorageReliabilityCounter -ErrorAction SilentlyContinue).Temperature } "
                 "| Where-Object { $_ } | ConvertTo-Json"],
                capture_output=True, text=True, timeout=10,
                creationflags=CREATE_NO_WINDOW)
            vals = json.loads(r.stdout or "null")
            if isinstance(vals, (int, float)):
                vals = [vals]
            vals = [float(v) for v in (vals or []) if v]
            if vals:
                self._disk_temp = round(max(vals), 1)
        except Exception:
            pass  # 无权限/无传感器:保持上次值
        return self._disk_temp

    def _sample(self) -> Snapshot:
        now = time.monotonic()
        dt = max(now - self._last_t, 1e-6)

        net = psutil.net_io_counters()
        net_up = (net.bytes_sent - self._last_net.bytes_sent) / dt / 1024
        net_down = (net.bytes_recv - self._last_net.bytes_recv) / dt / 1024
        self._last_net = net

        dio = psutil.disk_io_counters()
        disk_write = max(0.0, dio.write_bytes - self._last_disk.write_bytes) / dt / 1024 / 1024
        disk_read = max(0.0, dio.read_bytes - self._last_disk.read_bytes) / dt / 1024 / 1024
        self._last_disk = dio
        self._last_t = now

        gpu = gpu_mem = None
        if GPU_BACKEND == "nvidia":
            try:
                gpu = float(pynvml.nvmlDeviceGetUtilizationRates(_NVML_HANDLE).gpu)
                mi = pynvml.nvmlDeviceGetMemoryInfo(_NVML_HANDLE)
                gpu_mem = mi.used / mi.total * 100
            except Exception:
                gpu = gpu_mem = None
        elif GPU_BACKEND == "amd":
            try:
                gpu = float(_AMD_DEVICES[0].getCurrentUsage())
            except Exception:
                pass
            try:
                gpu_mem = float(_AMD_DEVICES[0].getCurrentMemoryUsage())
            except Exception:
                pass

        snap = Snapshot(
            ts=time.time(),
            cpu=psutil.cpu_percent(interval=None),
            gpu=gpu,
            gpu_mem=gpu_mem,
            mem=psutil.virtual_memory().percent,
            disk=psutil.disk_usage("C:\\").percent,
            disk_write=disk_write,
            disk_read=disk_read,
            net_up=max(0.0, net_up),
            net_down=max(0.0, net_down),
            temp=self._read_cpu_temp(),
            gpu_temp=self._read_gpu_temp(),
            mem_temp=None,  # 消费级主板无内存温度传感器
            disk_temp=self._read_disk_temp(now),
        )
        with self._lock:
            for k, v in (("cpu", snap.cpu), ("gpu", snap.gpu), ("mem", snap.mem),
                         ("disk", snap.disk), ("disk_write", snap.disk_write),
                         ("disk_read", snap.disk_read),
                         ("net_up", snap.net_up), ("net_down", snap.net_down),
                         ("temp_cpu", snap.temp), ("temp_gpu", snap.gpu_temp),
                         ("temp_mem", snap.mem_temp), ("temp_disk", snap.disk_temp)):
                self._history[k].append(v)
        return snap

    def run(self):
        try:
            import pythoncom
            pythoncom.CoInitialize()  # 工作线程调用 WMI/COM 前必须初始化
        except Exception:
            pass
        try:
            self._temp_kind, self._temp_conn = _probe_temp()
            while self._running:
                snap = self._sample()
                self.snapshot_ready.emit(snap)
                end = time.monotonic() + self.interval
                while self._running and time.monotonic() < end:
                    time.sleep(min(0.1, end - time.monotonic()))
        finally:
            try:
                import pythoncom
                pythoncom.CoUninitialize()
            except Exception:
                pass
