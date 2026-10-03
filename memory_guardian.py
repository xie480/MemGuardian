from __future__ import annotations

import argparse
import ctypes
import logging
import os
import subprocess
import time
from ctypes import wintypes
from pathlib import Path

import psutil


DEFAULT_TASK_NAME = "MemoryBoost"
DEFAULT_THRESHOLD = 90.0      # 内存占用率阈值
DEFAULT_RELEASE = 80.0        # 低于该值后允许再次触发
DEFAULT_INTERVAL = 0.25       # 轮询间隔（秒）
DEFAULT_COOLDOWN = 120        # 触发后的冷却时间（秒）
DEFAULT_CLEANER_PROCESS = "memory_boost.exe"
TASK_START_TIMEOUT = 15
RETRY_BACKOFF_INITIAL = 5
RETRY_BACKOFF_MAX = 60
ERROR_ALREADY_EXISTS = 183

BASE_DIR = Path(__file__).resolve().parent
LOG_FILE = BASE_DIR / "memory_guardian.log"


def setup_logging() -> None:
    """初始化日志配置"""
    logging.basicConfig(
        filename=str(LOG_FILE),
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        encoding="utf-8",
    )


def memory_snapshot() -> tuple[float, int, int]:
    """返回占用率、可用内存字节数和物理内存总量。"""
    memory = psutil.virtual_memory()
    return float(memory.percent), int(memory.available), int(memory.total)


def acquire_single_instance() -> tuple[object, object | None, bool]:
    """使用当前登录会话的 Windows 命名互斥量防止重复守护进程。"""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = (
        wintypes.LPVOID,
        wintypes.BOOL,
        wintypes.LPCWSTR,
    )
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    kernel32.ReleaseMutex.argtypes = (wintypes.HANDLE,)
    kernel32.ReleaseMutex.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.restype = wintypes.BOOL

    ctypes.set_last_error(0)
    handle = kernel32.CreateMutexW(
        None,
        True,
        r"Local\MemGuardian.MemoryGuardian",
    )
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())

    if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        kernel32.CloseHandle(handle)
        logging.info("Another memory guardian instance is already running.")
        return kernel32, None, False

    return kernel32, handle, True


def release_single_instance(kernel32: object, handle: object) -> None:
    """释放互斥量并关闭 Windows 句柄。"""
    try:
        kernel32.ReleaseMutex(handle)
    finally:
        kernel32.CloseHandle(handle)


def cleaner_is_running(process_name: str) -> bool:
    """避免在现有清理器尚未退出时再次启动计划任务。"""
    expected_name = Path(process_name).name.casefold()
    for process in psutil.process_iter(["name"]):
        try:
            name = process.info.get("name")
        except psutil.Error:
            continue
        if name and name.casefold() == expected_name:
            return True
    return False


def run_task(task_name: str) -> bool:
    """
    运行 Windows 计划任务。

    返回：
    True  : 任务启动成功
    False : 任务启动失败
    """
    task_scheduler = (
        Path(os.environ.get("SystemRoot", r"C:\Windows"))
        / "System32"
        / "schtasks.exe"
    )
    if not task_scheduler.is_file():
        logging.error("Task Scheduler command not found: %s", task_scheduler)
        return False

    try:
        result = subprocess.run(
            [str(task_scheduler), "/run", "/tn", task_name],
            capture_output=True,
            text=True,
            errors="replace",
            shell=False,
            timeout=TASK_START_TIMEOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

        if result.returncode == 0:
            logging.info("Task start request accepted: %s", task_name)
            return True

        logging.warning(
            "Failed to trigger task=%s rc=%s stdout=%r stderr=%r",
            task_name,
            result.returncode,
            result.stdout.strip()[:500],
            result.stderr.strip()[:500],
        )
        return False

    except subprocess.TimeoutExpired:
        logging.error(
            "Timed out after %ss while requesting task=%s",
            TASK_START_TIMEOUT,
            task_name,
        )
        return False
    except Exception as e:
        logging.exception("Exception when triggering task %s: %s", task_name, e)
        return False


def main() -> int:
    if os.name != "nt":
        print("该脚本仅支持 Windows 系统运行。")
        return 2

    parser = argparse.ArgumentParser(description="内存守护进程")

    parser.add_argument(
        "--task",
        default=DEFAULT_TASK_NAME,
        help="计划任务名称"
    )

    parser.add_argument(
        "--threshold",
        type=float,
        default=DEFAULT_THRESHOLD,
        help="触发阈值（内存占用率）"
    )

    parser.add_argument(
        "--release",
        type=float,
        default=DEFAULT_RELEASE,
        help="重新布防阈值"
    )

    parser.add_argument(
        "--interval",
        type=float,
        default=DEFAULT_INTERVAL,
        help="轮询间隔（秒）"
    )

    parser.add_argument(
        "--cooldown",
        type=int,
        default=DEFAULT_COOLDOWN,
        help="冷却时间（秒）"
    )

    parser.add_argument(
        "--cleaner-process",
        default=DEFAULT_CLEANER_PROCESS,
        help="清理器进程名；运行中时暂缓重复触发"
    )

    args = parser.parse_args()

    if not 0 < args.threshold <= 100:
        parser.error("--threshold 必须大于 0 且不超过 100")
    if not 0 <= args.release < args.threshold:
        parser.error("--release 必须大于等于 0 且小于 --threshold")
    if not 0.1 <= args.interval <= 60:
        parser.error("--interval 必须在 0.1 到 60 秒之间")
    if not 1 <= args.cooldown <= 86400:
        parser.error("--cooldown 必须在 1 到 86400 秒之间")
    if not args.task.strip():
        parser.error("--task 不能为空")
    if not Path(args.cleaner_process).name.strip():
        parser.error("--cleaner-process 不能为空")

    setup_logging()

    try:
        kernel32, mutex_handle, owns_mutex = acquire_single_instance()
    except Exception:
        logging.exception("Unable to create the single-instance mutex.")
        return 1

    if not owns_mutex:
        return 0

    logging.info(
        "Started memory guardian: task=%s threshold=%.1f%% release=%.1f%% "
        "interval=%.2fs cooldown=%ss cleaner=%s",
        args.task,
        args.threshold,
        args.release,
        args.interval,
        args.cooldown,
        Path(args.cleaner_process).name,
    )

    armed = True
    last_success_ts: float | None = None
    next_attempt_ts = 0.0
    failure_backoff = RETRY_BACKOFF_INITIAL

    try:
        while True:
            try:
                mem, available_bytes, total_bytes = memory_snapshot()
                now = time.monotonic()

                if mem <= args.release:
                    if not armed:
                        logging.info("Re-armed at memory=%.1f%%", mem)
                    armed = True
                    last_success_ts = None
                    next_attempt_ts = 0.0
                    failure_backoff = RETRY_BACKOFF_INITIAL

                cooldown_elapsed = (
                    last_success_ts is None
                    or now - last_success_ts >= args.cooldown
                )
                if (
                    mem >= args.threshold
                    and (armed or cooldown_elapsed)
                    and now >= next_attempt_ts
                ):
                    available_mib = available_bytes / (1024 * 1024)
                    total_mib = total_bytes / (1024 * 1024)
                    logging.info(
                        "Threshold reached: memory=%.1f%% >= %.1f%%, "
                        "available=%.0f MiB / total=%.0f MiB",
                        mem,
                        args.threshold,
                        available_mib,
                        total_mib,
                    )

                    if cleaner_is_running(args.cleaner_process):
                        defer_seconds = max(args.interval, 1.0)
                        logging.info(
                            "Cleaner process %s is already running; "
                            "checking again in %.1fs",
                            Path(args.cleaner_process).name,
                            defer_seconds,
                        )
                        next_attempt_ts = now + defer_seconds
                    elif run_task(args.task):
                        last_success_ts = now
                        next_attempt_ts = now + args.cooldown
                        failure_backoff = RETRY_BACKOFF_INITIAL
                        armed = False
                    else:
                        logging.warning(
                            "Task request failed; retrying in %ss",
                            failure_backoff,
                        )
                        next_attempt_ts = now + failure_backoff
                        failure_backoff = min(
                            failure_backoff * 2,
                            RETRY_BACKOFF_MAX,
                        )

                time.sleep(args.interval)

            except KeyboardInterrupt:
                logging.info("Stopped by user.")
                return 0

            except Exception:
                logging.exception("Loop error; retrying after a short delay.")
                time.sleep(max(1.0, args.interval))
    finally:
        release_single_instance(kernel32, mutex_handle)


if __name__ == "__main__":
    raise SystemExit(main())
