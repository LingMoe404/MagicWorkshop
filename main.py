import sys
import os
os.environ["QT_API"] = "pyside6"
import shutil
import time
import re
import ctypes
import random
import subprocess
import json
import configparser

from PySide6.QtCore import Qt, QThread, Signal, QSize, QUrl, QTimer
from PySide6.QtWidgets import (QApplication, QWidget, QVBoxLayout, QHBoxLayout, 
                               QFileDialog, QFrame, QListWidgetItem, QAbstractItemView, QSplitter, QStyleOptionViewItem, QStyle)
from PySide6.QtGui import QIcon, QColor, QDesktopServices, QGuiApplication

# 引入 Fluent Widgets (Win11 风格组件)
from qfluentwidgets import (FluentWindow, SubtitleLabel, StrongBodyLabel, BodyLabel, 
                            LineEdit, PrimaryPushButton, PushButton, ProgressBar, 
                            TextEdit, SwitchButton, ComboBox, CardWidget, InfoBar, 
                            InfoBarPosition, setTheme, Theme, FluentIcon, setThemeColor, isDarkTheme, ImageLabel, MessageDialog,
                            ListWidget)
from qfluentwidgets.components.widgets.list_view import ListItemDelegate


class ClickableBodyLabel(BodyLabel):
    clicked = Signal()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(e)


class NoHighlightItemDelegate(ListItemDelegate):
    """兼容 Fluent ListWidget 接口，同时去除 hover/selected/focus 高亮。"""

    def paint(self, painter, option, index):
        opt = QStyleOptionViewItem(option)
        opt.state &= ~QStyle.StateFlag.State_Selected
        opt.state &= ~QStyle.StateFlag.State_MouseOver
        opt.state &= ~QStyle.StateFlag.State_HasFocus

        selected_rows = self.selectedRows.copy()
        hover_row = self.hoverRow
        pressed_row = self.pressedRow

        self.selectedRows = set()
        self.hoverRow = -1
        self.pressedRow = -1
        try:
            super().paint(painter, opt, index)
        finally:
            self.selectedRows = selected_rows
            self.hoverRow = hover_row
            self.pressedRow = pressed_row


class DroppableBodyLabel(BodyLabel):
    filesDropped = Signal(list)
    dragActiveChanged = Signal(bool)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setAcceptDrops(True)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            self.dragActiveChanged.emit(True)
            e.acceptProposedAction()
        else:
            super().dragEnterEvent(e)

    def dragMoveEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
        else:
            super().dragMoveEvent(e)

    def dragLeaveEvent(self, e):
        self.dragActiveChanged.emit(False)
        super().dragLeaveEvent(e)

    def dropEvent(self, e):
        paths = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        if paths:
            self.filesDropped.emit(paths)
            self.dragActiveChanged.emit(False)
            e.acceptProposedAction()
        else:
            self.dragActiveChanged.emit(False)
            e.ignore()


class DroppableListWidget(ListWidget):
    filesDropped = Signal(list)
    dragActiveChanged = Signal(bool)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setAcceptDrops(True)
        # 使用无高亮委托，避免主题切换后 Fluent 默认高亮复活
        self.setItemDelegate(NoHighlightItemDelegate(self))

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            self.dragActiveChanged.emit(True)
            e.acceptProposedAction()
        else:
            super().dragEnterEvent(e)

    def dragMoveEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
        else:
            super().dragMoveEvent(e)

    def dragLeaveEvent(self, e):
        self.dragActiveChanged.emit(False)
        super().dragLeaveEvent(e)

    def dropEvent(self, e):
        paths = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        if paths:
            self.filesDropped.emit(paths)
            self.dragActiveChanged.emit(False)
            e.acceptProposedAction()
        else:
            self.dragActiveChanged.emit(False)
            e.ignore()

    def mousePressEvent(self, e):
        super().mousePressEvent(e)
        self.clearSelection()
        self.setCurrentRow(-1)

# --- 核心工具函数 ---
def resource_path(relative_path):
    """获取资源绝对路径：打包后取 exe 同级，开发环境取项目根目录。"""
    base_path = os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else os.path.abspath(".")
    return os.path.join(base_path, relative_path)

def tool_path(filename):
    """ 获取 tools 目录下工具的绝对路径 """
    return resource_path(os.path.join("tools", filename))

def safe_decode(bytes_data):
    if not bytes_data:
        return ""

    try:
        return bytes_data.decode('utf-8').strip()
    except UnicodeDecodeError:
        try:
            return bytes_data.decode('gbk').strip()
        except UnicodeDecodeError:
            return bytes_data.decode('utf-8', errors='ignore').strip()

def time_str_to_seconds(time_str):
    try:
        h, m, s = time_str.split(':')
        return int(h) * 3600 + int(m) * 60 + float(s)
    except Exception:
        return 0.0

def to_long_path(path):
    """ 转换路径以支持 Windows 长路径 (超过 260 字符) """
    if os.name == 'nt':
        path = os.path.abspath(path)
        if not path.startswith('\\\\?\\'):
            return '\\\\?\\' + path
    return path

DEFAULT_SETTINGS = {
    "encoder": "Intel QSV",
    "vmaf": "93.0",
    "audio_bitrate": "96k",
    "preset": "4",
    "loudnorm": "loudnorm=I=-16:TP=-1.5:LRA=11,aresample=48000",
    "theme": "Auto",
    "nv_aq": "True",
    "save_mode": "元素覆写 (Overwrite)",
    "export_dir": ""
}

VIDEO_EXTS = ('.mkv', '.mp4', '.avi', '.mov', '.wmv', '.flv', '.webm', '.m4v', '.ts')
SAVE_MODE_SAVE_AS = "开辟新世界 (Save As)"
SAVE_MODE_OVERWRITE = "元素覆写 (Overwrite)"
SAVE_MODE_REMAIN = "元素保留 (Remain)"

def get_default_cache_dir():
    """ 获取默认缓存目录 (软件根目录/cache) """
    base_path = os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else os.path.abspath(".")
    return os.path.join(base_path, "cache")

def get_config_path():
    """ 获取配置文件路径 (exe同级) """
    base_path = os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else os.path.abspath(".")
    return os.path.join(base_path, "config.ini")

# --- 工作线程 (负责耗时的转码任务) ---
class EncoderWorker(QThread):
    # 定义信号，用于通知 UI 更新
    log_signal = Signal(str, str) # msg, level (info/success/error)
    progress_total_signal = Signal(int)
    progress_current_signal = Signal(int)
    finished_signal = Signal()
    ask_error_decision = Signal(str, str)
    
    def __init__(self, config):
        super().__init__()
        self.config = config
        self.is_running = True
        self.is_paused = False
        self.current_proc = None

    def stop(self):
        self.is_running = False
        if self.current_proc:
            try:
                # 使用 Popen 异步执行 taskkill，避免阻塞 UI 线程导致假死
                subprocess.Popen(["taskkill", "/F", "/T", "/PID", str(self.current_proc.pid)], 
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
            except Exception:
                pass

    def set_paused(self, paused):
        self.is_paused = paused

    def set_system_awake(self, keep_awake=True):
        try:
            if keep_awake:
                ctypes.windll.kernel32.SetThreadExecutionState(0x80000003)
            else:
                ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)
        except Exception:
            pass

    def receive_decision(self, decision):
        self.decision = decision
        self.waiting_decision = False

    def run(self):
        # 解包配置
        selected_files = self.config.get('selected_files') or []
        encoder_type = self.config.get('encoder', 'Intel QSV')
        export_dir = self.config['export_dir']
        cache_dir = self.config.get('cache_dir') or get_default_cache_dir()
        save_mode = self.config.get('save_mode', SAVE_MODE_OVERWRITE)
        try:
            os.makedirs(cache_dir, exist_ok=True)
        except Exception:
            cache_dir = ""
        preset = self.config['preset']
        target_vmaf = self.config['vmaf']
        audio_bitrate = self.config['audio_bitrate']
        loudnorm = self.config['loudnorm']

        ffmpeg = tool_path("ffmpeg.exe")
        ffprobe = tool_path("ffprobe.exe")
        ab_av1 = tool_path("ab-av1.exe")
        
        os.environ["PATH"] += os.pathsep + os.path.dirname(ffmpeg)
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

        try:
            self.set_system_awake(True)
            tasks = []

            # 统一使用已选择素材列表
            for p in selected_files:
                if os.path.isfile(p) and p.lower().endswith(VIDEO_EXTS):
                    tasks.append(p)
            
            total_tasks = len(tasks)
            if total_tasks == 0:
                self.log_signal.emit("侦测不到任何魔力残留... (｡•ˇ‸ˇ•｡)", "error")
                self.finished_signal.emit()
                return

            self.log_signal.emit(f"捕捉到 {total_tasks} 个待净化异变体！( •̀ ω •́ )y", "info")

            for i, filepath in enumerate(tasks):
                if not self.is_running:
                    break

                fname = os.path.basename(filepath)
                self.log_signal.emit(f"[{i+1}/{total_tasks}] 正在对 {fname} 展开固有结界...", "info")
                
                self.progress_total_signal.emit(int((i / total_tasks) * 100))
                self.progress_current_signal.emit(0)

                # 1. 探测是否已是 AV1
                try:
                    cmd_probe = [ffprobe, "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=codec_name", "-of", "default=noprint_wrappers=1:nokey=1", filepath]
                    raw_codec = subprocess.check_output(cmd_probe, creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
                    codec = safe_decode(raw_codec).lower()
                    if "av1" in codec:
                        self.log_signal.emit(" -> 此物质已是纯净形态 (AV1)，跳过~ (Pass)", "success")
                        continue
                except Exception:
                    pass

                # 2. 获取时长
                duration_sec = 0.0
                try:
                    cmd_dur = [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", filepath]
                    out_dur = subprocess.check_output(cmd_dur, creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
                    duration_sec = float(safe_decode(out_dur))
                except Exception:
                    pass

                # 2.1 获取原始音轨声道数（避免固定转为双声道）
                source_audio_channels = None
                try:
                    cmd_ach = [ffprobe, "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=channels", "-of", "default=noprint_wrappers=1:nokey=1", filepath]
                    out_ach = subprocess.check_output(cmd_ach, creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
                    ach = int(safe_decode(out_ach))
                    if ach > 0:
                        source_audio_channels = ach
                except Exception:
                    pass

                # 3. 准备编码器参数
                def map_amd_preset(p):
                    # 将 1-7 的通用速度预设映射为 AMF 支持的预设
                    try:
                        p = int(p)
                    except Exception:
                        p = 4
                    if p <= 2:
                        return "quality"
                    if p <= 5:
                        return "balanced"
                    return "speed"

                if "NVIDIA" in encoder_type:
                    enc_name = "av1_nvenc"
                    enc_preset = f"p{preset}" # NVENC uses p1-p7
                    enc_pix_fmt = "yuv420p10le" # [Fix] ab-av1 参数校验不支持 p010le，需用 yuv420p10le
                elif "AMD" in encoder_type:
                    enc_name = "av1_amf"
                    enc_preset = map_amd_preset(preset)
                    enc_pix_fmt = "yuv420p10le"
                else:
                    enc_name = "av1_qsv"
                    enc_preset = preset
                    enc_pix_fmt = "yuv420p10le" # ab-av1 use

                # 3. ab-av1 搜索
                cmd_search = [
                    ab_av1, "crf-search", "-i", filepath,
                    "--encoder", enc_name,
                    "--min-vmaf", str(target_vmaf),
                    "--preset", enc_preset,
                    "--pix-format", enc_pix_fmt
                ]
                if cache_dir and os.path.isdir(cache_dir):
                    cmd_search.extend(["--temp-dir", cache_dir])

                self.log_signal.emit(" -> 正在推演最强术式 (ab-av1)...", "info")
                
                best_icq = 24
                search_success = False
                ab_av1_log = []
                
                try:
                    self.current_proc = subprocess.Popen(cmd_search, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, bufsize=0, creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
                    while True:
                        if not self.is_running:
                            self.current_proc.kill()
                            break
                        
                        while self.is_paused:
                            if not self.is_running:
                                break
                            time.sleep(0.1)

                        line = self.current_proc.stdout.readline()
                        if not line and self.current_proc.poll() is not None:
                            break
                        if line:
                            decoded = safe_decode(line)
                            ab_av1_log.append(decoded)
                            # [Fix] 兼容 NVENC 的 cq/qp 输出，以及 QSV 的 crf 输出，并提取 VMAF 分数
                            match = re.search(r"(?:crf|cq|qp)\s+(\d+)", decoded, re.IGNORECASE)
                            vmaf_match = re.search(r"VMAF\s+([\d.]+)", decoded, re.IGNORECASE)
                            if match and vmaf_match:
                                val = match.group(1)
                                vmaf_score = vmaf_match.group(1)
                                self.log_signal.emit(f"    -> 探测中: {match.group(0).upper()} {val} => VMAF: {vmaf_score}", "info")
                                best_icq = int(val)
                                search_success = True
                    self.current_proc.wait()
                    # 显式清理管道
                    if self.current_proc.stdout:
                        self.current_proc.stdout.close()
                    if self.current_proc.stderr:
                        self.current_proc.stderr.close()

                except Exception:
                    pass

                if not self.is_running:
                    break

                if search_success:
                    self.log_signal.emit(f" -> 术式解析完毕 (ICQ): {best_icq} (๑•̀ㅂ•́)و✧", "success")
                else:
                    self.log_signal.emit(f" -> 解析失败，强制使用基础术式 ICQ: {best_icq} (T_T)", "error")
                    # [Fix] 输出 ab-av1 的最后几行日志以便排查
                    if ab_av1_log:
                        self.log_signal.emit("    [ab-av1 错误回溯]:", "error")
                        for log_line in ab_av1_log[-5:]:
                            self.log_signal.emit(f"    {log_line}", "error")

                # 4. FFmpeg 转码
                base_name = os.path.splitext(fname)[0]
                if cache_dir and os.path.isdir(cache_dir):
                    temp_file = os.path.join(cache_dir, f"{base_name}_{int(time.time())}.temp.mkv")
                else:
                    temp_file = os.path.join(os.path.dirname(filepath), base_name + ".temp.mkv")
                
                if save_mode == SAVE_MODE_OVERWRITE:
                    final_dest = os.path.join(os.path.dirname(filepath), base_name + ".mkv")
                elif save_mode == SAVE_MODE_REMAIN:
                    final_dest = os.path.join(os.path.dirname(filepath), base_name + "_opt.mkv")
                else:
                    if not export_dir:
                        export_dir = os.path.dirname(filepath)
                    if not os.path.exists(export_dir):
                        os.makedirs(export_dir, exist_ok=True)
                    final_dest = os.path.join(export_dir, base_name + ".mkv")

                # [Fix] MP4/MOV 容器中的 mov_text 字幕无法直接 copy 到 MKV，需转为 srt/subrip
                sub_codec = "copy"
                if fname.lower().endswith(('.mp4', '.mov', '.m4v')):
                    sub_codec = "subrip"

                audio_args = ["-c:a", "libopus", "-b:a", audio_bitrate, "-ar", "48000"]
                if source_audio_channels:
                    audio_args.extend(["-ac", str(source_audio_channels)])
                audio_args.extend(["-af", loudnorm])

                # 构建 FFmpeg 命令
                cmd = []
                if "NVIDIA" in encoder_type:
                    # NVIDIA NVENC 参数
                    cmd = [
                        ffmpeg, "-y", "-hide_banner",
                        "-i", filepath,
                        "-c:v", "av1_nvenc", 
                        "-preset", enc_preset,
                        "-rc:v", "vbr",       # [Fix] 显式指定 VBR 模式
                        "-cq", str(best_icq), # NVENC 使用 -cq 控制质量
                        "-b:v", "0",          # [Fix] 关键：解除码率上限，防止画质被默认码率限制
                    ]
                    if self.config.get('nv_aq', True):
                        cmd.extend(["-spatial-aq", "1", "-temporal-aq", "1"]) # 感知增强 (AQ)
                    
                    cmd.extend([
                        "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2",
                        "-pix_fmt", "p010le",

                        *audio_args,
                        "-c:s", sub_codec,

                        "-map", "0:v:0", 
                        "-map", "0:a:0?", 
                        "-map", "0:s?",
                        "-progress", "pipe:1",
                        temp_file
                    ])
                elif "AMD" in encoder_type:
                    # AMD AMF 参数
                    cmd = [
                        ffmpeg, "-y", "-hide_banner",
                        "-i", filepath,
                        "-c:v", "av1_amf",
                        "-usage", "transcoding",
                        "-quality", enc_preset,
                        "-rc", "cqp",
                        "-qp_i", str(best_icq),
                        "-qp_p", str(best_icq),
                        "-qp_b", str(best_icq),
                        "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2",
                        "-pix_fmt", "p010le",

                        *audio_args,
                        "-c:s", sub_codec,

                        "-map", "0:v:0",
                        "-map", "0:a:0?",
                        "-map", "0:s?",
                        "-progress", "pipe:1",
                        temp_file
                    ]
                else:
                    # Intel QSV 参数 (默认)
                    cmd = [
                        ffmpeg, "-y", "-hide_banner",
                        "-init_hw_device", "qsv=hw",
                        "-i", filepath,
                        "-c:v", "av1_qsv", "-preset", preset,
                        "-global_quality:v", str(best_icq), 
                        "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2", # 确保分辨率为偶数，防止 QSV 报错
                        "-pix_fmt", "p010le",
                        "-async_depth", "1", # 修复显存溢出/Invalid FrameType

                        *audio_args,
                        "-c:s", sub_codec,

                        "-map", "0:v:0", 
                        "-map", "0:a:0?", 
                        "-map", "0:s?",
                        "-progress", "pipe:1",
                        temp_file
                    ]

                try:
                    self.current_proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, startupinfo=startupinfo, bufsize=0)
                    err_log = []
                    while True:
                        if not self.is_running:
                            self.current_proc.kill()
                            break
                        
                        while self.is_paused:
                            if not self.is_running:
                                break
                            time.sleep(0.1)

                        line = self.current_proc.stdout.readline()
                        if not line and self.current_proc.poll() is not None:
                            break
                        if line:
                            d = safe_decode(line)
                            if "time=" in d and duration_sec > 0:
                                t_match = re.search(r"time=(\d{2}:\d{2}:\d{2}\.\d+)", d)
                                if t_match:
                                    current_sec = time_str_to_seconds(t_match.group(1))
                                    percent = int((current_sec / duration_sec) * 100)
                                    self.progress_current_signal.emit(percent)
                            
                            if "frame=" not in d:
                                err_log.append(d)
                                if len(err_log) > 20:
                                    err_log.pop(0)
                    
                    self.current_proc.wait()
                    # [Fix] 显式关闭管道，释放句柄
                    if self.current_proc.stdout:
                        self.current_proc.stdout.close()
                    if self.current_proc.stderr:
                        self.current_proc.stderr.close()

                    if not self.is_running:
                        lp_temp = to_long_path(temp_file)
                        if os.path.exists(lp_temp):
                            os.remove(lp_temp)
                        break

                    lp_temp = to_long_path(temp_file)
                    if self.current_proc.returncode == 0 and os.path.exists(lp_temp) and os.path.getsize(lp_temp) > 1024:
                        try:
                            lp_dest = to_long_path(final_dest)
                            abs_src = os.path.normcase(os.path.abspath(filepath))
                            abs_dest = os.path.normcase(os.path.abspath(final_dest))
                            lp_src = to_long_path(filepath)
                            
                            if save_mode == SAVE_MODE_OVERWRITE:
                                # [优化] 安全覆盖逻辑：先尝试移动，成功后再处理原文件
                                if abs_src == abs_dest:
                                    # 如果路径完全一致，先重命名原文件作为备份，防止 move 失败
                                    bak_path = lp_src + ".bak"
                                    os.replace(lp_src, bak_path)
                                    shutil.move(lp_temp, lp_dest)
                                    if os.path.exists(bak_path):
                                        os.remove(bak_path)
                                else:
                                    if os.path.exists(lp_dest):
                                        os.remove(lp_dest)
                                    shutil.move(lp_temp, lp_dest)
                                
                                # 只有当源文件和目标文件不同时(例如 mp4 -> mkv)，才删除源文件
                                if abs_src != abs_dest:
                                    os.remove(lp_src)
                                    
                                self.log_signal.emit(" -> 净化完成！旧世界已被重写 (Overwrite) (ﾉ>ω<)ﾉ", "success")
                            else:
                                if os.path.exists(lp_dest):
                                    os.remove(lp_dest)
                                shutil.move(lp_temp, lp_dest)
                                if save_mode == SAVE_MODE_REMAIN:
                                    self.log_signal.emit(" -> 净化完成！元素已保留，优化体已生成 (Remain) (ﾉ>ω<)ﾉ", "success")
                                else:
                                    self.log_signal.emit(" -> 净化完成！新世界已确立 (Save As) (ﾉ>ω<)ﾉ", "success")
                        except Exception as e:
                            self.log_signal.emit(f" -> 封印仪式失败: {e} (T_T)", "error")
                    else:
                        self.log_signal.emit(" -> 术式失控 (Crash)... (T_T)", "error")
                        for err_line in err_log:
                            self.log_signal.emit(f"   {err_line}", "error")
                        lp_temp = to_long_path(temp_file)
                        if os.path.exists(lp_temp):
                            os.remove(lp_temp)
                        
                        # 遇到错误时询问用户
                        if self.is_running:
                            self.waiting_decision = True
                            self.decision = None
                            self.ask_error_decision.emit("术式崩坏警告", f"任务 {fname} 遭遇未知错误。\n是否跳过此任务并继续？")
                            while self.waiting_decision and self.is_running:
                                time.sleep(0.1)
                            if self.decision == 'stop':
                                break

                except Exception as e:
                    self.log_signal.emit(f" -> 魔力逆流: {e} (×_×)", "error")
                
                # [Fix] 冷却机制：强制休眠 3 秒，让 Intel 显卡驱动释放显存和句柄
                if self.is_running:
                    self.log_signal.emit(" -> 正在冷却魔术回路 (Cooling down GPU)...", "info")
                    time.sleep(3)

            if self.is_running:
                self.log_signal.emit(">>> 奇迹达成！(๑•̀ㅂ•́)و✧", "success")
                self.progress_total_signal.emit(100)
                self.progress_current_signal.emit(100)
            else:
                self.log_signal.emit(">>> 契约被强制切断。", "error")

        except Exception as e:
            self.log_signal.emit(f"世界线变动率异常 (Fatal): {e}", "error")
        finally:
            self.set_system_awake(False)
            self.finished_signal.emit()

# --- 异步分析线程 (防止界面卡死) ---
class AnalysisWorker(QThread):
    report_signal = Signal(str)

    def __init__(self, filepath):
        super().__init__()
        self.filepath = filepath

    def run(self):
        ffprobe = tool_path("ffprobe.exe")
        try:
            # 调用 ffprobe 获取 JSON 格式的详细信息
            cmd = [
                ffprobe, "-v", "quiet", "-print_format", "json", 
                "-show_format", "-show_streams", "-show_chapters",
                self.filepath
            ]
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            
            output = subprocess.check_output(cmd, startupinfo=startupinfo, creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
            data = json.loads(output)
            
            # 格式化输出
            report = []
            report.append(f"📜 物质分析报告 (Report): {os.path.basename(self.filepath)}")
            report.append("="*60)
            
            # 1. 容器信息
            fmt = data.get('format', {})
            report.append("📦 容器形态 (Container)")
            report.append(f"   • 真名 (Format): {fmt.get('format_long_name', 'Unknown')}")
            report.append(f"   • 质量 (Size):   {int(fmt.get('size', 0))/1024/1024:.2f} MB")
            report.append(f"   • 观测时长 (Duration): {float(fmt.get('duration', 0)):.2f} s")
            report.append(f"   • 魔力流动 (Bitrate):  {int(fmt.get('bit_rate', 0))/1000:.0f} kbps")
            report.append(f"   • 标签信息 (Tags):     {json.dumps(fmt.get('tags', {}), ensure_ascii=False)}")
            report.append("-" * 60)

            # 2. 流信息
            for stream in data.get('streams', []):
                idx = stream.get('index')
                st_type = stream.get('codec_type', 'unknown').upper()
                codec = stream.get('codec_long_name', stream.get('codec_name', 'Unknown'))
                
                if st_type == 'VIDEO':
                    report.append(f"👁️ 视觉投影 (Stream #{idx} - Video)")
                    report.append(f"   • 核心编码 (Codec):    {codec}")
                    report.append(f"   • 视界范围 (Res):      {stream.get('width')} x {stream.get('height')}")
                    report.append(f"   • 帧率 (FPS):          {stream.get('r_frame_rate')} (Avg: {stream.get('avg_frame_rate')})")
                    report.append(f"   • 色彩空间 (PixFmt):   {stream.get('pix_fmt')}")
                    report.append(f"   • 描述 (Profile):      {stream.get('profile', 'N/A')} (Level {stream.get('level', 'N/A')})")
                    report.append(f"   • 色域 (Color):        {stream.get('color_primaries', 'N/A')} / {stream.get('color_transfer', 'N/A')}")
                    if 'bit_rate' in stream:
                        report.append(f"   • 强度 (Bitrate):      {int(stream.get('bit_rate'))/1000:.0f} kbps")
                
                elif st_type == 'AUDIO':
                    report.append(f"🔊 听觉共鸣 (Stream #{idx} - Audio)")
                    report.append(f"   • 核心编码 (Codec):    {codec}")
                    report.append(f"   • 采样率 (SampleRate): {stream.get('sample_rate')} Hz")
                    report.append(f"   • 声道 (Channels):     {stream.get('channels')} ({stream.get('channel_layout', 'N/A')})")
                    if 'bit_rate' in stream:
                        report.append(f"   • 强度 (Bitrate):      {int(stream.get('bit_rate'))/1000:.0f} kbps")
                
                elif st_type == 'SUBTITLE':
                    report.append(f"📝 铭文记载 (Stream #{idx} - Subtitle)")
                    report.append(f"   • 核心编码 (Codec):    {codec}")
                    if 'tags' in stream and 'language' in stream['tags']:
                        report.append(f"   • 语言 (Lang):         {stream['tags']['language']}")
                
                report.append("-" * 60)

            self.report_signal.emit("\n".join(report))

        except Exception as e:
            self.report_signal.emit(f"💥 解析失败 (Error): {str(e)}\n\n请确保 ffprobe.exe 就在身边哦！")

# --- 详细信息界面 (真理之眼) ---
class MediaInfoInterface(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self.setObjectName("mediaInfoInterface")
        self.setAcceptDrops(True) # 允许拖拽
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 30, 30, 30)
        layout.setSpacing(20)

        # 顶部拖拽区
        self.drop_card = CardWidget(self)
        self.drop_card.setFixedHeight(180)
        card_layout = QVBoxLayout(self.drop_card)
        
        title = SubtitleLabel("真理之眼 · 解析", self.drop_card)
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        hint = BodyLabel("将未知的遗物投入此地以解析... (拖拽文件)", self.drop_card)
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint.setTextColor(QColor("#999999"), QColor("#999999"))
        
        card_layout.addStretch(1)
        card_layout.addWidget(title)
        card_layout.addWidget(hint)
        card_layout.addStretch(1)
        
        layout.addWidget(self.drop_card)
        
        # 底部信息展示区
        self.info_text = TextEdit(self)
        self.info_text.setReadOnly(True)
        self.info_text.setPlaceholderText("等待魔力注入... (Waiting for file drop)")
        # 设置等宽字体以便对齐
        self.info_text.setStyleSheet("font-family: Consolas, 'Microsoft YaHei'; font-size: 10pt;")
        layout.addWidget(self.info_text)
        
        # 复制按钮
        self.btn_copy = PrimaryPushButton("📋 誊抄鉴定结果 (Copy)", self)
        self.btn_copy.clicked.connect(self.copy_report)
        layout.addWidget(self.btn_copy, 0, Qt.AlignmentFlag.AlignRight)

    def stop_worker(self):
        if hasattr(self, 'worker') and self.worker.isRunning():
            self.worker.terminate()

    def copy_report(self):
        text = self.info_text.toPlainText()
        if text:
            QApplication.clipboard().setText(text)
            InfoBar.success("誊抄完成", "鉴定报告已写入剪贴板 (Copied)", parent=self, position=InfoBarPosition.TOP)
        else:
            InfoBar.warning("空空如也", "还没有解析任何物质哦...", parent=self, position=InfoBarPosition.TOP)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.accept()
            bg_color = "#2D2023" if isDarkTheme() else "#FFF0F3" # 深色模式下使用深粉色背景
            self.drop_card.setStyleSheet(f"CardWidget {{ border: 2px dashed #FB7299; background-color: {bg_color}; }}")
        else:
            event.ignore()

    def dragLeaveEvent(self, event):
        self.drop_card.setStyleSheet("")

    def dropEvent(self, event):
        self.drop_card.setStyleSheet("")
        files = [u.toLocalFile() for u in event.mimeData().urls()]
        if files:
            self.analyze_file(files[0])

    def analyze_file(self, filepath):
        self.info_text.setText("✨ 正在解析物质构成... (Analyzing...)")
        
        self.worker = AnalysisWorker(filepath)
        self.worker.report_signal.connect(self.info_text.setText)
        self.worker.start()

# --- 个人资料界面 (观测者档案) ---
class ProfileInterface(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self.setObjectName("profileInterface")
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 30, 30, 30)
        
        # Center Card
        self.card = CardWidget(self)
        card_layout = QVBoxLayout(self.card)
        card_layout.setContentsMargins(40, 40, 40, 40)
        card_layout.setSpacing(20)
        
        # Title
        name = SubtitleLabel("泠萌404", self.card)
        name.setStyleSheet("font-size: 28px; font-weight: bold; color: #FB7299;")
        name.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        desc = BodyLabel("「 🌙 上班族 | 🎥 UP主 | 🛠️ 喜欢数码 」\n(🌙 9-to-5er | 🎥 Content Creator | 🛠️ Tech Geek)", self.card)
        desc.setAlignment(Qt.AlignmentFlag.AlignCenter)
        desc.setTextColor(QColor("#999999"), QColor("#999999"))
        
        # 版本信息
        ver = BodyLabel("Version: 1.1.0 | Author: 泠萌404", self.card)
        ver.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ver.setTextColor(QColor("#999999"), QColor("#999999"))
        
        card_layout.addStretch(1)
        
        # Avatar
        avatar_path = resource_path("LingMoe404.ico")
        if os.path.exists(avatar_path):
            # 强制加载 256x256 的高清图标，避免默认加载小尺寸导致模糊
            pixmap = QIcon(avatar_path).pixmap(256, 256)
            avatar = ImageLabel(pixmap, self.card)
            avatar.setFixedSize(100, 100)
            avatar.setBorderRadius(50, 50, 50, 50)
            avatar.scaledToWidth(100)
            
            h_avatar = QHBoxLayout()
            h_avatar.addStretch(1)
            h_avatar.addWidget(avatar)
            h_avatar.addStretch(1)
            card_layout.addLayout(h_avatar)
            card_layout.addSpacing(10)

        card_layout.addWidget(name)
        card_layout.addWidget(desc)
        card_layout.addWidget(ver)
        card_layout.addSpacing(30)
        
        # Buttons
        # Bilibili
        btn_bili = PushButton("📺 哔哩哔哩秘密基地", self.card)
        btn_bili.clicked.connect(lambda: QDesktopServices.openUrl(QUrl("https://space.bilibili.com/136850")))
        btn_bili.setFixedWidth(280)
        btn_bili.setMinimumHeight(45)
        btn_bili.setStyleSheet("PushButton { background-color: #FB7299; color: white; border: none; border-radius: 8px; font-weight: bold; font-family: 'Microsoft YaHei'; } PushButton:hover { background-color: #FF85A5; }")
        
        # Youtube
        btn_yt = PushButton("▶️ Youtube 观测站", self.card)
        btn_yt.clicked.connect(lambda: QDesktopServices.openUrl(QUrl("https://www.youtube.com/@LingMoe404")))
        btn_yt.setFixedWidth(280)
        btn_yt.setMinimumHeight(45)
        btn_yt.setStyleSheet("PushButton { background-color: #FF0000; color: white; border: none; border-radius: 8px; font-weight: bold; font-family: 'Microsoft YaHei'; } PushButton:hover { background-color: #FF4444; }")
        
        # Douyin
        btn_douyin = PushButton("🎵 抖音记录点", self.card)
        btn_douyin.clicked.connect(lambda: QDesktopServices.openUrl(QUrl("https://www.douyin.com/user/MS4wLjABAAAA8fYebaVF2xlczanlTvT-bVoRxLqNjp5Tr01pV8wM88Q")))
        btn_douyin.setFixedWidth(280)
        btn_douyin.setMinimumHeight(45)
        btn_douyin.setStyleSheet("PushButton { background-color: #1C0B1A; color: white; border: none; border-radius: 8px; font-weight: bold; font-family: 'Microsoft YaHei'; } PushButton:hover { background-color: #3D2C3B; }")

        # GitHub
        btn_github = PushButton("🐙 GitHub 异次元仓库", self.card)
        btn_github.clicked.connect(lambda: QDesktopServices.openUrl(QUrl("https://github.com/LingMoe404")))
        btn_github.setFixedWidth(280)
        btn_github.setMinimumHeight(45)
        btn_github.setStyleSheet("PushButton { background-color: #24292e; color: white; border: none; border-radius: 8px; font-weight: bold; font-family: 'Microsoft YaHei'; } PushButton:hover { background-color: #444c56; }")

        # Center buttons
        for btn in [btn_bili, btn_yt, btn_douyin, btn_github]:
            h_box = QHBoxLayout()
            h_box.addStretch(1)
            h_box.addWidget(btn)
            h_box.addStretch(1)
            card_layout.addLayout(h_box)

        card_layout.addStretch(1)
        
        layout.addWidget(self.card)

# --- 主窗口 (Win11 风格) ---
class MainWindow(FluentWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("魔法少女工坊 ")
        self.resize(1180, 780)
        self._base_min_size = QSize(1180, 780)
        self._centered_once = False
        
        # 启用 Mica 效果 (Win11 特有半透明背景)
        self.windowEffect.setMicaEffect(self.winId())
        setThemeColor('#FB7299') # Bilibili Pink / 魔法少女粉

        # 设置窗口图标 (任务栏和左上角)
        icon_path = resource_path("logo.ico")
        if os.path.exists(icon_path):
            icon = QIcon()
            # 使用 addFile 加载多分辨率图标，配合 AppUserModelID 解决模糊问题
            icon.addFile(icon_path)
            self.setWindowIcon(icon)

        # 核心变量
        self.worker = None
        self.selected_files = []
        self._drag_over_source_zone = False
        self._auto_save_blocked = False
        
        # 初始化 UI
        self.init_ui()
        self.apply_min_window_size()
        self.load_settings_to_ui()
        self.combo_encoder.currentIndexChanged.connect(self.on_encoder_changed)
        self.bind_auto_save_signals()
        
        # 欢迎语
        kaomojis = ["(｡•̀ᴗ-)✧", "(*/ω＼*)", "ヽ(✿ﾟ▽ﾟ)ノ", "(๑•̀ㅂ•́)و✧"]
        self.log(f"系统就绪... {random.choice(kaomojis)}", "info")
        
        # 启动 0.5 秒后检查结界完整性 (依赖检查)
        QTimer.singleShot(500, self.check_dependencies)

    def apply_min_window_size(self):
        """根据当前布局自动计算最小可用尺寸，避免控件挤压错位。"""
        hint = self.minimumSizeHint()
        min_w = max(self._base_min_size.width(), hint.width())
        min_h = max(self._base_min_size.height(), hint.height())
        self.setMinimumSize(min_w, min_h)
        if self.width() < min_w or self.height() < min_h:
            self.resize(max(self.width(), min_w), max(self.height(), min_h))

    def init_ui(self):
        # 主布局
        self.main_layout = QVBoxLayout()
        self.main_layout.setContentsMargins(20, 20, 20, 20)
        self.main_layout.setSpacing(15)

        # 1. 标题栏区域 + 主题切换
        header_row = QHBoxLayout()
        header_row.setSpacing(16)

        title_block = QVBoxLayout()
        title = SubtitleLabel("炼成祭坛", self)
        subtitle = BodyLabel("AV1 硬件加速魔力驱动 · 绝对领域 Edition", self)
        subtitle.setTextColor(QColor("#999999"), QColor("#999999")) # 灰色副标题
        title_block.addWidget(title)
        title_block.addWidget(subtitle)
        title_block.setSpacing(2)
        header_row.addLayout(title_block, 1)

        theme_block = QVBoxLayout()
        theme_block.setSpacing(4)
        theme_block.addWidget(StrongBodyLabel("世界线风格 (Theme)", self))
        theme_actions = QHBoxLayout()
        theme_actions.setSpacing(8)
        self.combo_theme = ComboBox(self)
        self.combo_theme.addItems(["世界线收束 (Auto)", "光之加护 (Light)", "深渊凝视 (Dark)"])
        self.combo_theme.currentIndexChanged.connect(self.on_theme_changed)
        self.combo_theme.setFixedWidth(240)
        self.combo_theme.setMinimumHeight(34)
        theme_actions.addWidget(self.combo_theme)

        self.btn_reset_conf = PushButton("↩️ 记忆回溯", self)
        self.btn_reset_conf.setMinimumHeight(34)
        self.btn_reset_conf.clicked.connect(self.restore_defaults)
        theme_actions.addWidget(self.btn_reset_conf)
        theme_block.addLayout(theme_actions)
        header_row.addLayout(theme_block)

        self.main_layout.addLayout(header_row)

        # 2. 分栏区域
        content_row = QHBoxLayout()
        content_row.setSpacing(14)
        self.column_splitter = QSplitter(Qt.Orientation.Horizontal, self)
        self.column_splitter.setChildrenCollapsible(False)
        self.column_splitter.setHandleWidth(8)
        self.column_splitter.setStyleSheet("QSplitter::handle { background: transparent; }")

        self.left_panel = QWidget(self)
        self.left_panel.setMinimumWidth(0)
        left_column = QVBoxLayout(self.left_panel)
        left_column.setContentsMargins(0, 0, 0, 0)
        left_column.setSpacing(12)

        self.right_panel = QWidget(self)
        self.right_panel.setMinimumWidth(0)
        right_column = QVBoxLayout(self.right_panel)
        right_column.setContentsMargins(0, 0, 0, 0)
        right_column.setSpacing(12)

        # 2.1 左栏卡片区域 (使用 CardWidget)
        # --- 缓存卡片 ---
        self.card_io = CardWidget(self)
        io_layout = QVBoxLayout(self.card_io)
        io_layout.setContentsMargins(18, 16, 18, 16)
        io_layout.setSpacing(12)

        # 缓存
        io_layout.addWidget(StrongBodyLabel("魔力回路缓冲 (Cache)", self.card_io))
        h2 = QHBoxLayout()
        self.line_cache = LineEdit(self.card_io)
        self.line_cache.setPlaceholderText("ab-av1 临时文件存放处...")
        self.line_cache.setFixedHeight(36)
        self.line_cache.setText(get_default_cache_dir())
        self.btn_cache = PushButton("浏览", self.card_io)
        self.btn_cache.setFixedHeight(36)
        self.btn_cache.clicked.connect(lambda: self.browse_folder(self.line_cache))
        h2.addWidget(self.line_cache)
        h2.addWidget(self.btn_cache)
        
        self.btn_clear_cache = PushButton("🧹 净化残渣", self.card_io)
        self.btn_clear_cache.setFixedHeight(36)
        self.btn_clear_cache.clicked.connect(self.clear_cache_files)
        h2.addWidget(self.btn_clear_cache)
        
        io_layout.addLayout(h2)
        left_column.addWidget(self.card_io)

        # --- 参数设置卡片 ---
        self.card_settings = CardWidget(self)
        set_layout = QVBoxLayout(self.card_settings)
        set_layout.setContentsMargins(18, 16, 18, 16)
        set_layout.setSpacing(12)
        
        # 第一行参数
        row1 = QHBoxLayout()
        row1.setSpacing(12)
        
        v1 = QVBoxLayout()
        v1.addWidget(StrongBodyLabel("魔力核心 (Encoder)", self.card_settings))
        self.combo_encoder = ComboBox(self.card_settings)
        self.combo_encoder.addItems(["Intel QSV", "NVIDIA NVENC", "AMD AMF"])
        self.combo_encoder.setMinimumHeight(36)
        v1.addWidget(self.combo_encoder)

        v2 = QVBoxLayout()
        v2.addWidget(StrongBodyLabel("视界还原度 (VMAF)", self.card_settings))
        self.line_vmaf = LineEdit(self.card_settings)
        self.line_vmaf.setMinimumHeight(36)
        v2.addWidget(self.line_vmaf)
        
        v3 = QVBoxLayout()
        v3.addWidget(StrongBodyLabel("共鸣频率 (Bitrate)", self.card_settings))
        self.line_audio = LineEdit(self.card_settings)
        self.line_audio.setMinimumHeight(36)
        v3.addWidget(self.line_audio)

        v4 = QVBoxLayout()
        v4.addWidget(StrongBodyLabel("咏唱速度 (Preset)", self.card_settings))
        self.combo_preset = ComboBox(self.card_settings)
        self.combo_preset.addItems(["1", "2", "3", "4", "5", "6", "7"])
        self.combo_preset.setMinimumHeight(36)
        v4.addWidget(self.combo_preset)

        row1.addLayout(v1, 1)
        row1.addLayout(v2, 1)
        row1.addLayout(v3, 1)
        row1.addLayout(v4, 1)
        set_layout.addLayout(row1)

        # 第二行参数
        row2 = QHBoxLayout()
        row2.setSpacing(12)

        v6 = QVBoxLayout()
        v6.addWidget(StrongBodyLabel("音量均一化术式 (Loudnorm)", self.card_settings))
        self.line_loudnorm = LineEdit(self.card_settings)
        self.line_loudnorm.setMinimumHeight(36)
        v6.addWidget(self.line_loudnorm)
        
        v7 = QVBoxLayout()
        v7.addWidget(StrongBodyLabel("NVIDIA 感知增强", self.card_settings))
        self.sw_nv_aq = SwitchButton("开启", self.card_settings)
        self.sw_nv_aq.setOnText("开启")
        self.sw_nv_aq.setOffText("关闭")
        self.sw_nv_aq.setChecked(True)
        v7.addWidget(self.sw_nv_aq)
        
        row2.addLayout(v6, 4)
        row2.addLayout(v7, 1)
        set_layout.addLayout(row2)

        left_column.addWidget(self.card_settings)

        # --- 选项与操作卡片 ---
        self.card_action = CardWidget(self)
        act_layout = QVBoxLayout(self.card_action)
        act_layout.setContentsMargins(18, 16, 18, 16)
        act_layout.setSpacing(12)

        # 保存模式 + 导出路径（与操作按钮同卡片）
        mode_layout = QVBoxLayout()
        mode_layout.setContentsMargins(0, 0, 0, 0)
        mode_layout.setSpacing(6)
        mode_layout.addWidget(StrongBodyLabel("保存模式 (Save Mode)", self.card_action))
        self.combo_save_mode = ComboBox(self.card_action)
        self.combo_save_mode.addItems([SAVE_MODE_SAVE_AS, SAVE_MODE_OVERWRITE, SAVE_MODE_REMAIN])
        self.combo_save_mode.setMinimumHeight(36)
        self.combo_save_mode.currentIndexChanged.connect(self.toggle_export_ui)
        mode_layout.addWidget(self.combo_save_mode)

        self.export_container = QWidget(self.card_action)
        exp_layout = QHBoxLayout(self.export_container)
        exp_layout.setContentsMargins(0, 0, 0, 0)
        exp_layout.setSpacing(10)
        self.line_export = LineEdit(self.export_container)
        self.line_export.setPlaceholderText("新世界坐标...")
        self.line_export.setFixedHeight(36)
        self.btn_export = PushButton("选择", self.export_container)
        self.btn_export.setFixedHeight(36)
        self.btn_export.setFixedWidth(84)
        self.btn_export.clicked.connect(lambda: self.browse_folder(self.line_export))
        exp_layout.addWidget(self.line_export)
        exp_layout.addWidget(self.btn_export)
        mode_layout.addWidget(self.export_container)
        act_layout.addLayout(mode_layout)
        # 弹性空间放在保存模式与按钮组之间，保证按钮固定贴底
        act_layout.addStretch(1)
        self.toggle_export_ui() # 初始化状态

        # 按钮组
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(10)
        self.btn_start = PrimaryPushButton("✨ 缔结契约 (Start)", self.card_action)
        self.btn_start.clicked.connect(self.start_task)
        self.btn_start.setMinimumHeight(36)
        self.btn_start.setMaximumHeight(36)
        
        self.btn_pause = PushButton("⏳ 时空冻结 (Pause)", self.card_action)
        self.btn_pause.clicked.connect(self.pause_task)
        self.btn_pause.setEnabled(False)
        self.btn_pause.setMinimumHeight(36)
        self.btn_pause.setMaximumHeight(36)
        
        self.btn_stop = PushButton(" 契约破弃 (Stop)", self.card_action)
        self.btn_stop.clicked.connect(self.stop_task)
        self.btn_stop.setEnabled(False)
        self.btn_stop.setMinimumHeight(36)
        self.btn_stop.setMaximumHeight(36)
        # 设置停止按钮为红色样式 (自定义QSS)
        self.btn_stop.setStyleSheet("PushButton { color: #D93652; font-weight: bold; } PushButton:disabled { color: #CCCCCC; }")

        btn_layout.addWidget(self.btn_start)
        btn_layout.addWidget(self.btn_pause)
        btn_layout.addWidget(self.btn_stop)
        act_layout.addLayout(btn_layout)

        left_column.addWidget(self.card_action)

        # 2.2 右栏：素材次元（选择入口）
        self.card_source = CardWidget(self)
        source_layout = QVBoxLayout(self.card_source)
        source_layout.setContentsMargins(18, 16, 18, 16)
        source_layout.setSpacing(10)
        source_layout.addWidget(StrongBodyLabel("素材次元 (Source)", self.card_source))

        source_btns = QHBoxLayout()
        source_btns.setSpacing(10)
        self.btn_src = PushButton("以文件夹之名", self.card_source)
        self.btn_src.setMinimumHeight(36)
        self.btn_src.clicked.connect(self.choose_source_folder)
        self.btn_files = PushButton("以文件之名", self.card_source)
        self.btn_files.setMinimumHeight(36)
        self.btn_files.clicked.connect(self.browse_files)
        source_btns.addWidget(self.btn_src)
        source_btns.addWidget(self.btn_files)
        source_layout.addLayout(source_btns)

        right_column.addWidget(self.card_source)
        self.sync_source_cache_card_height()

        # 2.3 右栏：已选素材列表
        self.card_selected_files = CardWidget(self)
        selected_layout = QVBoxLayout(self.card_selected_files)
        selected_layout.setContentsMargins(18, 16, 18, 16)
        selected_layout.setSpacing(8)

        selected_header = QHBoxLayout()
        selected_header.addWidget(StrongBodyLabel("次元空间 (List)", self.card_selected_files))
        selected_header.addStretch(1)
        self.lbl_selected_count_right = BodyLabel("0", self.card_selected_files)
        selected_header.addWidget(self.lbl_selected_count_right)
        selected_layout.addLayout(selected_header)

        self.lbl_selected_placeholder = DroppableBodyLabel("把元素拖拽到此处", self.card_selected_files)
        self.lbl_selected_placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_selected_placeholder.setTextColor(QColor("#FB7299"), QColor("#FB7299"))
        self.lbl_selected_placeholder.setMinimumHeight(330)
        self.lbl_selected_placeholder.filesDropped.connect(self.handle_dropped_paths)
        self.lbl_selected_placeholder.dragActiveChanged.connect(self.on_selected_zone_drag_active_changed)
        selected_layout.addWidget(self.lbl_selected_placeholder)

        self.list_selected_files = DroppableListWidget(self.card_selected_files)
        self.list_selected_files.setMinimumHeight(330)
        self.list_selected_files.setSpacing(0)
        self.list_selected_files.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.list_selected_files.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.list_selected_files.setUniformItemSizes(True)
        self.list_selected_files.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.list_selected_files.setContentsMargins(0, 0, 0, 0)
        self.list_selected_files.setViewportMargins(0, 0, 0, 0)
        if hasattr(self.list_selected_files, "setSelectionRectVisible"):
            self.list_selected_files.setSelectionRectVisible(False)
        if hasattr(self.list_selected_files, "setSelectRightClickedRow"):
            self.list_selected_files.setSelectRightClickedRow(False)
        self.list_selected_files.pressed.connect(lambda _: self.clear_selected_list_visual_state())
        self.list_selected_files.clicked.connect(lambda _: self.clear_selected_list_visual_state())
        self.list_selected_files.filesDropped.connect(self.handle_dropped_paths)
        self.list_selected_files.dragActiveChanged.connect(self.on_selected_zone_drag_active_changed)
        selected_layout.addWidget(self.list_selected_files)
        self.update_selected_count()

        right_column.addWidget(self.card_selected_files)
        self.sync_settings_selected_card_height()
        right_column.addStretch(1)

        self.column_splitter.addWidget(self.left_panel)
        self.column_splitter.addWidget(self.right_panel)
        self.column_splitter.setStretchFactor(0, 1)
        self.column_splitter.setStretchFactor(1, 1)
        self.column_splitter.setSizes([1, 1])

        content_row.addWidget(self.column_splitter, 1)
        self.main_layout.addLayout(content_row)

        # 3. 底部状态区

        # 进度条
        self.lbl_current = BodyLabel("当前咏唱:", self)
        self.pbar_current = ProgressBar(self)
        self.lbl_total = BodyLabel("总体构筑:", self)
        self.pbar_total = ProgressBar(self)
        
        self.main_layout.addWidget(self.lbl_current)
        self.main_layout.addWidget(self.pbar_current)
        self.main_layout.addWidget(self.lbl_total)
        self.main_layout.addWidget(self.pbar_total)

        # 日志
        self.text_log = TextEdit(self)
        self.text_log.setReadOnly(True)
        self.text_log.setFixedHeight(120)
        self.text_log.setStyleSheet("font-family: 'Consolas', 'Courier New', monospace; font-size: 13px;")
        self.main_layout.addWidget(self.text_log)

        # 署名
        footer = BodyLabel("Designed by <a href='https://space.bilibili.com/136850' style='color: #FB7299; text-decoration: none; font-weight: bold;'>泠萌404</a> | Powered by Python, PySide6, QFluentWidgets, FFmpeg, ab-av1, Gemini", self)
        footer.setAlignment(Qt.AlignmentFlag.AlignCenter)
        footer.setTextColor(QColor("#AAAAAA"), QColor("#AAAAAA"))
        footer.setOpenExternalLinks(True)
        self.main_layout.addWidget(footer)

        # 将主布局设置给中心部件
        w = QWidget()
        w.setObjectName("homeInterface")
        w.setLayout(self.main_layout)
        self.addSubInterface(w, FluentIcon.VIDEO, "炼成祭坛")
        
        # 添加详细信息页
        self.info_interface = MediaInfoInterface(self)
        self.addSubInterface(self.info_interface, FluentIcon.INFO, "真理之眼")
        
        # 添加个人资料页
        self.profile_interface = ProfileInterface(self)
        self.addSubInterface(self.profile_interface, FluentIcon.PEOPLE, "观测者档案")

    def showEvent(self, event):
        super().showEvent(event)
        if not self._centered_once:
            self._centered_once = True
            QTimer.singleShot(0, self.center_on_screen)
        QTimer.singleShot(0, self.equalize_columns)
        QTimer.singleShot(0, self.sync_source_cache_card_height)
        QTimer.singleShot(0, self.sync_settings_selected_card_height)
        QTimer.singleShot(0, self.update_selected_zone_border)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.equalize_columns()
        self.sync_source_cache_card_height()
        self.sync_settings_selected_card_height()

    def equalize_columns(self):
        if hasattr(self, "column_splitter") and self.column_splitter:
            total = max(self.column_splitter.width(), 2)
            half = total // 2
            self.column_splitter.setSizes([half, total - half])

    def sync_source_cache_card_height(self):
        if hasattr(self, "card_io") and hasattr(self, "card_source"):
            target = max(self.card_io.minimumSizeHint().height(), self.card_source.minimumSizeHint().height())
            self.card_io.setFixedHeight(target)
            self.card_source.setFixedHeight(target)

    def sync_settings_selected_card_height(self):
        if not (hasattr(self, "card_settings") and hasattr(self, "card_action") and hasattr(self, "card_selected_files")):
            return

        settings_min = self.card_settings.minimumSizeHint().height()
        action_min = self.card_action.minimumSizeHint().height()
        if settings_min <= 0 or action_min <= 0:
            return

        # 使用当前可见内容的建议高度进行比例分配（保存模式切换后会变化）
        settings_pref = max(settings_min, self.card_settings.sizeHint().height())
        action_pref = max(action_min, self.card_action.sizeHint().height())
        mode_text = self.combo_save_mode.currentText() if hasattr(self, "combo_save_mode") else SAVE_MODE_SAVE_AS
        # 元素覆写/元素保留模式下，操作卡片更紧凑一点
        if mode_text != SAVE_MODE_SAVE_AS:
            action_pref = max(action_min, int(action_pref * 0.48))

        left_layout = self.left_panel.layout() if hasattr(self, "left_panel") else None
        gap = left_layout.spacing() if left_layout is not None else 12
        if gap < 0:
            gap = 12

        right_h = max(self.card_selected_files.height(), self.card_selected_files.minimumSizeHint().height())
        available = max(0, right_h - gap)

        pref_sum = max(1, settings_pref + action_pref)
        action_h = int(round(available * (action_pref / pref_sum)))
        settings_h = available - action_h

        if settings_h < settings_min:
            settings_h = settings_min
            action_h = available - settings_h
        if action_h < action_min:
            action_h = action_min
            settings_h = available - action_h

        # 极端情况下（总可用高度小于两卡片最小总和）尽量回退到可显示状态
        if settings_h < settings_min or action_h < action_min:
            settings_h = settings_min
            action_h = action_min

        self.card_settings.setFixedHeight(settings_h)
        self.card_action.setFixedHeight(action_h)

    def center_on_screen(self):
        screen = self.windowHandle().screen() if self.windowHandle() else QGuiApplication.primaryScreen()
        if not screen:
            return
        screen_geo = screen.availableGeometry()
        frame_geo = self.frameGeometry()
        frame_geo.moveCenter(screen_geo.center())
        self.move(frame_geo.topLeft())

    def load_settings_to_ui(self):
        cfg_path = get_config_path()
        config = configparser.ConfigParser()
        
        data = DEFAULT_SETTINGS.copy()
        if os.path.exists(cfg_path):
            try:
                config.read(cfg_path, encoding='utf-8')
                if "Settings" in config:
                    sect = config["Settings"]
                    data["encoder"] = sect.get("encoder", DEFAULT_SETTINGS["encoder"])
                    data["vmaf"] = sect.get("vmaf", DEFAULT_SETTINGS["vmaf"])
                    data["audio_bitrate"] = sect.get("audio_bitrate", DEFAULT_SETTINGS["audio_bitrate"])
                    data["preset"] = sect.get("preset", DEFAULT_SETTINGS["preset"])
                    data["loudnorm"] = sect.get("loudnorm", DEFAULT_SETTINGS["loudnorm"])
                    data["theme"] = sect.get("theme", DEFAULT_SETTINGS["theme"])
                    data["nv_aq"] = sect.get("nv_aq", DEFAULT_SETTINGS["nv_aq"])
                    data["save_mode"] = sect.get("save_mode", DEFAULT_SETTINGS["save_mode"])
                    data["export_dir"] = sect.get("export_dir", DEFAULT_SETTINGS["export_dir"])
            except Exception:
                pass
        else:
            self.save_settings_file(DEFAULT_SETTINGS)
        
        self.line_vmaf.setText(data["vmaf"])
        self.line_audio.setText(data["audio_bitrate"])
        self.line_loudnorm.setText(data["loudnorm"])
        self.sw_nv_aq.setChecked(data.get("nv_aq", "True") == "True")
        
        # 设置 Encoder
        enc_idx = 0
        if "NVIDIA" in data["encoder"]:
            enc_idx = 1
        elif "AMD" in data["encoder"]:
            enc_idx = 2
        self.combo_encoder.setCurrentIndex(enc_idx)
        
        # 设置 ComboBox
        idx = -1
        for i in range(self.combo_preset.count()):
            if self.combo_preset.itemText(i) == data["preset"]:
                idx = i
                break
        if idx >= 0:
            self.combo_preset.setCurrentIndex(idx)
        else:
            self.combo_preset.setCurrentIndex(3)  # Default 4
        
        # 设置主题
        theme_map = {"Auto": 0, "Light": 1, "Dark": 2}
        self.combo_theme.setCurrentIndex(theme_map.get(data["theme"], 0))
        self.on_theme_changed(self.combo_theme.currentIndex()) # 确保应用

        # 设置保存模式 + 导出目录
        mode_map = {
            SAVE_MODE_SAVE_AS: 0,
            SAVE_MODE_OVERWRITE: 1,
            SAVE_MODE_REMAIN: 2
        }
        default_mode_idx = mode_map.get(DEFAULT_SETTINGS["save_mode"], 1)
        self.combo_save_mode.setCurrentIndex(mode_map.get(data["save_mode"], default_mode_idx))
        self.line_export.setText(data.get("export_dir", ""))
        self.toggle_export_ui()

    def on_encoder_changed(self, index):
        is_nv = (index == 1)
        # 切换默认 VMAF
        current_vmaf = self.line_vmaf.text()
        if is_nv:
            if current_vmaf == "93.0":
                self.line_vmaf.setText("95.0")
            self.sw_nv_aq.setEnabled(True)
        else:
            if current_vmaf == "95.0":
                self.line_vmaf.setText("93.0")
            self.sw_nv_aq.setEnabled(False)

    def bind_auto_save_signals(self):
        self.combo_encoder.currentIndexChanged.connect(lambda _: self.auto_save_settings())
        self.combo_preset.currentIndexChanged.connect(lambda _: self.auto_save_settings())
        self.combo_theme.currentIndexChanged.connect(lambda _: self.auto_save_settings())
        self.combo_save_mode.currentIndexChanged.connect(lambda _: self.auto_save_settings())
        self.sw_nv_aq.checkedChanged.connect(lambda _: self.auto_save_settings())
        self.line_vmaf.textChanged.connect(lambda _: self.auto_save_settings())
        self.line_audio.textChanged.connect(lambda _: self.auto_save_settings())
        self.line_loudnorm.textChanged.connect(lambda _: self.auto_save_settings())
        self.line_export.textChanged.connect(lambda _: self.auto_save_settings())

    def auto_save_settings(self):
        if self._auto_save_blocked:
            return
        self.save_current_settings(show_tip=False)

    def save_settings_file(self, settings_dict):
        config = configparser.ConfigParser()
        config["Settings"] = settings_dict
        with open(get_config_path(), 'w', encoding='utf-8') as f:
            config.write(f)

    def save_current_settings(self, show_tip=False):
        settings = {
            "encoder": self.combo_encoder.currentText(),
            "vmaf": self.line_vmaf.text(),
            "audio_bitrate": self.line_audio.text(),
            "preset": self.combo_preset.text(),
            "loudnorm": self.line_loudnorm.text(),
            "theme": ["Auto", "Light", "Dark"][self.combo_theme.currentIndex()],
            "nv_aq": str(self.sw_nv_aq.isChecked()),
            "save_mode": self.combo_save_mode.currentText(),
            "export_dir": self.line_export.text().strip()
        }
        self.save_settings_file(settings)
        if show_tip:
            InfoBar.success("已自动保存", "当前术式参数已写入 config.ini", parent=self, position=InfoBarPosition.TOP)

    def restore_defaults(self):
        self._auto_save_blocked = True
        self.combo_encoder.setCurrentIndex(0) # Intel QSV
        self.line_vmaf.setText(DEFAULT_SETTINGS["vmaf"])
        self.line_audio.setText(DEFAULT_SETTINGS["audio_bitrate"])
        self.line_loudnorm.setText(DEFAULT_SETTINGS["loudnorm"])
        self.sw_nv_aq.setChecked(True)
        
        idx = -1
        for i in range(self.combo_preset.count()):
            if self.combo_preset.itemText(i) == DEFAULT_SETTINGS["preset"]:
                idx = i
                break
        if idx >= 0:
            self.combo_preset.setCurrentIndex(idx)
        
        self.combo_theme.setCurrentIndex(0) # Auto
        self.combo_save_mode.setCurrentIndex(1) # Overwrite
        self.line_export.clear()
        self.toggle_export_ui()
        self._auto_save_blocked = False

        self.save_current_settings(show_tip=False)
        InfoBar.info("记忆回溯成功", "参数已重置为初始形态", parent=self, position=InfoBarPosition.TOP)
        if self.worker and self.worker.isRunning():
            InfoBar.warning("魔力核心重检已跳过", "当前正在进行炼成，停止任务后再执行记忆回溯可触发自检。", parent=self, position=InfoBarPosition.TOP)
        else:
            self.log(">>> 正在重新校准魔力核心可用性...", "info")
            QTimer.singleShot(0, self.check_dependencies)

    def on_theme_changed(self, index):
        if index == 0:
            setTheme(Theme.AUTO)
        elif index == 1:
            setTheme(Theme.LIGHT)
        elif index == 2:
            setTheme(Theme.DARK)
        setThemeColor('#FB7299') # 重新应用主题色
        # 主题切换会刷新控件样式，延迟重绘一次拖拽提示边框，防止虚线被覆盖
        QTimer.singleShot(0, self.update_selected_zone_border)
        QTimer.singleShot(120, self.update_selected_zone_border)

    def browse_folder(self, line_edit):
        folder = QFileDialog.getExistingDirectory(self, "选择文件夹")
        if folder:
            line_edit.setText(folder)

    def add_source_paths(self, paths):
        existing = set(self.selected_files)
        added = 0

        for raw in paths:
            if not raw:
                continue
            p = os.path.normpath(raw)

            if os.path.isdir(p):
                for dp, _, filenames in os.walk(p):
                    for f in filenames:
                        fp = os.path.join(dp, f)
                        if fp.lower().endswith(VIDEO_EXTS) and fp not in existing:
                            self.selected_files.append(fp)
                            existing.add(fp)
                            added += 1
            elif os.path.isfile(p):
                if p.lower().endswith(VIDEO_EXTS) and p not in existing:
                    self.selected_files.append(p)
                    existing.add(p)
                    added += 1

        if added > 0:
            self.update_selected_count()
        return added

    def handle_dropped_paths(self, paths):
        added = self.add_source_paths(paths)
        if added == 0:
            InfoBar.warning("未添加素材", "拖拽内容中没有可处理的视频文件，或已全部存在。", parent=self, position=InfoBarPosition.TOP)
        else:
            InfoBar.success("素材已加入", f"拖拽添加 {added} 个文件。", parent=self, position=InfoBarPosition.TOP)

    def clear_selected_list_visual_state(self):
        if hasattr(self, "list_selected_files"):
            self.list_selected_files.clearSelection()
            self.list_selected_files.setCurrentRow(-1)

    def on_selected_zone_drag_active_changed(self, active):
        self._drag_over_source_zone = bool(active)
        self.update_selected_zone_border()

    def update_selected_zone_border(self):
        if not hasattr(self, "lbl_selected_placeholder") or not hasattr(self, "list_selected_files"):
            return

        show_hint_border = self._drag_over_source_zone or (len(self.selected_files) == 0)
        border_css = "2px dashed rgba(251, 114, 153, 0.90)" if show_hint_border else "1px solid transparent"
        bg_css = "rgba(251, 114, 153, 0.06)" if show_hint_border else "transparent"

        self.lbl_selected_placeholder.setStyleSheet(
            f"border: {border_css}; border-radius: 10px; background: {bg_css}; padding: 8px; color: #FB7299; font-size: 18px; font-weight: 700;"
        )

        self.list_selected_files.setStyleSheet(f"""
            ListWidget {{
                background: {bg_css};
                border: {border_css};
                border-radius: 10px;
                outline: none;
            }}
            ListWidget::item {{
                background: transparent;
                border: none;
                margin: 0px;
                padding: 0px;
            }}
            ListWidget::item:hover {{
                background: transparent;
            }}
            ListWidget::item:selected {{
                background: transparent;
            }}
            QListWidget {{
                background: {bg_css};
                border: {border_css};
                border-radius: 10px;
                outline: none;
            }}
            QListWidget::item {{
                background: transparent;
                border: none;
                margin: 0px;
                padding: 0px;
            }}
            QListWidget::item:hover {{
                background: transparent;
            }}
            QListWidget::item:selected {{
                background: transparent;
            }}
        """)

    def choose_source_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "选择素材文件夹")
        if not folder:
            return
        added = self.add_source_paths([folder])
        if added == 0:
            InfoBar.warning("未发现可用文件", "该文件夹下没有可处理的视频文件。", parent=self, position=InfoBarPosition.TOP)
        else:
            InfoBar.success("素材已加入", f"已添加 {added} 个文件。", parent=self, position=InfoBarPosition.TOP)

    def browse_files(self):
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "选择视频文件",
            "",
            "Video Files (*.mkv *.mp4 *.avi *.mov *.wmv *.flv *.webm *.m4v *.ts);;All Files (*.*)"
        )
        if files:
            self.add_source_paths(files)

    def remove_selected_file(self, file_path):
        self.selected_files = [p for p in self.selected_files if p != file_path]
        self.update_selected_count()

    def update_selected_count(self):
        count = len(self.selected_files)
        if hasattr(self, 'lbl_selected_count_right'):
            self.lbl_selected_count_right.setText(str(count))

        if hasattr(self, 'lbl_selected_placeholder') and hasattr(self, 'list_selected_files'):
            is_empty = (count == 0)
            self.lbl_selected_placeholder.setVisible(is_empty)
            self.list_selected_files.setVisible(not is_empty)
            self.list_selected_files.clear()
            self.update_selected_zone_border()

            for idx, p in enumerate(self.selected_files):
                item = QListWidgetItem(self.list_selected_files)
                item.setSizeHint(QSize(0, 40))

                item_widget = QWidget(self.list_selected_files)
                container = QVBoxLayout(item_widget)
                container.setContentsMargins(0, 0, 0, 0)
                container.setSpacing(0)

                row = QWidget(item_widget)
                row.setFixedHeight(39)
                row_layout = QHBoxLayout(row)
                row_layout.setContentsMargins(10, 4, 12, 4)
                row_layout.setSpacing(8)

                name_label = BodyLabel(os.path.basename(p) or p, row)
                name_label.setToolTip(p)

                btn_remove = ClickableBodyLabel("移除", row)
                btn_remove.setCursor(Qt.CursorShape.PointingHandCursor)
                btn_remove.setStyleSheet("font-weight: 700; background: transparent;")
                btn_remove.setTextColor(QColor("#D93652"), QColor("#FF8FA1"))
                btn_remove.clicked.connect(lambda path=p: self.remove_selected_file(path))

                row_layout.addWidget(name_label)
                row_layout.addStretch(1)
                row_layout.addWidget(btn_remove)

                divider_wrap = QWidget(item_widget)
                divider_wrap.setFixedHeight(1)
                divider_layout = QHBoxLayout(divider_wrap)
                divider_layout.setContentsMargins(10, 0, 10, 0)
                divider_layout.setSpacing(0)
                divider = QFrame(divider_wrap)
                divider.setFixedHeight(1)
                divider.setFrameShape(QFrame.Shape.HLine)
                divider.setFrameShadow(QFrame.Shadow.Plain)
                if idx == count - 1:
                    divider.setStyleSheet("background-color: transparent; border: none;")
                else:
                    divider.setStyleSheet("background-color: rgba(127, 127, 127, 0.30); border: none;")
                divider_layout.addWidget(divider)

                container.addWidget(row)
                container.addWidget(divider_wrap)

                self.list_selected_files.setItemWidget(item, item_widget)

            self.clear_selected_list_visual_state()

    def toggle_export_ui(self):
        mode_text = self.combo_save_mode.currentText()
        is_save_as = (mode_text == SAVE_MODE_SAVE_AS)
        self.export_container.setVisible(is_save_as)
        # 仅刷新布局，避免强制 resize 在无边框窗口下触发异常
        self.export_container.updateGeometry()
        if self.card_action.layout():
            self.card_action.layout().activate()
        self.card_action.updateGeometry()
        self.sync_settings_selected_card_height()
        QTimer.singleShot(0, self.sync_settings_selected_card_height)

    def log(self, msg, level="info"):
        timestamp = time.strftime('%H:%M:%S')
        # 简单的 HTML 颜色格式化
        is_dark = isDarkTheme()

        # 优化深色模式下的颜色对比度
        ts_color = "#AAAAAA" if is_dark else "#888888"
        color = "#FFFFFF" if is_dark else "#000000"
        if level == "error":
            color = "#FF4E6A" if is_dark else "#C00000"
        elif level == "warning":
            color = "#FFC857" if is_dark else "#B36B00"
        elif level == "success":
            color = "#55E555" if is_dark else "#008800"
        elif level == "info":
            color = ts_color if is_dark else "#444444"
        
        html = f'<span style="color:{ts_color}">[{timestamp}]</span> <span style="color:{color}">{msg}</span>'
        self.text_log.append(html)
        # 滚动到底部
        cursor = self.text_log.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        self.text_log.setTextCursor(cursor)

    def clear_cache_files(self):
        cache_path = self.line_cache.text().strip() or get_default_cache_dir()
        if not os.path.exists(cache_path):
            os.makedirs(cache_path, exist_ok=True)
        
        try:
            count = 0
            for f in os.listdir(cache_path):
                # 仅删除看起来像临时文件的文件，避免误删
                if f.endswith(".temp.mkv"):
                    os.remove(os.path.join(cache_path, f))
                    count += 1
            InfoBar.success("净化完成", f"已清除 {count} 个魔力残渣！", parent=self, position=InfoBarPosition.TOP)
        except Exception as e:
            InfoBar.error("净化失败", str(e), parent=self, position=InfoBarPosition.TOP)

    def start_task(self):
        if not self.selected_files:
            InfoBar.warning(title="提示", content="请先选择视频源文件夹或视频文件！", orient=Qt.Orientation.Horizontal, isClosable=True, position=InfoBarPosition.TOP, parent=self)
            return

        save_mode = self.combo_save_mode.currentText()
        export_dir = self.line_export.text().strip()
        if save_mode == SAVE_MODE_SAVE_AS and not export_dir:
            InfoBar.warning("缺少导出目录", "当前是“开辟新世界 (Save As)”模式，请先选择导出文件夹。", parent=self, position=InfoBarPosition.TOP)
            return

        # 参数校验
        try:
            vmaf_val = float(self.line_vmaf.text())
        except ValueError:
            InfoBar.error("参数错误", "VMAF 必须是数字 (例如 93.0)", parent=self, position=InfoBarPosition.TOP)
            return

        config = {
            'selected_files': self.selected_files[:],
            'encoder': self.combo_encoder.currentText(),
            'export_dir': export_dir,
            'save_mode': save_mode,
            'cache_dir': self.line_cache.text().strip() or get_default_cache_dir(),
            'preset': self.combo_preset.text(),
            'vmaf': vmaf_val,
            'audio_bitrate': self.line_audio.text(),
            'loudnorm': self.line_loudnorm.text(),
            'nv_aq': self.sw_nv_aq.isChecked()
        }
        os.makedirs(config['cache_dir'], exist_ok=True)

        self.worker = EncoderWorker(config)
        self.worker.log_signal.connect(self.log)
        self.worker.progress_total_signal.connect(self.pbar_total.setValue)
        self.worker.progress_current_signal.connect(self.pbar_current.setValue)
        self.worker.finished_signal.connect(self.on_finished)
        self.worker.ask_error_decision.connect(self.on_worker_error)
        
        self.worker.start()
        
        self.btn_start.setEnabled(False)
        self.btn_start.setText("✨ 奇迹发生中...")
        self.btn_pause.setEnabled(True)
        self.combo_encoder.setEnabled(False) # 运行中禁止切换后端
        self.combo_save_mode.setEnabled(False) # 运行中禁止切换保存模式
        self.btn_pause.setText("⏳ 时空冻结 (Pause)")
        self.btn_stop.setEnabled(True)
        self.pbar_total.setValue(0)
        self.pbar_current.setValue(0)

    def on_worker_error(self, title, content):
        """ 处理转码失败时的弹窗询问 """
        dialog = MessageDialog(title, content, self)
        dialog.yesButton.setText("跳过并继续 (Skip)")
        dialog.cancelButton.setText("停止任务 (Stop)")
        
        self.error_countdown = 30
        
        def update_timer():
            self.error_countdown -= 1
            dialog.titleLabel.setText(f"{title} ({self.error_countdown}s 后自动跳过)")
            if self.error_countdown <= 0:
                timer.stop()
                dialog.accept() # 默认接受（继续）
        
        timer = QTimer(self)
        timer.timeout.connect(update_timer)
        timer.start(1000)
        
        dialog.titleLabel.setText(f"{title} ({self.error_countdown}s 后自动跳过)")
        res = dialog.exec()
        timer.stop()
        
        decision = 'continue' if res else 'stop'
        if self.worker:
            self.worker.receive_decision(decision)

    def stop_task(self):
        if self.worker:
            self.log(">>> 正在请求中止...", "error")
            self.worker.stop()
            self.btn_pause.setEnabled(False)
            self.btn_stop.setEnabled(False)

    def pause_task(self):
        if self.worker:
            if self.worker.is_paused:
                self.worker.set_paused(False)
                self.btn_pause.setText("⏳ 时空冻结 (Pause)")
                self.log(">>> 时空流动已恢复...", "info")
            else:
                self.worker.set_paused(True)
                self.btn_pause.setText("▶️ 时空流动 (Resume)")
                self.log(">>> 固有结界已冻结 (Paused)...", "info")

    def on_finished(self):
        self.btn_start.setEnabled(True)
        self.btn_start.setText("✨ 缔结契约 (Start)")
        self.btn_pause.setEnabled(False)
        self.btn_stop.setEnabled(False)
        self.combo_encoder.setEnabled(True)
        self.combo_save_mode.setEnabled(True)
        self.worker = None

    def apply_encoder_availability(self, has_qsv, has_nvenc, has_amf):
        """根据自检结果启用/禁用魔力核心选项，返回自动切换到的后端名(若发生切换)。"""
        mapping = [("Intel QSV", 0, has_qsv), ("NVIDIA NVENC", 1, has_nvenc), ("AMD AMF", 2, has_amf)]

        for _, idx, enabled in mapping:
            self.combo_encoder.setItemEnabled(idx, enabled)

        available = [(name, idx) for name, idx, enabled in mapping if enabled]
        if not available:
            self.combo_encoder.setEnabled(False)
            return None

        # 仅当当前不在任务中时允许切换/启用
        if not (self.worker and self.worker.isRunning()):
            self.combo_encoder.setEnabled(True)

        current = self.combo_encoder.currentText()
        valid_names = {name for name, _ in available}
        if current not in valid_names:
            self.combo_encoder.setCurrentIndex(available[0][1])
            return available[0][0]

        return None

    def check_dependencies(self):
        """ 启动时检查依赖组件 (二次元风格) """
        missing = []
        # 检查列表：文件名 -> 描述
        dependencies = {
            "ffmpeg.exe": "核心术式构筑 (FFmpeg)",
            "ffprobe.exe": "真理之眼组件 (FFprobe)",
            "ab-av1.exe": "极限咏唱触媒 (ab-av1)"
        }

        for exe, desc in dependencies.items():
            if not os.path.exists(tool_path(exe)):
                missing.append(f"❌ {desc} [{exe}]")

        if missing:
            title = "⚠️ 结界破损警告 (Critical Error)"
            content = (
                "呜哇！大事不好了！(>_<)\n"
                "工坊的魔力回路检测到了严重的断裂！\n\n"
                "以下核心圣遗物似乎离家出走了：\n"
                f"{chr(10).join(missing)}\n\n"
                "没有它们，炼成仪式将无法进行！\n"
                "请尽快将它们召回至工坊目录！"
            )
            
            dialog = MessageDialog(title, content, self)
            dialog.yesButton.setText("GitHub (Search)")
            dialog.cancelButton.setText("我这就去修 (OK)")
            
            if dialog.exec():
                QDesktopServices.openUrl(QUrl("https://github.com/"))
            
            # 禁用开始按钮防止报错
            self.btn_start.setEnabled(False)
            self.btn_start.setText("🚫 缺少组件")
            self.apply_encoder_availability(False, False, False)
            self.log(">>> 致命错误：关键组件缺失，系统已停摆。", "error")
        else:
            # 组件存在，进一步检查硬件兼容性
            try:
                ffmpeg_path = tool_path("ffmpeg.exe")
                
                # 1. 检查 FFmpeg 软件层面是否包含 av1_qsv 编码器
                enc_output = subprocess.check_output(
                    [ffmpeg_path, "-v", "quiet", "-encoders"], 
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0
                )
                enc_str = safe_decode(enc_output)
                
                # 2. 检查硬件层面是否支持 AV1 编码 (解决旧款 Intel 核显误报问题)
                has_qsv = False
                has_nvenc = False
                has_amf = False

                # 检测 Intel QSV (尝试硬件编码一帧)
                if "av1_qsv" in enc_str:
                    try:
                        proc = subprocess.Popen(
                            [ffmpeg_path, "-v", "error", "-init_hw_device", "qsv=hw", 
                             "-f", "lavfi", "-i", "color=black:s=1280x720", 
                             "-pix_fmt", "p010le",
                             "-c:v", "av1_qsv", "-frames:v", "1", "-f", "null", "-"],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0
                        )
                        _, stderr = proc.communicate()
                        if proc.returncode == 0:
                            has_qsv = True
                        else:
                            err_msg = safe_decode(stderr)
                            if err_msg:
                                self.log(f">>> Intel QSV 自检未通过: {err_msg.splitlines()[0]}", "error")
                    except Exception as e:
                        self.log(f">>> Intel QSV 检测异常: {e}", "error")

                # 检测 NVIDIA NVENC (尝试硬件编码一帧)
                if "av1_nvenc" in enc_str:
                    try:
                        proc = subprocess.Popen(
                            [ffmpeg_path, "-v", "error", 
                             "-f", "lavfi", "-i", "color=black:s=1280x720", 
                             "-pix_fmt", "p010le",
                             "-c:v", "av1_nvenc", "-frames:v", "1", "-f", "null", "-"],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0
                        )
                        _, stderr = proc.communicate()
                        
                        if proc.returncode == 0:
                            has_nvenc = True
                        else:
                            err_msg = safe_decode(stderr)
                            
                            # [优化] 如果是未检测到设备(CUDA_ERROR_NO_DEVICE)，直接静默跳过，不输出冗长日志
                            if "CUDA_ERROR_NO_DEVICE" in err_msg:
                                pass
                            else:
                                # 尝试 HEVC 验证显卡是否存在 (区分"无显卡"和"显卡不支持AV1")
                                proc_hevc = subprocess.Popen(
                                    [ffmpeg_path, "-v", "error", 
                                     "-f", "lavfi", "-i", "color=black:s=1280x720", 
                                     "-pix_fmt", "yuv420p",
                                     "-c:v", "hevc_nvenc", "-frames:v", "1", "-f", "null", "-"],
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0
                                )
                                proc_hevc.communicate()
                                if proc_hevc.returncode == 0:
                                    self.log(">>> 提示: 检测到 NVIDIA 显卡，但该型号不支持 AV1 硬件编码 (需 RTX 40 系列)。", "warning")
                                else:
                                    # 简化报错信息，只取第一行
                                    short_err = err_msg.split('\n')[0] if err_msg else '未知错误'
                                    self.log(f">>> NVENC 自检未通过: {short_err}", "error")
                    except Exception as e:
                        self.log(f">>> NVENC 检测异常: {e}", "error")

                # 检测 AMD AMF (尝试硬件编码一帧)
                if "av1_amf" in enc_str:
                    try:
                        proc = subprocess.Popen(
                            [ffmpeg_path, "-v", "error",
                             "-f", "lavfi", "-i", "color=black:s=1280x720",
                             "-pix_fmt", "p010le",
                             "-c:v", "av1_amf", "-usage", "transcoding",
                             "-quality", "balanced",
                             "-rc", "cqp",
                             "-qp_i", "30", "-qp_p", "30", "-qp_b", "30",
                             "-frames:v", "1", "-f", "null", "-"],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0
                        )
                        _, stderr = proc.communicate()
                        if proc.returncode == 0:
                            has_amf = True
                        else:
                            err_msg = safe_decode(stderr)
                            if err_msg:
                                short_err = err_msg.split('\n')[0]
                                self.log(f">>> AMD AMF 自检未通过: {short_err}", "warning")
                    except Exception as e:
                        self.log(f">>> AMD AMF 检测异常: {e}", "error")

                switched_to = self.apply_encoder_availability(has_qsv, has_nvenc, has_amf)

                if not has_qsv and not has_nvenc and not has_amf:
                    self.log(">>> 警告：未侦测到有效的 AV1 硬件编码器 (QSV/NVENC/AMF)。", "error")
                    InfoBar.warning("硬件不支持", "您的显卡似乎不支持 AV1 硬件编码，或者驱动未正确安装。", parent=self, position=InfoBarPosition.TOP)
                else:
                    msg = ">>> 适格者认证通过："
                    if has_qsv:
                        msg += " [Intel QSV]"
                    if has_nvenc:
                        msg += " [NVIDIA NVENC]"
                    if has_amf:
                        msg += " [AMD AMF]"
                    self.log(msg + " (Ready)", "success")
                    if switched_to:
                        self.log(f">>> 已自动切换至 {switched_to} 术式。", "info")
                    
            except Exception as e:
                self.log(f">>> 环境自检异常: {e}", "error")

    def closeEvent(self, event):
        """ [Fix] 窗口关闭时强制终止所有子进程 """
        if self.worker and self.worker.isRunning():
            self.worker.stop()
            self.worker.wait(500)
        # 清理真理之眼的分析线程
        self.info_interface.stop_worker()
        super().closeEvent(event)

if __name__ == '__main__':
    # 设置 AppUserModelID，将程序与 Python 解释器区分开，确保任务栏图标清晰且独立
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("LingMoe404.MagicWorkshop.Encoder.v1")
    except Exception:
        pass

    # 启用高分屏支持
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)

    app = QApplication(sys.argv)
    w = MainWindow()
    w.show()
    sys.exit(app.exec())
