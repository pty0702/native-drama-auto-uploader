from __future__ import annotations

import asyncio
import threading
from pathlib import Path

from .qt_bootstrap import configure_qt_runtime

configure_qt_runtime()

from PyQt5.QtCore import QObject, QTimer, pyqtSignal
from PyQt5.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
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

from .models import build_task_from_folder
from .queue_store import QueueStore
from .runner import run_next_task
from .settings import AppConfig, load_config, save_config


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


class UploadThread(threading.Thread):
    def __init__(self, signals: Signals) -> None:
        super().__init__(daemon=True)
        self.signals = signals

    def run(self) -> None:
        try:
            asyncio.run(run_next_task(dry_run=False))
            self.signals.upload_finished.emit("上传任务完成")
        except Exception as exc:
            self.signals.upload_failed.emit(str(exc))


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
            from .generation_pipeline import run_generation_pipeline

            output_dir = run_generation_pipeline(
                self.source,
                self.config,
                log_cb=self.signals.log.emit,
                progress_cb=self.signals.progress.emit,
            )
            self.signals.generation_finished.emit(str(output_dir))
        except Exception as exc:
            self.signals.generation_failed.emit(str(exc))


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.config = load_config()
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
        self.upload_thread: UploadThread | None = None
        self.login_thread: LoginThread | None = None
        self.generation_thread: GenerationThread | None = None
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.run_next_if_idle)
        self.init_ui()
        self.refresh_table()

    def init_ui(self) -> None:
        self.setWindowTitle("微信视频号短剧上传队列")
        self.resize(1050, 720)

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)

        gen_group = QGroupBox("短剧生成流程")
        gen_layout = QVBoxLayout(gen_group)

        gen_row1 = QHBoxLayout()
        gen_row1.addWidget(QLabel("源素材文件夹"))
        self.le_source = QLineEdit()
        gen_row1.addWidget(self.le_source)
        btn_source = QPushButton("选择")
        btn_source.clicked.connect(self.choose_source)
        gen_row1.addWidget(btn_source)
        gen_layout.addLayout(gen_row1)

        gen_row2 = QHBoxLayout()
        gen_row2.addWidget(QLabel("输出目录"))
        self.le_output = QLineEdit(self.config.output_dir)
        gen_row2.addWidget(self.le_output)
        btn_output = QPushButton("选择")
        btn_output.clicked.connect(self.choose_output)
        gen_row2.addWidget(btn_output)
        gen_layout.addLayout(gen_row2)

        gen_row3 = QHBoxLayout()
        gen_row3.addWidget(QLabel("API Key"))
        self.le_api_key = QLineEdit(self.config.volc_api_key)
        self.le_api_key.setEchoMode(QLineEdit.Password)
        gen_row3.addWidget(self.le_api_key)
        gen_row3.addWidget(QLabel("API 地址"))
        self.le_api_base = QLineEdit(self.config.api_base_url)
        gen_row3.addWidget(self.le_api_base)
        gen_layout.addLayout(gen_row3)

        gen_row4 = QHBoxLayout()
        gen_row4.addWidget(QLabel("文本模型"))
        self.cb_text_model = QComboBox()
        self.cb_text_model.addItems([
            "doubao-seed-2-0-lite-260428",
            "doubao-seed-2-0-mini-260428",
            "doubao-seed-1-6-flash-250615",
            "doubao-1-5-pro-32k-250115",
            "deepseek-v4-flash-260425",
            "gpt-5.2",
        ])
        index = self.cb_text_model.findText(self.config.text_model)
        if index >= 0:
            self.cb_text_model.setCurrentIndex(index)
        gen_row4.addWidget(self.cb_text_model)
        gen_row4.addWidget(QLabel("图像模型"))
        self.cb_image_model = QComboBox()
        self.cb_image_model.addItems([
            "doubao-seedream-5-0-260128",
            "gpt-image-2",
            "doubao-seedream-4-5-251128",
            "doubao-seedream-4-0-250828",
        ])
        image_index = self.cb_image_model.findText(self.config.image_model)
        if image_index >= 0:
            self.cb_image_model.setCurrentIndex(image_index)
        gen_row4.addWidget(self.cb_image_model)
        gen_layout.addLayout(gen_row4)

        gen_row5 = QHBoxLayout()
        gen_row5.addWidget(QLabel("成本模板原图"))
        self.le_template_image = QLineEdit(self.config.template_image)
        gen_row5.addWidget(self.le_template_image)
        btn_template = QPushButton("选择")
        btn_template.clicked.connect(self.choose_template_image)
        gen_row5.addWidget(btn_template)
        self.btn_generate = QPushButton("开始生成并加入队列")
        self.btn_generate.clicked.connect(self.start_generation)
        gen_row5.addWidget(self.btn_generate)
        gen_layout.addLayout(gen_row5)

        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        gen_layout.addWidget(self.progress_bar)
        root.addWidget(gen_group)

        row1 = QHBoxLayout()
        row1.addWidget(QLabel("监控根目录"))
        self.le_watch_root = QLineEdit(self.config.watch_root)
        row1.addWidget(self.le_watch_root)
        btn_root = QPushButton("选择")
        btn_root.clicked.connect(self.choose_watch_root)
        row1.addWidget(btn_root)
        btn_scan = QPushButton("扫描加入队列")
        btn_scan.clicked.connect(self.scan_root)
        row1.addWidget(btn_scan)
        root.addLayout(row1)

        row_login = QHBoxLayout()
        row_login.addWidget(QLabel("登录态文件"))
        self.le_account_state = QLineEdit(self.config.account_state_path)
        row_login.addWidget(self.le_account_state)
        btn_state = QPushButton("选择")
        btn_state.clicked.connect(self.choose_account_state)
        row_login.addWidget(btn_state)
        btn_login = QPushButton("扫码登录")
        btn_login.clicked.connect(self.scan_login)
        row_login.addWidget(btn_login)
        root.addLayout(row_login)

        row2 = QHBoxLayout()
        row2.addWidget(QLabel("成品文件夹"))
        self.le_folder = QLineEdit()
        row2.addWidget(self.le_folder)
        btn_folder = QPushButton("选择")
        btn_folder.clicked.connect(self.choose_folder)
        row2.addWidget(btn_folder)
        btn_add = QPushButton("加入队列")
        btn_add.clicked.connect(self.add_folder)
        row2.addWidget(btn_add)
        root.addLayout(row2)

        row3 = QHBoxLayout()
        row3.addWidget(QLabel("制作方名称"))
        self.le_company = QLineEdit(self.config.default_company_name)
        row3.addWidget(self.le_company)
        btn_default_company = QPushButton("设为默认")
        btn_default_company.clicked.connect(self.save_defaults)
        row3.addWidget(btn_default_company)

        row3.addWidget(QLabel("试看集数"))
        self.spin_trial = QSpinBox()
        self.spin_trial.setRange(1, 1000)
        self.spin_trial.setValue(self.config.default_trial_episodes)
        row3.addWidget(self.spin_trial)

        row3.addWidget(QLabel("制作成本"))
        self.spin_cost = QSpinBox()
        self.spin_cost.setRange(0, 100000)
        self.spin_cost.setValue(self.config.default_production_cost)
        row3.addWidget(self.spin_cost)

        self.ck_submit = QCheckBox("上传完成后确认提审")
        self.ck_submit.setChecked(self.config.submit_after_upload)
        row3.addWidget(self.ck_submit)
        root.addLayout(row3)

        row4 = QHBoxLayout()
        row4.addWidget(QLabel("定时检查间隔(分钟)"))
        self.spin_interval = QSpinBox()
        self.spin_interval.setRange(1, 1440)
        self.spin_interval.setValue(self.config.upload_interval_min)
        row4.addWidget(self.spin_interval)
        self.btn_timer = QPushButton("启动定时上传")
        self.btn_timer.clicked.connect(self.toggle_timer)
        row4.addWidget(self.btn_timer)
        self.btn_run = QPushButton("立即上传下一条")
        self.btn_run.clicked.connect(self.run_next_if_idle)
        row4.addWidget(self.btn_run)
        root.addLayout(row4)

        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels(["状态", "剧目", "集数", "试看", "成本", "制作方", "文件夹", "错误"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        root.addWidget(self.table)

        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setMaximumHeight(180)
        root.addWidget(self.log_text)

    def choose_watch_root(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "选择监控根目录", self.le_watch_root.text())
        if path:
            self.le_watch_root.setText(path)

    def choose_source(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "选择源素材文件夹", self.le_watch_root.text())
        if path:
            self.le_source.setText(path)

    def choose_output(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "选择输出目录", self.le_output.text())
        if path:
            self.le_output.setText(path)

    def choose_template_image(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择成本模板原图",
            self.le_template_image.text(),
            "图片 (*.png *.jpg *.jpeg *.bmp)",
        )
        if path:
            self.le_template_image.setText(path)

    def choose_folder(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "选择成品文件夹", self.le_watch_root.text())
        if path:
            self.le_folder.setText(path)

    def choose_account_state(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "选择登录态文件", self.le_account_state.text(), "JSON (*.json)")
        if path:
            self.le_account_state.setText(path)

    def scan_login(self) -> None:
        if self.login_thread and self.login_thread.is_alive():
            self.append_log("扫码登录窗口已经打开")
            return
        self.save_defaults()
        self.login_thread = LoginThread(self.signals, self.config)
        self.login_thread.start()
        self.append_log("正在打开扫码登录窗口")

    def start_generation(self) -> None:
        if self.generation_thread and self.generation_thread.is_alive():
            self.append_log("生成任务正在运行")
            return
        source = self.le_source.text().strip()
        if not source:
            QMessageBox.warning(self, "提示", "请选择源素材文件夹")
            return
        self.save_defaults()
        self.progress_bar.setValue(0)
        self.btn_generate.setEnabled(False)
        self.generation_thread = GenerationThread(self.signals, source, self.config)
        self.generation_thread.start()
        self.append_log("开始生成短剧成品")

    def current_task_args(self) -> dict[str, object]:
        return {
            "company_name": self.le_company.text().strip() or self.config.default_company_name,
            "trial_episodes": self.spin_trial.value(),
            "production_cost": self.spin_cost.value(),
            "submit_after_upload": self.ck_submit.isChecked(),
        }

    def add_folder(self) -> None:
        folder = self.le_folder.text().strip()
        if not folder:
            QMessageBox.warning(self, "提示", "请选择成品文件夹")
            return
        try:
            task = build_task_from_folder(folder, **self.current_task_args())
            task = self.store.add(task)
            self.append_log(f"加入队列: {task.drama_name}")
            self.refresh_table()
        except Exception as exc:
            QMessageBox.warning(self, "加入失败", str(exc))

    def scan_root(self) -> None:
        root = Path(self.le_watch_root.text().strip())
        if not root.exists():
            QMessageBox.warning(self, "提示", "监控根目录不存在")
            return
        added = 0
        for folder in sorted([p for p in root.iterdir() if p.is_dir()], key=lambda p: p.name):
            try:
                before = len(self.store.load())
                self.store.add(build_task_from_folder(folder, **self.current_task_args()))
                if len(self.store.load()) > before:
                    added += 1
            except Exception:
                continue
        self.append_log(f"扫描完成，新增 {added} 个任务")
        self.refresh_table()

    def save_defaults(self) -> None:
        self.config.watch_root = self.le_watch_root.text().strip()
        self.config.account_state_path = self.le_account_state.text().strip()
        self.config.output_dir = self.le_output.text().strip()
        self.config.volc_api_key = self.le_api_key.text().strip()
        self.config.api_base_url = self.le_api_base.text().strip()
        self.config.text_model = self.cb_text_model.currentText()
        self.config.image_model = self.cb_image_model.currentText()
        self.config.template_image = self.le_template_image.text().strip()
        self.config.default_company_name = self.le_company.text().strip()
        self.config.default_trial_episodes = self.spin_trial.value()
        self.config.default_production_cost = self.spin_cost.value()
        self.config.upload_interval_min = self.spin_interval.value()
        self.config.submit_after_upload = self.ck_submit.isChecked()
        save_config(self.config)
        self.append_log("默认配置已保存")

    def toggle_timer(self) -> None:
        if self.timer.isActive():
            self.timer.stop()
            self.btn_timer.setText("启动定时上传")
            self.append_log("定时上传已停止")
            return
        self.save_defaults()
        self.timer.start(self.spin_interval.value() * 60 * 1000)
        self.btn_timer.setText("停止定时上传")
        self.append_log("定时上传已启动")

    def run_next_if_idle(self) -> None:
        if self.upload_thread and self.upload_thread.is_alive():
            self.append_log("已有上传任务正在运行")
            return
        if not self.store.next_pending():
            self.append_log("没有待上传任务")
            return
        self.save_defaults()
        self.upload_thread = UploadThread(self.signals)
        self.upload_thread.start()
        self.append_log("开始上传下一条任务")
        self.refresh_table()

    def refresh_table(self) -> None:
        tasks = self.store.load()
        self.table.setRowCount(len(tasks))
        for row, task in enumerate(tasks):
            values = [
                task.status,
                task.drama_name,
                str(task.episode_count),
                str(task.trial_episodes),
                str(task.production_cost),
                task.company_name,
                task.folder,
                task.last_error,
            ]
            for col, value in enumerate(values):
                self.table.setItem(row, col, QTableWidgetItem(value))

    def on_upload_finished(self, msg: str) -> None:
        self.append_log(msg)
        self.refresh_table()

    def on_upload_failed(self, msg: str) -> None:
        self.append_log(f"上传失败: {msg}")
        self.refresh_table()

    def on_generation_progress(self, value: int, text: str) -> None:
        self.progress_bar.setValue(value)
        self.append_log(text)

    def on_generation_finished(self, output_dir: str) -> None:
        self.btn_generate.setEnabled(True)
        self.le_folder.setText(output_dir)
        try:
            task = build_task_from_folder(output_dir, **self.current_task_args())
            task = self.store.add(task)
            self.append_log(f"生成完成并加入队列: {task.drama_name}")
            self.refresh_table()
        except Exception as exc:
            self.append_log(f"生成完成，但加入队列失败: {exc}")

    def on_generation_failed(self, msg: str) -> None:
        self.btn_generate.setEnabled(True)
        self.append_log(f"生成失败: {msg}")

    def on_login_finished(self, path: str) -> None:
        self.le_account_state.setText(path)
        self.config.account_state_path = path
        save_config(self.config)
        self.append_log(f"扫码登录成功，登录态已保存: {path}")

    def on_login_failed(self, msg: str) -> None:
        self.append_log(f"扫码登录失败: {msg}")

    def append_log(self, message: str) -> None:
        self.log_text.append(message)


def main() -> None:
    app = QApplication([])
    window = MainWindow()
    window.show()
    app.exec_()


if __name__ == "__main__":
    main()
