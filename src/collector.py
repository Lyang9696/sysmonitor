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

# GPU 使用率读取后端:优先 N 卡官方 NVML,回退 AMD ADL,都没有则 UI 显示 --
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
    disk: float | None    # 磁盘活跃时间 %(任务管理器口径)
    disk_write: float     # 磁盘写入 MB/s
    disk_read: float      # 磁盘读取 MB/s
    net_up: float         # 上行 KB/s
    net_down: float       # 下行 KB/s
    temp: float | None    # CPU 温度 °C
    gpu_temp: float | None  # GPU 温度 °C
    mem_temp: float | None  # 内存温度 °C(消费级主板无传感器)
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

        # 历史曲线缓冲:每个指标一份,供主窗口折线图读取
        self._history = {k: deque(maxlen=HISTORY_LEN)
                         for k in ("cpu", "gpu", "mem", "disk", "disk_write", "disk_read",
                                   "net_up", "net_down",
                                   "temp_cpu", "temp_gpu", "temp_mem", "temp_disk")}
        self._lock = threading.Lock()

        # CPU/GPU 温度源(LibreHardwareMonitor 或 ACPI),run() 内探测
        self._temp_kind = None
        self._temp_conn = None

        # 硬盘温度:后台线程查询(每次新建 PowerShell 进程,耗时 0.5~2s)
        self._disk_temp = None
        self._disk_temp_busy = False
        self._disk_temp_t = time.monotonic()  # 延后首次查询,避免启动即阻塞采集线程

        # 差分基线:速率类指标按两次采样差值计算
        psutil.cpu_percent(interval=None)  # 预热,第一次调用无意义
        self._last_net = psutil.net_io_counters()
        self._last_disk = psutil.disk_io_counters()
        self._last_t = time.monotonic()

        # 磁盘活跃时间计数器连接,run() 内创建(COM 对象不能跨线程使用)
        self._perf_conn = None

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

    def _disk_active(self):
        """任务管理器口径:磁盘活跃时间% = 100 - %IdleTime,取最忙物理盘。"""
        if self._perf_conn is None:
            return None
        try:
            rows = self._perf_conn.query(
                "SELECT Name, PercentIdleTime FROM Win32_PerfFormattedData_PerfDisk_PhysicalDisk")
            actives = [100.0 - float(r.PercentIdleTime) for r in rows
                       if r.Name != "_Total" and r.PercentIdleTime is not None]
            return round(max(min(max(actives), 100.0), 0.0), 1) if actives else None
        except Exception:
            return None

    def _read_disk_temp(self, now):
        if now - self._disk_temp_t < DISK_TEMP_PERIOD or self._disk_temp_busy:
            return self._disk_temp  # 节流;查询已在后台进行时直接用缓存
        self._disk_temp_t = now
        self._disk_temp_busy = True
        threading.Thread(target=self._query_disk_temp, daemon=True).start()
        return self._disk_temp

    def _query_disk_temp(self):
        # 后台线程执行:PowerShell 冷启动 0.5~2s,放采集循环里会周期性阻塞采样
        try:
            r = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "Get-PhysicalDisk | ForEach-Object { "
                 "($_ | Get-StorageReliabilityCounter -ErrorAction SilentlyContinue).Temperature } "
                 "| Where-Object { $_ } | ConvertTo-Json"],
                capture_output=True, text=True, timeout=15,
                creationflags=CREATE_NO_WINDOW)
            vals = json.loads(r.stdout or "null")
            if isinstance(vals, (int, float)):
                vals = [vals]
            vals = [float(v) for v in (vals or []) if v]
            if vals:
                self._disk_temp = round(max(vals), 1)
        except Exception:
            pass  # 无权限/无传感器:保持上次值
        finally:
            self._disk_temp_busy = False

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
            disk=self._disk_active(),
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
        # COM 初始化:本线程的 WMI 查询(温度源、磁盘活跃时间)都依赖它
        try:
            import pythoncom
            pythoncom.CoInitialize()
        except Exception:
            pass
        try:
            # 探测温度源 / 建立磁盘计数器连接(失败则对应指标显示 --)
            self._temp_kind, self._temp_conn = _probe_temp()
            try:
                import wmi
                self._perf_conn = wmi.WMI(namespace="root/CIMV2")
                self._perf_conn.query(  # 预热,formatted 计数器首查无差分
                    "SELECT PercentIdleTime FROM Win32_PerfFormattedData_PerfDisk_PhysicalDisk")
            except Exception:
                pass

            # 采集主循环:每拍采样并广播,失败只跳过本拍,绝不中断线程
            while self._running:
                try:
                    snap = self._sample()
                except Exception:
                    snap = None  # ponytail: 单次采样失败(WMI 偶发错误等)跳过本拍,不杀采集线程
                if snap is not None:
                    self.snapshot_ready.emit(snap)
                end = time.monotonic() + self.interval
                while self._running and time.monotonic() < end:
                    time.sleep(min(0.1, end - time.monotonic()))
        finally:
            self._perf_conn = None
            self._temp_conn = None
            try:
                import pythoncom
                pythoncom.CoUninitialize()
            except Exception:
                pass
