from __future__ import annotations

import asyncio
import threading
from datetime import datetime, timezone
from pathlib import Path

from .qt_bootstrap import configure_qt_runtime

configure_qt_runtime()

from PyQt5.QtCore import Qt, QObject, QTimer, pyqtSignal
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGroupBox,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from .license_client import get_machine_code, parse_server_time, verify_license
from .models import build_task_from_folder
from .queue_store import QueueStore
from .runner import run_next_task
from .settings import (
    IMAGE_API_BASE_URL,
    IMAGE_MODEL,
    PROJECT_DIR,
    SUCAI_DIR,
    TEXT_API_BASE_URL,
    TEXT_MODEL,
    AppConfig,
    load_config,
    save_config,
)
from .updater import check_update, download_update, schedule_update_and_exit


class Signals(QObject):
    log = pyqtSignal(str)
    progress = pyqtSignal(int, str)
    refresh = pyqtSignal()
    upload_finished = pyqtSignal(str)
    upload_failed = pyqtSignal(str)
    login_finished = pyqtSignal(str)
    login_failed = pyqtSignal(str)
    generation_finished = pyqtSignal(str)
    generation_failed = pyqtSignal(str)
    all_generation_done = pyqtSignal()
    api_test_finished = pyqtSignal(str)
    api_test_failed = pyqtSignal(str)
    license_finished = pyqtSignal(str)
    license_failed = pyqtSignal(str)
    update_available = pyqtSignal(object)
    update_none = pyqtSignal(str)
    update_failed = pyqtSignal(str)
    update_downloaded = pyqtSignal(str)
    update_progress = pyqtSignal(int)


class NoWheelComboBox(QComboBox):
    """避免鼠标滚轮误改模型，只允许点击/输入选择。"""

    def wheelEvent(self, event) -> None:
        event.ignore()


class NoWheelSpinBox(QSpinBox):
    """避免鼠标滚轮误改数值，只允许点击箭头或键盘输入。"""

    def wheelEvent(self, event) -> None:
        event.ignore()


class UploadThread(threading.Thread):
    def __init__(self, signals: Signals) -> None:
        super().__init__(daemon=True)
        self.signals = signals

    def run(self) -> None:
        try:
            asyncio.run(run_next_task(dry_run=False))
            self.signals.upload_finished.emit("上传任务完成")
        except Exception as exc:
            import traceback
            error_detail = f"{exc}\n\n{traceback.format_exc()}"
            self.signals.upload_failed.emit(error_detail)


class LoginThread(threading.Thread):
    def __init__(self, signals: Signals, config: AppConfig) -> None:
        super().__init__(daemon=True)
        self.signals = signals
        self.config = config

    def run(self) -> None:
        try:
            from .login import login_blocking

            path = login_blocking(self.config)
            self.signals.login_finished.emit(str(path))
        except Exception as exc:
            self.signals.login_failed.emit(str(exc))


class GenerationThread(threading.Thread):
    def __init__(self, signals: Signals, source: str, config: AppConfig) -> None:
        super().__init__(daemon=True)
        self.signals = signals
        self.source = source
        self.config = config

    def run(self) -> None:
        try:
            from .generation_pipeline import DuplicateDramaNameSkipped, run_generation_pipeline

            output_dir = run_generation_pipeline(
                self.source,
                self.config,
                log_cb=self.signals.log.emit,
                progress_cb=self.signals.progress.emit,
            )
            self.signals.generation_finished.emit(str(output_dir))
        except DuplicateDramaNameSkipped as exc:
            self.signals.log.emit(str(exc))
            self.signals.generation_finished.emit("")
        except Exception as exc:
            self.signals.generation_failed.emit(str(exc))


class ApiTestThread(threading.Thread):
    def __init__(self, signals: Signals, kind: str, config: AppConfig) -> None:
        super().__init__(daemon=True)
        self.signals = signals
        self.kind = kind
        self.config = config

    def run(self) -> None:
        try:
            if self.kind == "text":
                self._test_text_api()
                self.signals.api_test_finished.emit("文本大模型 API 测试成功，配置已保存")
            else:
                self._test_image_api()
                self.signals.api_test_finished.emit("生图 API 测试成功，配置已保存")
        except Exception as exc:
            prefix = "文本" if self.kind == "text" else "生图"
            self.signals.api_test_failed.emit(f"{prefix}: {exc}")

    def _test_text_api(self) -> None:
        import httpx
        from openai import OpenAI

        if not self.config.volc_api_key.strip():
            raise ValueError("请先填写文本 API Key")
        if not self.config.api_base_url.strip():
            raise ValueError("请先填写文本 API 地址")

        client = OpenAI(
            api_key=self.config.volc_api_key.strip(),
            base_url=self.config.api_base_url.strip(),
            http_client=httpx.Client(trust_env=False, timeout=60),
        )
        client.chat.completions.create(
            model=self.config.text_model or TEXT_MODEL,
            messages=[{"role": "user", "content": "请只回复：ok"}],
            max_tokens=8,
            temperature=0,
        )

    def _test_image_api(self) -> None:
        import requests
        from core.ark_image import generate_ark_image, is_ark_image_api
        from core.dashscope_image import generate_dashscope_image, is_dashscope_api

        api_key = self.config.image_api_key.strip()
        if not api_key:
            raise ValueError("请先填写生图 API Key")
        api_base = self.config.image_api_base_url.strip().rstrip("/")
        if not api_base:
            raise ValueError("请先填写生图 API 地址")
        if is_ark_image_api(api_base):
            session = requests.Session()
            session.trust_env = False
            img = generate_ark_image(
                session=session,
                api_base_url=api_base,
                api_key=api_key,
                model=self.config.image_model or IMAGE_MODEL,
                prompt="生成一张白底黑点测试图。",
                size="2K",
                response_format="url",
                output_format="png",
                watermark=True,
                sequential_image_generation="disabled",
                stream=False,
                timeout=180,
            )
            save_dir = PROJECT_DIR / "debug"
            save_dir.mkdir(parents=True, exist_ok=True)
            save_path = save_dir / "gui_image_api_test.png"
            img.save(save_path)
            self.signals.api_test_finished.emit(f"生图 API 测试成功，已保存: {save_path}")
            return
        if is_dashscope_api(api_base):
            session = requests.Session()
            session.trust_env = False
            img = generate_dashscope_image(
                session=session,
                api_base_url=api_base,
                api_key=api_key,
                model=self.config.image_model or IMAGE_MODEL,
                prompt="生成一张白底黑点测试图。",
                size="1024*1024",
                n=1,
                timeout=180,
                prompt_extend=False,
                watermark=False,
            )
            save_dir = PROJECT_DIR / "debug"
            save_dir.mkdir(parents=True, exist_ok=True)
            save_path = save_dir / "gui_image_api_test.png"
            img.save(save_path)
            self.signals.api_test_finished.emit(f"生图 API 测试成功，已保存: {save_path}")
            return
        if not api_base.endswith("/v1"):
            api_base = f"{api_base}/v1"

        session = requests.Session()
        session.trust_env = False
        # 如需切回 OpenRouter，可恢复下面这段分支逻辑。
        # from core.openrouter_image import generate_openrouter_image, is_openrouter_api
        # if is_openrouter_api(api_base):
        #     generate_openrouter_image(
        #         session=session,
        #         api_base_url=api_base,
        #         api_key=api_key,
        #         model=self.config.image_model or IMAGE_MODEL,
        #         prompt="测试图片 API 是否可用，生成一张简单白底黑点小图。",
        #         aspect_ratio="1:1",
        #         image_size="1K",
        #         timeout=180,
        #     )
        #     return

        response = session.post(
            f"{api_base}/images/generations",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": self.config.image_model or IMAGE_MODEL,
                "prompt": "测试图片 API 是否可用，生成一张简单白底黑点小图。",
                "size": "1024x1024",
                "quality": "high",
                "n": 1,
                "response_format": "b64_json",
            },
            timeout=180,
        )
        if response.status_code != 200:
            raise RuntimeError(f"生图 API 返回 HTTP {response.status_code}: {response.text[:300]}")
        data = response.json()
        if not data.get("data"):
            raise RuntimeError(f"生图 API 返回数据异常: {str(data)[:300]}")
        first = data["data"][0]
        save_dir = PROJECT_DIR / "debug"
        save_dir.mkdir(parents=True, exist_ok=True)
        save_path = save_dir / "gui_image_api_test.png"
        if first.get("b64_json"):
            import base64
            from io import BytesIO
            from PIL import Image

            img_bytes = base64.b64decode(first["b64_json"])
            Image.open(BytesIO(img_bytes)).convert("RGB").save(save_path)
            self.signals.api_test_finished.emit(f"生图 API 测试成功，已保存: {save_path}")
            return
        raise RuntimeError(f"生图 API 返回数据异常: {list(first.keys())}")



class ModelFetchThread(threading.Thread):
    def __init__(self, signals: Signals, kind: str, config: AppConfig) -> None:
        super().__init__(daemon=True)
        self.signals = signals
        self.kind = kind
        self.config = config

    def run(self) -> None:
        try:
            import httpx
            from openai import OpenAI
            from core.ark_image import is_ark_image_api
            from core.dashscope_image import is_dashscope_api

            if self.kind == "text":
                api_key = self.config.volc_api_key.strip()
                base_url = self.config.api_base_url.strip()
                prefix = "文本"
            else:
                api_key = self.config.image_api_key.strip()
                base_url = self.config.image_api_base_url.strip()
                if is_ark_image_api(base_url):
                    base_url = base_url.rstrip("/")
                elif is_dashscope_api(base_url):
                    current_model = (self.config.image_model or IMAGE_MODEL).strip()
                    if not current_model:
                        raise RuntimeError("当前生图供应商暂不支持自动获取模型列表，请手动填写模型名")
                    self.signals.api_test_finished.emit(f"生图模型列表: {current_model}")
                    return
                elif base_url and not base_url.rstrip("/").endswith("/v1"):
                    base_url = f"{base_url.rstrip('/')}/v1"
                prefix = "生图"
            if not api_key:
                raise ValueError(f"请先填写{prefix} API Key")
            if not base_url:
                raise ValueError(f"请先填写{prefix} API 地址")
            client = OpenAI(
                api_key=api_key,
                base_url=base_url.rstrip("/"),
                http_client=httpx.Client(trust_env=False, timeout=60),
            )
            models = client.models.list()
            ids = [item.id for item in models.data if getattr(item, "id", "")]
            if not ids:
                raise RuntimeError("接口未返回模型列表")
            self.signals.api_test_finished.emit(f"{prefix}模型列表: {'|'.join(ids)}")
        except Exception as exc:
            prefix = "文本" if self.kind == "text" else "生图"
            self.signals.api_test_failed.emit(f"{prefix}: 获取模型列表失败: {exc}")


class UpdateCheckThread(threading.Thread):
    def __init__(self, signals: Signals, config: AppConfig) -> None:
        super().__init__(daemon=True)
        self.signals = signals
        self.config = config

    def run(self) -> None:
        try:
            info = check_update(self.config)
            if info.available:
                self.signals.update_available.emit(info)
            else:
                self.signals.update_none.emit("当前已是最新版本")
        except Exception as exc:
            self.signals.update_failed.emit(str(exc))


class UpdateDownloadThread(threading.Thread):
    def __init__(self, signals: Signals, info) -> None:
        super().__init__(daemon=True)
        self.signals = signals
        self.info = info

    def run(self) -> None:
        try:
            zip_path = download_update(self.info, progress_cb=self.signals.update_progress.emit)
            self.signals.update_downloaded.emit(str(zip_path))
        except Exception as exc:
            self.signals.update_failed.emit(str(exc))


def fill_upload_table(table: QTableWidget, store: QueueStore) -> None:
    tasks = store.load()
    table.setRowCount(len(tasks))
    for row, task in enumerate(tasks):
        values = [
            task.status,
            task.drama_name,
            task.folder,
            task.last_error,
        ]
        for col, value in enumerate(values):
            table.setItem(row, col, QTableWidgetItem(value))


class UploadQueueDialog(QDialog):
    def __init__(self, store: QueueStore, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.store = store
        self.setWindowTitle("上传队列与后台过程")
        self.setMinimumSize(980, 680)
        self.resize(1120, 760)
        self.setStyleSheet(APP_STYLE)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(12)

        title = QLabel("上传队列")
        title.setObjectName("sectionTitle")
        layout.addWidget(title)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["状态", "剧目", "文件夹", "错误"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.verticalHeader().setDefaultSectionSize(52)
        layout.addWidget(self.table, stretch=3)

        log_title = QLabel("上传过程日志")
        log_title.setObjectName("sectionTitle")
        layout.addWidget(log_title)

        self.log_text = QTextEdit()
        self.log_text.setObjectName("runLog")
        self.log_text.setReadOnly(True)
        self.log_text.setMinimumHeight(220)
        layout.addWidget(self.log_text, stretch=2)

        btn_row = QHBoxLayout()
        btn_refresh = QPushButton("刷新队列")
        btn_refresh.clicked.connect(self.refresh_table)
        btn_row.addWidget(btn_refresh)
        btn_row.addStretch()
        btn_close = QPushButton("关闭")
        btn_close.clicked.connect(self.close)
        btn_row.addWidget(btn_close)
        layout.addLayout(btn_row)

        self.refresh_table()

    def refresh_table(self) -> None:
        fill_upload_table(self.table, self.store)

    def append_log(self, message: str) -> None:
        self.log_text.append(message)


class MainWindow(QMainWindow):
    def __init__(self, license_status=None) -> None:
        super().__init__()
        self.config = load_config()
        self.license_status = license_status
        self.store = QueueStore()
        self.signals = Signals()
        self.signals.log.connect(self.append_log)
        self.signals.progress.connect(self.on_generation_progress)
        self.signals.refresh.connect(self.refresh_table)
        self.signals.upload_finished.connect(self.on_upload_finished)
        self.signals.upload_failed.connect(self.on_upload_failed)
        self.signals.login_finished.connect(self.on_login_finished)
        self.signals.login_failed.connect(self.on_login_failed)
        self.signals.generation_finished.connect(self.on_generation_finished)
        self.signals.generation_failed.connect(self.on_generation_failed)
        self.signals.all_generation_done.connect(self.on_all_generation_done)
        self.signals.api_test_finished.connect(self.on_api_test_finished)
        self.signals.api_test_failed.connect(self.on_api_test_failed)
        self.signals.license_finished.connect(self.on_license_finished)
        self.signals.license_failed.connect(self.on_license_failed)
        self.signals.update_available.connect(self.on_update_available)
        self.signals.update_none.connect(self.on_update_none)
        self.signals.update_failed.connect(self.on_update_failed)
        self.signals.update_downloaded.connect(self.on_update_downloaded)
        self.signals.update_progress.connect(self.on_update_progress)
        self.upload_thread: UploadThread | None = None
        self.login_thread: LoginThread | None = None
        self.generation_thread: GenerationThread | None = None
        self.api_test_thread: ApiTestThread | None = None
        self.update_check_thread: UpdateCheckThread | None = None
        self.update_download_thread: UpdateDownloadThread | None = None
        self.model_fetch_thread: ModelFetchThread | None = None
        self.queue_dialog: UploadQueueDialog | None = None
        self.pending_sources: list[str] = []  # 待处理的文件夹队列
        self.upload_timer = QTimer(self)
        self.upload_timer.setSingleShot(True)
        self.upload_timer.timeout.connect(self.run_next_if_idle)
        self.init_ui()
        self.refresh_table()
        self.refresh_gpu_status()
        QTimer.singleShot(1800, self.check_update_silent)

    def init_ui(self) -> None:
        self.setWindowTitle(license_title(self.license_status))
        self.resize(1580, 940)
        self.setMinimumSize(1380, 840)
        self.setStyleSheet(APP_STYLE)

        # 设置应用图标
        icon_path = PROJECT_DIR / "app_icon.png"
        if icon_path.exists():
            from PyQt5.QtGui import QIcon
            self.setWindowIcon(QIcon(str(icon_path)))

        central = QWidget()
        central.setObjectName("page")
        self.setCentralWidget(central)

        root = QVBoxLayout(central)
        root.setContentsMargins(28, 24, 28, 20)
        root.setSpacing(14)

        header = QFrame()
        header.setObjectName("appHeader")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(20, 16, 20, 16)
        header_layout.setSpacing(18)
        title_box = QVBoxLayout()
        title_box.setSpacing(4)
        self.app_title = QLabel(f"ReCreate AI  ·  {license_remaining_text(self.license_status)}")
        self.app_title.setObjectName("appTitle")
        title_box.addWidget(self.app_title)
        app_subtitle = QLabel("微短剧素材生成、成本配置表制作与视频号提审流水线")
        app_subtitle.setObjectName("appSubtitle")
        title_box.addWidget(app_subtitle)
        header_layout.addLayout(title_box, stretch=1)
        status_label = QLabel(f"素材目录  {SUCAI_DIR}")
        status_label.setObjectName("statusPill")
        header_layout.addWidget(status_label)
        self.gpu_encode_label = QLabel("GPU 检测中...")
        self.gpu_encode_label.setObjectName("statusPill")
        header_layout.addWidget(self.gpu_encode_label)
        root.addWidget(header)

        main_panel = QHBoxLayout()
        main_panel.setSpacing(18)

        # ---- 生成流程区 ----
        gen_group = QGroupBox("1. 素材与生成")
        gen_group.setMinimumWidth(760)
        gen_layout = QVBoxLayout(gen_group)
        gen_layout.setSpacing(16)

        # 源素材文件夹列表（多选）
        src_label_row = QHBoxLayout()
        source_title = QLabel("待处理素材包")
        source_title.setObjectName("sourceListTitle")
        src_label_row.addWidget(source_title)
        src_label_row.addStretch()
        btn_add_src = QPushButton("添加素材包")
        btn_add_src.setObjectName("secondaryButton")
        btn_add_src.clicked.connect(self.add_source_folders)
        src_label_row.addWidget(btn_add_src)
        btn_remove_src = QPushButton("移除选中")
        btn_remove_src.clicked.connect(self.remove_selected_source)
        src_label_row.addWidget(btn_remove_src)
        gen_layout.addLayout(src_label_row)
        self.source_list = QListWidget()
        self.source_list.setObjectName("sourceList")
        self.source_list.setMinimumHeight(82)
        self.source_list.setMaximumHeight(96)
        gen_layout.addWidget(self.source_list)

        # API 配置
        text_api_group = QGroupBox("文本大模型")
        text_api_group.setObjectName("subCard")
        text_api_layout = QGridLayout(text_api_group)
        text_api_layout.setHorizontalSpacing(10)
        text_api_layout.setVerticalSpacing(10)
        text_api_layout.setColumnMinimumWidth(0, 74)
        text_api_layout.setColumnStretch(1, 1)

        text_api_layout.addWidget(QLabel("API 地址"), 0, 0)
        self.le_api_base = QLineEdit(self.config.api_base_url or TEXT_API_BASE_URL)
        text_api_layout.addWidget(self.le_api_base, 0, 1)
        text_api_layout.addWidget(QLabel("API Key"), 1, 0)
        self.le_api_key = QLineEdit(self.config.volc_api_key)
        self.le_api_key.setEchoMode(QLineEdit.Password)
        text_api_layout.addWidget(self.le_api_key, 1, 1)
        text_api_layout.addWidget(QLabel("模型"), 2, 0)
        self.combo_text_model = NoWheelComboBox()
        self.combo_text_model.setEditable(True)
        self.combo_text_model.addItem(self.config.text_model or TEXT_MODEL)
        self.combo_text_model.setCurrentText(self.config.text_model or TEXT_MODEL)
        text_api_layout.addWidget(self.combo_text_model, 2, 1)
        text_btn_row = QHBoxLayout()
        btn_fetch_text_models = QPushButton("获取模型列表")
        btn_fetch_text_models.clicked.connect(self.fetch_text_models)
        text_btn_row.addWidget(btn_fetch_text_models, stretch=1)
        btn_test_text_api = QPushButton("测试文本并保存")
        btn_test_text_api.clicked.connect(self.test_text_api)
        text_btn_row.addWidget(btn_test_text_api, stretch=1)
        text_api_layout.addLayout(text_btn_row, 3, 1)
        self.lbl_text_api_status = QLabel("")
        self.lbl_text_api_status.setObjectName("apiStatus")
        text_api_layout.addWidget(self.lbl_text_api_status, 4, 1)

        image_api_group = QGroupBox("生图模型")
        image_api_group.setObjectName("subCard")
        image_api_layout = QGridLayout(image_api_group)
        image_api_layout.setHorizontalSpacing(10)
        image_api_layout.setVerticalSpacing(10)
        image_api_layout.setColumnMinimumWidth(0, 74)
        image_api_layout.setColumnStretch(1, 1)
        image_api_base_label = QLabel("API 地址")
        image_api_base_label.hide()
        image_api_layout.addWidget(image_api_base_label, 0, 0)
        self.le_image_api_base = QLineEdit(self.config.image_api_base_url or IMAGE_API_BASE_URL)
        self.le_image_api_base.hide()
        image_api_layout.addWidget(self.le_image_api_base, 0, 1)
        image_api_layout.setRowMinimumHeight(0, 38)
        image_api_layout.addWidget(QLabel("API Key"), 1, 0)
        self.le_image_api_key = QLineEdit(self.config.image_api_key)
        self.le_image_api_key.setEchoMode(QLineEdit.Password)
        image_api_layout.addWidget(self.le_image_api_key, 1, 1)
        image_api_layout.addWidget(QLabel("模型"), 2, 0)
        self.combo_image_model = NoWheelComboBox()
        self.combo_image_model.setEditable(True)
        for model_name in (
            self.config.image_model or IMAGE_MODEL,
            "doubao-seedream-5-0-260128",
            "qwen-image-2.0-pro",
            "qwen-image-2.0",
            "wan2.7-image-pro",
            "wan2.7-image",
            "gpt-image-2",
        ):
            if self.combo_image_model.findText(model_name) < 0:
                self.combo_image_model.addItem(model_name)
        self.combo_image_model.setCurrentText(self.config.image_model or IMAGE_MODEL)
        image_api_layout.addWidget(self.combo_image_model, 2, 1)
        image_btn_row = QHBoxLayout()
        btn_fetch_image_models = QPushButton("获取模型列表")
        btn_fetch_image_models.clicked.connect(self.fetch_image_models)
        image_btn_row.addWidget(btn_fetch_image_models, stretch=1)
        btn_test_image_api = QPushButton("测试生图并保存")
        btn_test_image_api.clicked.connect(self.test_image_api)
        image_btn_row.addWidget(btn_test_image_api, stretch=1)
        image_api_layout.addLayout(image_btn_row, 3, 1)
        self.lbl_image_api_status = QLabel("")
        self.lbl_image_api_status.setObjectName("apiStatus")
        image_api_layout.addWidget(self.lbl_image_api_status, 4, 1)

        api_cards = QHBoxLayout()
        api_cards.setSpacing(12)
        api_cards.addWidget(text_api_group, stretch=1)
        api_cards.addWidget(image_api_group, stretch=1)
        gen_layout.addLayout(api_cards)

        # 模板文件
        tpl_row = QVBoxLayout()
        tpl_docx_row = QHBoxLayout()
        tpl_docx_row.addWidget(QLabel("成本模板 docx"))
        self.le_docx_template = QLineEdit(self.config.docx_template)
        tpl_docx_row.addWidget(self.le_docx_template, stretch=1)
        btn_docx = QPushButton("浏览")
        btn_docx.clicked.connect(self.choose_docx_template)
        tpl_docx_row.addWidget(btn_docx)
        tpl_row.addLayout(tpl_docx_row)
        tpl_stamp_row = QHBoxLayout()
        tpl_stamp_row.addWidget(QLabel("印章底图"))
        self.le_stamp_image = QLineEdit(self.config.stamp_image)
        tpl_stamp_row.addWidget(self.le_stamp_image, stretch=1)
        btn_stamp = QPushButton("浏览")
        btn_stamp.clicked.connect(self.choose_stamp_image)
        tpl_stamp_row.addWidget(btn_stamp)
        tpl_row.addLayout(tpl_stamp_row)
        gen_layout.addLayout(tpl_row)

        # 进度条 + 开始按钮
        action_row = QHBoxLayout()
        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        action_row.addWidget(self.progress_bar, stretch=1)
        self.btn_generate = QPushButton("开始批量生成并上传")
        self.btn_generate.setObjectName("primaryButton")
        self.btn_generate.clicked.connect(self.start_batch_generation)
        action_row.addWidget(self.btn_generate)
        gen_layout.addLayout(action_row)

        main_panel.addWidget(gen_group, stretch=5)

        # ---- 上传设置区 ----
        upload_group = QGroupBox("2. 上传与提审")
        upload_group.setMinimumWidth(420)
        upload_layout = QVBoxLayout(upload_group)
        upload_layout.setSpacing(14)

        account_row = QHBoxLayout()
        account_row.addWidget(QLabel("登录态文件"))
        self.le_account_state = QLineEdit(self.config.account_state_path)
        account_row.addWidget(self.le_account_state, stretch=1)
        btn_state = QPushButton("浏览")
        btn_state.clicked.connect(self.choose_account_state)
        account_row.addWidget(btn_state)
        btn_login = QPushButton("扫码登录")
        btn_login.setObjectName("secondaryButton")
        btn_login.clicked.connect(self.scan_login)
        account_row.addWidget(btn_login)
        upload_layout.addLayout(account_row)

        company_row = QVBoxLayout()
        company_row.addWidget(QLabel("制作方名称"))
        self.le_company = QLineEdit(self.config.default_company_name)
        company_row.addWidget(self.le_company)
        upload_layout.addLayout(company_row)

        numbers_row = QHBoxLayout()
        trial_col = QVBoxLayout()
        trial_col.addWidget(QLabel("试看集数"))
        self.spin_trial = QSpinBox()
        self.spin_trial.setRange(1, 1000)
        self.spin_trial.setValue(self.config.default_trial_episodes)
        trial_col.addWidget(self.spin_trial)
        cost_col = QVBoxLayout()
        cost_col.addWidget(QLabel("制作成本"))
        self.spin_cost = NoWheelSpinBox()
        self.spin_cost.setRange(0, 100000)
        self.spin_cost.setValue(self.config.default_production_cost)
        cost_col.addWidget(self.spin_cost)
        interval_col = QVBoxLayout()
        interval_col.addWidget(QLabel("上传间隔"))
        self.spin_interval = QSpinBox()
        self.spin_interval.setRange(1, 1440)
        self.spin_interval.setValue(self.config.upload_interval_min)
        self.spin_interval.setSuffix(" 分钟")
        interval_col.addWidget(self.spin_interval)
        numbers_row.addLayout(trial_col)
        numbers_row.addLayout(cost_col)
        numbers_row.addLayout(interval_col)
        upload_layout.addLayout(numbers_row)

        self.ck_submit = QCheckBox("确认提审")
        self.ck_submit.setChecked(self.config.submit_after_upload)
        upload_layout.addWidget(self.ck_submit)

        upload_status_row = QHBoxLayout()
        self.lbl_upload_status = QLabel("后台上传状态：空闲")
        self.lbl_upload_status.setObjectName("uploadStatus")
        upload_status_row.addWidget(self.lbl_upload_status, stretch=1)
        btn_view_queue = QPushButton("查看上传队列")
        btn_view_queue.setObjectName("secondaryButton")
        btn_view_queue.clicked.connect(self.show_upload_queue)
        upload_status_row.addWidget(btn_view_queue)
        upload_layout.addLayout(upload_status_row)

        run_next_btn = QPushButton("上传队列下一条")
        run_next_btn.clicked.connect(self.run_next_if_idle)
        upload_layout.addWidget(run_next_btn)

        log_title = QLabel("运行日志")
        log_title.setObjectName("sectionTitle")
        upload_layout.addWidget(log_title)
        self.log_text = QTextEdit()
        self.log_text.setObjectName("runLog")
        self.log_text.setReadOnly(True)
        self.log_text.setMinimumHeight(360)
        self.log_text.setPlaceholderText("运行日志会显示在这里...")
        upload_layout.addWidget(self.log_text, stretch=1)

        main_panel.addWidget(upload_group, stretch=3)
        root.addLayout(main_panel, stretch=1)

        # ---- 底部品牌 ----
        footer = QVBoxLayout()
        footer.setSpacing(4)
        disclaimer = QLabel(
            "温馨提示：二次创作请尊重版权，使用具备合法权限的素材，"
            "相关创作责任由使用者自行承担。"
        )
        disclaimer.setStyleSheet(
            "font-size: 14px; color: #5f6b7a; font-weight: 500;"
        )
        disclaimer.setAlignment(Qt.AlignCenter)
        disclaimer.setWordWrap(True)
        footer.addWidget(disclaimer)
        root.addLayout(footer)

    # ---- 源文件夹管理 ----

    def add_source_folders(self) -> None:
        """从 sucai 目录选择多个文件夹添加到待处理列表。"""
        folders = QFileDialog.getExistingDirectory(
            self, "选择源素材文件夹", str(SUCAI_DIR),
        )
        if not folders:
            return
        # 检查选中的是单个文件夹还是包含多个子文件夹
        path = Path(folders)
        # 如果文件夹里有视频文件，说明它本身就是素材文件夹
        has_videos = any(p.suffix.lower() in {".mp4", ".mov", ".m4v"} for p in path.iterdir() if p.is_file())
        if has_videos:
            self._add_source_item(str(path))
        else:
            # 认为是包含多个素材文件夹的父目录，列出所有子文件夹
            added = 0
            for sub in sorted(path.iterdir()):
                if not sub.is_dir():
                    continue
                if sub.name.startswith("-"):
                    continue  # 跳过已生成的
                vids = any(p.suffix.lower() in {".mp4", ".mov", ".m4v"} for p in sub.iterdir() if p.is_file())
                if vids:
                    self._add_source_item(str(sub))
                    added += 1
            if added == 0:
                self.append_log(f"{path} 下没有找到含视频的子文件夹")

    def _add_source_item(self, folder: str) -> None:
        """添加到列表（去重）。"""
        existing = set()
        for i in range(self.source_list.count()):
            existing.add(self.source_list.item(i).text())
        if folder not in existing:
            self.source_list.addItem(folder)
            self.append_log(f"已添加: {Path(folder).name}")

    def remove_selected_source(self) -> None:
        """移除列表中选中的项。"""
        for item in self.source_list.selectedItems():
            self.source_list.takeItem(self.source_list.row(item))

    # ---- 文件选择对话框 ----

    def choose_docx_template(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "选择成本模板 docx", self.le_docx_template.text(), "Word (*.docx)")
        if path:
            self.le_docx_template.setText(path)

    def choose_stamp_image(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "选择印章底图", self.le_stamp_image.text(), "图片 (*.png *.jpg *.jpeg *.bmp)")
        if path:
            self.le_stamp_image.setText(path)

    def choose_account_state(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "选择登录态文件", self.le_account_state.text(), "JSON (*.json)")
        if path:
            self.le_account_state.setText(path)

    def copy_machine_code(self) -> None:
        QApplication.clipboard().setText(get_machine_code())
        self.append_log("机器码已复制")

    def ensure_license_or_warn(self) -> bool:
        self.save_defaults()
        status = verify_license(self.config)
        if status.ok:
            self.license_status = status
            self.setWindowTitle(license_title(status))
            if hasattr(self, "app_title"):
                self.app_title.setText(f"ReCreate AI  ·  {license_remaining_text(status)}")
            return True
        QMessageBox.warning(self, "授权验证失败", status.message)
        self.append_log(f"授权验证失败: {status.message}")
        return False

    # ---- 批量生成 ----

    def start_batch_generation(self) -> None:
        """开始批量生成：依次处理列表中的每个文件夹。"""
        if self.generation_thread and self.generation_thread.is_alive():
            self.append_log("生成任务正在运行")
            return
        if self.source_list.count() == 0:
            QMessageBox.warning(self, "提示", "请先添加待处理的源素材文件夹")
            return
        if not self.ensure_license_or_warn():
            return

        self.save_defaults()
        self.pending_sources = []
        for i in range(self.source_list.count()):
            self.pending_sources.append(self.source_list.item(i).text())

        try:
            from .generation_pipeline import validate_generation_inputs

            input_errors = validate_generation_inputs(self.pending_sources)
        except Exception as exc:
            self.append_log(f"导入生成模块失败: {exc}")
            QMessageBox.critical(self, "错误", f"加载生成模块失败:\n{exc}")
            return

        if input_errors:
            message = "请先补全以下文件后再开始:\n" + "\n".join(input_errors)
            self.append_log(message)
            QMessageBox.warning(self, "素材检查未通过", message)
            return

        self.source_list.clear()
        self.progress_bar.setValue(0)
        self.btn_generate.setEnabled(False)
        self._check_image_api_status()
        self._process_next_source()

    def _process_next_source(self) -> None:
        """处理下一个待生成的文件夹。"""
        if not self.pending_sources:
            self.signals.all_generation_done.emit()
            return
        source = self.pending_sources.pop(0)
        self.append_log(f"开始处理: {Path(source).name}（剩余 {len(self.pending_sources)} 个）")
        self.generation_thread = GenerationThread(self.signals, source, self.config)
        self.generation_thread.start()

    def _check_image_api_status(self) -> None:
        """生成前检测生图 API 连通性，结果写入日志。"""
        def _check():
            import requests
            from core.ark_image import generate_ark_image, is_ark_image_api
            from core.dashscope_image import generate_dashscope_image, is_dashscope_api

            api_key = (self.config.image_api_key or "").strip()
            api_base = (self.config.image_api_base_url or IMAGE_API_BASE_URL).rstrip("/")
            if not api_key:
                self.signals.log.emit("⚠️ 生图 API Key 未配置，海报生成将失败")
                return
            if is_ark_image_api(api_base):
                try:
                    session = requests.Session()
                    session.trust_env = False
                    generate_ark_image(
                        session=session,
                        api_base_url=api_base,
                        api_key=api_key,
                        model=self.config.image_model or IMAGE_MODEL,
                        prompt="生成一张白底黑点测试图。",
                        size="2K",
                        response_format="url",
                        output_format="png",
                        watermark=True,
                        sequential_image_generation="disabled",
                        stream=False,
                        timeout=60,
                    )
                    self.signals.log.emit("✅ 火山方舟生图 API 连接正常，海报和配置表将使用在线生成")
                except Exception as e:
                    self.signals.log.emit(f"⚠️ 火山方舟生图 API 无法连接: {e}，海报和配置表生成将出错")
                return
            if is_dashscope_api(api_base):
                try:
                    session = requests.Session()
                    session.trust_env = False
                    generate_dashscope_image(
                        session=session,
                        api_base_url=api_base,
                        api_key=api_key,
                        model=self.config.image_model or IMAGE_MODEL,
                        prompt="生成一张白底黑点测试图。",
                        size="1024*1024",
                        n=1,
                        timeout=60,
                        prompt_extend=False,
                        watermark=False,
                    )
                    self.signals.log.emit("✅ 阿里云百炼生图 API 连接正常，海报和配置表将使用在线生成")
                except Exception as e:
                    self.signals.log.emit(f"⚠️ 阿里云百炼生图 API 无法连接: {e}，海报和配置表生成将出错")
                return
            if not api_base.endswith("/v1"):
                api_base = f"{api_base}/v1"
            try:
                session = requests.Session()
                session.trust_env = False
                # 如需切回 OpenRouter，可恢复下面这段分支逻辑。
                # from core.openrouter_image import generate_openrouter_image, is_openrouter_api
                # if is_openrouter_api(api_base):
                #     generate_openrouter_image(
                #         session=session,
                #         api_base_url=api_base,
                #         api_key=api_key,
                #         model=self.config.image_model or IMAGE_MODEL,
                #         prompt="test",
                #         aspect_ratio="1:1",
                #         image_size="1K",
                #         timeout=30,
                #     )
                #     self.signals.log.emit("✅ OpenRouter 生图 API 连接正常，海报将使用在线生成")
                #     return

                response = session.post(
                    f"{api_base}/images/generations",
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": self.config.image_model or IMAGE_MODEL,
                        "prompt": "test",
                        "size": "256x256",
                        "n": 1,
                        "response_format": "b64_json",
                    },
                    timeout=30,
                )
                if response.status_code == 200:
                    self.signals.log.emit("✅ 生图 API 连接正常，海报将使用在线生成")
                else:
                    self.signals.log.emit(f"⚠️ 生图 API 连接失败 (HTTP {response.status_code})，海报生成将出错")
            except Exception as e:
                self.signals.log.emit(f"⚠️ 生图 API 无法连接: {e}，海报生成将出错")
        import threading
        threading.Thread(target=_check, daemon=True).start()

    def on_generation_finished(self, output_dir: str) -> None:
        """单个文件夹生成完成 → 加入上传队列 → 处理下一个。"""
        if not output_dir:
            self.append_log("已跳过重复剧目，继续处理下一个素材文件夹")
            self._process_next_source()
            return
        try:
            task = build_task_from_folder(output_dir, **self.current_task_args())
            task = self.store.add(task)
            self.append_log(f"生成完成并加入队列: {task.drama_name}")
            self.refresh_table()
        except Exception as exc:
            self.append_log(f"生成完成，但加入队列失败: {exc}")
        self._process_next_source()

    def on_generation_failed(self, msg: str) -> None:
        """单个文件夹生成失败 → 继续处理下一个。"""
        self.append_log(f"生成失败: {msg}")
        self._process_next_source()

    def on_all_generation_done(self) -> None:
        """所有文件夹处理完毕 → 自动开始上传。"""
        self.btn_generate.setEnabled(True)
        self.progress_bar.setValue(100)
        pending_count = sum(1 for t in self.store.load() if t.status == "pending")
        self.append_log(f"全部生成完毕，队列中有 {pending_count} 个待上传任务")
        if pending_count > 0:
            self.run_next_if_idle()

    # ---- 扫码登录 ----

    def scan_login(self) -> None:
        if self.login_thread and self.login_thread.is_alive():
            self.append_log("扫码登录窗口已经打开")
            return
        self.save_defaults()
        self.login_thread = LoginThread(self.signals, self.config)
        self.login_thread.start()
        self.append_log("正在打开扫码登录窗口")

    # ---- 上传 ----

    def run_next_if_idle(self) -> None:
        if self.upload_thread and self.upload_thread.is_alive():
            self.append_log("已有上传任务正在运行")
            return
        if not self.store.next_pending():
            self.append_log("没有待上传任务")
            return
        if not self.ensure_license_or_warn():
            return
        self.save_defaults()
        self.upload_thread = UploadThread(self.signals)
        self.upload_thread.start()
        self.append_log("开始上传下一条任务")
        self.refresh_table()
        self.update_upload_status()

    # ---- API 测试 ----

    def test_text_api(self) -> None:
        if self.api_test_thread and self.api_test_thread.is_alive():
            self.append_log("已有 API 测试正在运行")
            return
        self.save_defaults()
        self.lbl_text_api_status.setText("测试中...")
        self.lbl_text_api_status.setProperty("state", "running")
        self.lbl_text_api_status.style().unpolish(self.lbl_text_api_status)
        self.lbl_text_api_status.style().polish(self.lbl_text_api_status)
        self.api_test_thread = ApiTestThread(self.signals, "text", self.config)
        self.api_test_thread.start()

    def test_image_api(self) -> None:
        if self.api_test_thread and self.api_test_thread.is_alive():
            self.append_log("已有 API 测试正在运行")
            return
        self.save_defaults()
        self.lbl_image_api_status.setText("测试中...")
        self.lbl_image_api_status.setProperty("state", "running")
        self.lbl_image_api_status.style().unpolish(self.lbl_image_api_status)
        self.lbl_image_api_status.style().polish(self.lbl_image_api_status)
        self.api_test_thread = ApiTestThread(self.signals, "image", self.config)
        self.api_test_thread.start()

    def fetch_text_models(self) -> None:
        if self.model_fetch_thread and self.model_fetch_thread.is_alive():
            self.append_log("已有模型列表获取正在运行")
            return
        self.save_defaults()
        self.lbl_text_api_status.setText("正在获取模型列表...")
        self.lbl_text_api_status.setProperty("state", "running")
        self.lbl_text_api_status.style().unpolish(self.lbl_text_api_status)
        self.lbl_text_api_status.style().polish(self.lbl_text_api_status)
        self.model_fetch_thread = ModelFetchThread(self.signals, "text", self.config)
        self.model_fetch_thread.start()

    def fetch_image_models(self) -> None:
        if self.model_fetch_thread and self.model_fetch_thread.is_alive():
            self.append_log("已有模型列表获取正在运行")
            return
        self.save_defaults()
        self.lbl_image_api_status.setText("正在获取模型列表...")
        self.lbl_image_api_status.setProperty("state", "running")
        self.lbl_image_api_status.style().unpolish(self.lbl_image_api_status)
        self.lbl_image_api_status.style().polish(self.lbl_image_api_status)
        self.model_fetch_thread = ModelFetchThread(self.signals, "image", self.config)
        self.model_fetch_thread.start()

    def on_api_test_finished(self, msg: str) -> None:
        if msg.startswith("文本模型列表:"):
            self._populate_model_combo(self.combo_text_model, msg.split(":", 1)[1].split("|"))
            self.lbl_text_api_status.setText(f"已获取 {self.combo_text_model.count()} 个模型")
            self.lbl_text_api_status.setProperty("state", "ok")
            self.lbl_text_api_status.style().unpolish(self.lbl_text_api_status)
            self.lbl_text_api_status.style().polish(self.lbl_text_api_status)
            self.append_log(f"已获取文本模型列表: {self.combo_text_model.count()} 个")
            return
        if msg.startswith("生图模型列表:"):
            self._populate_model_combo(self.combo_image_model, msg.split(":", 1)[1].split("|"))
            self.lbl_image_api_status.setText(f"已获取 {self.combo_image_model.count()} 个模型")
            self.lbl_image_api_status.setProperty("state", "ok")
            self.lbl_image_api_status.style().unpolish(self.lbl_image_api_status)
            self.lbl_image_api_status.style().polish(self.lbl_image_api_status)
            self.append_log(f"已获取生图模型列表: {self.combo_image_model.count()} 个")
            return
        if "文本" in msg:
            self.lbl_text_api_status.setText("可用，已保存")
            self.lbl_text_api_status.setProperty("state", "ok")
            self.lbl_text_api_status.style().unpolish(self.lbl_text_api_status)
            self.lbl_text_api_status.style().polish(self.lbl_text_api_status)
        else:
            self.lbl_image_api_status.setText("可用，已保存")
            self.lbl_image_api_status.setProperty("state", "ok")
            self.lbl_image_api_status.style().unpolish(self.lbl_image_api_status)
            self.lbl_image_api_status.style().polish(self.lbl_image_api_status)
        self.append_log(msg)

    def _populate_model_combo(self, combo: QComboBox, models: list[str]) -> None:
        current = combo.currentText().strip()
        combo.blockSignals(True)
        combo.clear()
        for model in models:
            model = model.strip()
            if model:
                combo.addItem(model)
        if current:
            combo.setCurrentText(current)
        combo.blockSignals(False)

    def on_api_test_failed(self, msg: str) -> None:
        target = self.lbl_text_api_status if msg.startswith("文本:") else self.lbl_image_api_status
        target.setText("测试失败")
        target.setProperty("state", "error")
        target.style().unpolish(target)
        target.style().polish(target)
        self.append_log(f"API 测试失败: {msg}")

    def on_license_finished(self, msg: str) -> None:
        self.append_log(f"授权验证成功: {msg}")

    def on_license_failed(self, msg: str) -> None:
        self.append_log(f"授权验证失败: {msg}")

    # ---- 软件更新 ----

    def check_update_silent(self) -> None:
        if self.update_check_thread and self.update_check_thread.is_alive():
            return
        self.save_defaults()
        self.update_check_thread = UpdateCheckThread(self.signals, self.config)
        self.update_check_thread.start()

    def on_update_available(self, info) -> None:
        notes = info.notes.strip() or "服务器已发布新版本。"
        msg = (
            f"发现新版本 {info.latest_version}\n"
            f"当前版本 {info.current_version}\n\n"
            f"更新内容:\n{notes}\n\n"
            "是否立即更新？"
        )
        reply = QMessageBox.question(
            self,
            "发现新版本",
            msg,
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes if info.force else QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            self.append_log(f"发现新版本 {info.latest_version}，用户暂不更新")
            return
        self.append_log(f"开始下载新版本 {info.latest_version}")
        self.progress_bar.setValue(0)
        self.update_download_thread = UpdateDownloadThread(self.signals, info)
        self.update_download_thread.start()

    def on_update_none(self, msg: str) -> None:
        self.append_log(msg)

    def on_update_failed(self, msg: str) -> None:
        self.append_log(f"更新检查失败: {msg}")

    def on_update_progress(self, value: int) -> None:
        self.progress_bar.setValue(value)

    def on_update_downloaded(self, zip_path: str) -> None:
        self.append_log(f"更新包下载完成: {zip_path}")
        QMessageBox.information(self, "准备更新", "更新包已下载完成。软件将关闭并自动安装新版本。")
        schedule_update_and_exit(Path(zip_path))

    # ---- 队列/日志 ----

    def current_task_args(self) -> dict[str, object]:
        return {
            "company_name": self.le_company.text().strip() or self.config.default_company_name,
            "trial_episodes": self.spin_trial.value(),
            "production_cost": self.spin_cost.value(),
            "submit_after_upload": self.ck_submit.isChecked(),
        }

    def save_defaults(self) -> None:
        self.config.account_state_path = self.le_account_state.text().strip()
        self.config.volc_api_key = self.le_api_key.text().strip()
        self.config.api_base_url = self.le_api_base.text().strip() or TEXT_API_BASE_URL
        self.config.image_api_key = self.le_image_api_key.text().strip()
        self.config.image_api_base_url = self.le_image_api_base.text().strip() or IMAGE_API_BASE_URL
        self.config.text_model = self.combo_text_model.currentText().strip() or TEXT_MODEL
        self.config.image_model = self.combo_image_model.currentText().strip() or IMAGE_MODEL
        self.config.docx_template = self.le_docx_template.text().strip()
        self.config.stamp_image = self.le_stamp_image.text().strip()
        self.config.default_company_name = self.le_company.text().strip()
        self.config.default_trial_episodes = self.spin_trial.value()
        self.config.default_production_cost = self.spin_cost.value()
        self.config.submit_after_upload = self.ck_submit.isChecked()
        self.config.upload_interval_min = self.spin_interval.value()
        save_config(self.config)

    def refresh_table(self) -> None:
        tasks = self.store.load()
        if self.queue_dialog:
            self.queue_dialog.refresh_table()
        self.update_upload_status(tasks)

    def update_upload_status(self, tasks=None) -> None:
        tasks = tasks if tasks is not None else self.store.load()
        uploading_count = sum(1 for task in tasks if task.status == "uploading")
        pending_count = sum(1 for task in tasks if task.status == "pending")
        failed_count = sum(1 for task in tasks if task.status == "failed")
        success_count = sum(1 for task in tasks if task.status == "success")
        if self.upload_thread and self.upload_thread.is_alive():
            status = "后台上传中"
        elif self.upload_timer.isActive():
            status = "等待下一次上传"
        elif pending_count:
            status = f"待上传 {pending_count} 个"
        else:
            status = "空闲"
        self.lbl_upload_status.setText(
            f"后台上传状态：{status}  |  成功 {success_count} / 失败 {failed_count} / 待传 {pending_count} / 上传中 {uploading_count}"
        )

    def on_upload_finished(self, msg: str) -> None:
        self.append_log(msg)
        self.refresh_table()
        # 上传完一个，按间隔时间等下一个
        if self.store.next_pending():
            interval_min = self.spin_interval.value()
            self.append_log(f"等待 {interval_min} 分钟后上传下一条...")
            self.upload_timer.start(interval_min * 60 * 1000)
        self.update_upload_status()

    def on_upload_failed(self, msg: str) -> None:
        self.append_log(f"上传失败: {msg[:500]}")
        self.btn_generate.setEnabled(True)
        self.refresh_table()
        # 失败了也继续下一条
        if self.store.next_pending():
            interval_min = self.spin_interval.value()
            self.append_log(f"等待 {interval_min} 分钟后上传下一条...")
            self.upload_timer.start(interval_min * 60 * 1000)
        self.update_upload_status()

    def on_generation_progress(self, value: int, text: str) -> None:
        self.progress_bar.setValue(value)
        self.append_log(text)

    def on_login_finished(self, path: str) -> None:
        self.le_account_state.setText(path)
        self.config.account_state_path = path
        save_config(self.config)
        self.append_log(f"扫码登录成功，登录态已保存: {path}")

    def on_login_failed(self, msg: str) -> None:
        self.append_log(f"扫码登录失败: {msg}")

    def show_upload_queue(self) -> None:
        if self.queue_dialog is None:
            self.queue_dialog = UploadQueueDialog(self.store, self)
            self.queue_dialog.destroyed.connect(lambda: setattr(self, "queue_dialog", None))
        self.queue_dialog.refresh_table()
        self.queue_dialog.show()
        self.queue_dialog.raise_()
        self.queue_dialog.activateWindow()

    def refresh_gpu_status(self) -> None:
        """检测 GPU 编码器并在 header 中显示。"""
        try:
            from core.video_processor import get_gpu_encoder_label

            label = get_gpu_encoder_label()
        except Exception:
            label = None
        if label:
            self.gpu_encode_label.setText(f"GPU 加速  {label}")
        else:
            self.gpu_encode_label.setText("GPU 未检测到")

    def append_log(self, message: str) -> None:
        self.log_text.append(message)
        if self.queue_dialog:
            self.queue_dialog.append_log(message)


def main() -> None:
    app = QApplication([])
    app.setFont(QFont("Microsoft YaHei UI", 10))
    config = load_config()
    dialog = LicenseDialog(config)
    if dialog.exec_() != QDialog.Accepted:
        return
    window = MainWindow(dialog.license_status)
    window.show()
    app.exec_()


APP_STYLE = """
QWidget#central {
    background: #eef3f8;
}
QWidget#page {
    background: #eef3f8;
}
QFrame#appHeader {
    background: #111827;
    border: 1px solid #1f2937;
    border-radius: 12px;
}
QLabel#appTitle {
    color: #ffffff;
    font-size: 28px;
    font-weight: 800;
}
QLabel#appSubtitle {
    color: #b9c3d2;
    font-size: 14px;
    font-weight: 500;
}
QLabel#statusPill {
    background: #182235;
    color: #d6e0ee;
    border: 1px solid #2d3b51;
    border-radius: 14px;
    padding: 7px 12px;
    font-size: 12px;
}
QLabel#sectionTitle {
    color: #172033;
    font-size: 16px;
    font-weight: 800;
}
QLabel#dialogTitle {
    color: #172033;
    font-size: 22px;
    font-weight: 800;
}
QLabel#dialogHint {
    color: #64748b;
    font-size: 13px;
    font-weight: 600;
}
QGroupBox {
    background: #ffffff;
    border: 1px solid #d7e0eb;
    border-radius: 12px;
    margin-top: 18px;
    padding: 20px 18px 18px 18px;
    font-size: 16px;
    font-weight: 800;
    color: #172033;
}
QGroupBox#subCard {
    background: #f8fbff;
    border: 1px solid #dce6f2;
    border-radius: 10px;
    margin-top: 14px;
    padding: 16px 14px 14px 14px;
    font-size: 15px;
}
QGroupBox#subCard::title {
    background: #f8fbff;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 14px;
    padding: 0 8px;
    background: #ffffff;
}
QLabel {
    color: #2c3a4f;
    font-size: 14px;
    font-weight: 600;
}
QLabel#sourceListTitle {
    color: #172033;
    font-size: 15px;
    font-weight: 800;
}
QLabel#uploadStatus {
    background: #eef6ff;
    border: 1px solid #bfdbfe;
    border-radius: 8px;
    color: #1e3a8a;
    font-size: 14px;
    font-weight: 800;
    padding: 8px 10px;
}
QLineEdit, QComboBox, QSpinBox, QListWidget, QTextEdit {
    background: #fbfdff;
    border: 1px solid #cad5e4;
    border-radius: 8px;
    min-height: 38px;
    padding: 6px 10px;
    color: #172033;
    font-size: 15px;
    selection-background-color: #2563eb;
}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QListWidget:focus, QTextEdit:focus {
    border: 1px solid #1f6feb;
    background: #ffffff;
}
QLineEdit#licenseInput {
    min-height: 46px;
    font-size: 18px;
    font-family: Consolas, "Microsoft YaHei UI";
    letter-spacing: 0.5px;
    padding: 8px 12px;
}
QLabel#apiStatus {
    color: #64748b;
    font-size: 13px;
    font-weight: 700;
}
QLabel#apiStatus[state="running"] {
    color: #2563eb;
}
QLabel#apiStatus[state="ok"] {
    color: #059669;
}
QLabel#apiStatus[state="error"] {
    color: #dc2626;
}
QListWidget {
    padding: 8px;
}
QListWidget#sourceList {
    font-size: 15px;
    padding: 5px 8px;
}
QListWidget::item {
    border-radius: 6px;
    padding: 7px 8px;
}
QListWidget#sourceList::item {
    padding: 5px 8px;
}
QListWidget::item:selected {
    background: #dbeafe;
    color: #172033;
}
QPushButton {
    background: #ffffff;
    color: #172033;
    border: 1px solid #c9d4e3;
    border-radius: 8px;
    min-height: 36px;
    padding: 6px 14px;
    font-weight: 700;
    font-size: 15px;
}
QPushButton:hover {
    background: #f3f7fc;
    border-color: #8fb2e8;
}
QPushButton:pressed {
    background: #e5edf8;
}
QPushButton#primaryButton {
    background: #1f6feb;
    color: #ffffff;
    border: 1px solid #1f6feb;
    min-height: 38px;
    padding-left: 24px;
    padding-right: 24px;
}
QPushButton#primaryButton:hover {
    background: #1b5fd0;
}
QPushButton#secondaryButton {
    color: #0f766e;
    border-color: #8bd0c7;
    background: #ecfdf9;
}
QCheckBox {
    color: #243044;
    font-size: 14px;
    font-weight: 700;
    spacing: 6px;
}
QProgressBar {
    border: 1px solid #cad5e4;
    border-radius: 8px;
    background: #e8eef6;
    height: 18px;
    text-align: center;
    color: #172033;
    font-weight: 700;
}
QProgressBar::chunk {
    border-radius: 7px;
    background: #14b8a6;
}
QTableWidget {
    background: #ffffff;
    alternate-background-color: #f8fbff;
    border: 1px solid #d7e0eb;
    border-radius: 10px;
    gridline-color: #e8eef6;
    selection-background-color: #dbeafe;
    selection-color: #172033;
    font-size: 16px;
}
QHeaderView::section {
    background: #f1f5f9;
    color: #172033;
    border: 0;
    border-right: 1px solid #d7e0eb;
    padding: 12px 10px;
    font-weight: 800;
    font-size: 17px;
}
QTableWidget::item {
    padding: 10px 8px;
}
QTextEdit {
    color: #dce7f5;
    background: #111827;
    border: 1px solid #263244;
    border-radius: 10px;
    font-family: Consolas, "Microsoft YaHei UI";
    font-size: 15px;
}
QTextEdit#runLog:focus {
    color: #dce7f5;
    background: #111827;
    border: 1px solid #3b82f6;
}
"""


if __name__ == "__main__":
    main()
