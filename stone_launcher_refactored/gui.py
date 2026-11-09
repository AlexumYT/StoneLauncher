import os
import sys
import threading
import time
import webbrowser
from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QPushButton, QLabel, QProgressBar,
    QHBoxLayout, QFrame, QGridLayout, QSystemTrayIcon, QMenu, QMessageBox
)
from PyQt6.QtGui import QPixmap, QIcon, QAction
from PyQt6.QtCore import pyqtSignal, Qt, QTimer, QThread
from minecraft_launcher_lib.microsoft_account import get_secure_login_data, parse_auth_code_url, complete_login
from .account_service import refresh_saved_account
from .config import APP_VERSION, CLIENT_ID, CLIENT_SECRET, MANIFEST_URL, REDIRECT_URI
from .discord_presence import discord_rpc
from .launch_thread import LaunchThread
from .minecraft_service import safe_get
from .oauth_server import oauth_server
from .storage import AccountStore, PreferencesStore
from .updater import apply_update
from .versions import (
    ALLOWED_VERSIONS,
    UNSUPPORTED_VERSIONS,
    VERSION_BG_COLORS,
    normalize_version_id,
    version_label,
)
from .widgets import PulseDot, resource_path

class MicrosoftLoginThread(QThread):
    status = pyqtSignal(str)
    succeeded = pyqtSignal(dict)
    failed = pyqtSignal(str)

    def run(self) -> None:
        try:
            oauth_server.reset()
            login_url, state, code_verifier = get_secure_login_data(
                CLIENT_ID,
                REDIRECT_URI,
            )

            self.status.emit("Waiting for Microsoft login…")
            webbrowser.open(login_url)

            callback_url = oauth_server.wait_for_callback(timeout=300.0)
            if callback_url is None:
                raise TimeoutError("Microsoft sign-in timed out after 5 minutes.")

            self.status.emit("Finishing Microsoft sign-in…")
            auth_code = parse_auth_code_url(callback_url, state)
            login_data = complete_login(
                CLIENT_ID,
                CLIENT_SECRET,
                REDIRECT_URI,
                auth_code,
                code_verifier,
            )

            self.succeeded.emit({
                "username": login_data["name"],
                "uuid": login_data["id"],
                "access_token": login_data["access_token"],
                "refresh_token": login_data.get("refresh_token"),
            })
        except Exception as error:
            self.failed.emit(str(error) or type(error).__name__)


class LauncherGUI(QWidget):
    restart_signal    = pyqtSignal(str)
    do_restart_signal = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Stone Launcher")
        self.setFixedSize(860, 560)
        self.account_info       = None
        self.release_versions   = []
        self._version_buttons   = {}
        self._allow_close        = False
        self._minecraft_running  = False
        self.login_thread: MicrosoftLoginThread | None = None

        prefs = PreferencesStore.load()
        self.selected_version_id = normalize_version_id(prefs["version"])
        self.shaders_enabled     = prefs["shaders"]
        if self.selected_version_id != prefs["version"]:
            PreferencesStore.save(self.selected_version_id, self.shaders_enabled)

        self._build_ui()
        self._setup_tray()
        self._restore_session()
        self.load_versions()
        self.update_ui_state()

        # Start RPC after Qt has entered its event loop. A second update is sent
        # shortly afterward because some Discord clients ignore the first frame.
        QTimer.singleShot(500, self._start_discord_presence)
        QTimer.singleShot(5000, self._refresh_discord_presence)

    def _start_discord_presence(self) -> None:
        discord_rpc.connect(force=True)
        discord_rpc.show_launcher(version_label(self.selected_version_id))

    def _refresh_discord_presence(self) -> None:
        discord_rpc.show_launcher(version_label(self.selected_version_id))

    def closeEvent(self, event) -> None:
        if self._allow_close:
            discord_rpc.close()
            event.accept()
            return

        event.ignore()
        self.hide()
        if self.tray_icon.isVisible():
            self.tray_icon.showMessage(
                "Stone Launcher",
                "Stone Launcher is still running in the system tray.",
                QSystemTrayIcon.MessageIcon.Information,
                2500,
            )

    def _setup_tray(self) -> None:
        icon = self.windowIcon()
        if icon.isNull():
            pixmap = QPixmap(resource_path("img/logo.png"))
            if not pixmap.isNull():
                icon = QIcon(pixmap)
        self.tray_icon = QSystemTrayIcon(icon, self)
        self.tray_icon.setToolTip("Stone Launcher")

        tray_menu = QMenu(self)
        open_action = QAction("Open Stone Launcher", self)
        open_action.triggered.connect(self.restore_from_tray)
        tray_menu.addAction(open_action)

        tray_menu.addSeparator()
        exit_action = QAction("Exit", self)
        exit_action.triggered.connect(self.exit_application)
        tray_menu.addAction(exit_action)

        self.tray_icon.setContextMenu(tray_menu)
        self.tray_icon.activated.connect(self._on_tray_activated)
        self.tray_icon.show()

    def _on_tray_activated(self, reason) -> None:
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self.restore_from_tray()

    def restore_from_tray(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def exit_application(self) -> None:
        self._allow_close = True
        self.tray_icon.hide()
        discord_rpc.close()
        QApplication.instance().quit()

    def _on_minecraft_started(self) -> None:
        self._minecraft_running = True
        self.update_status("Minecraft is running")
        self.hide()

    def _on_minecraft_stopped(self) -> None:
        self._minecraft_running = False
        self.launch_button.setEnabled(self.account_info is not None)
        self.update_status("Minecraft closed")
        self.restore_from_tray()

    def _on_launch_thread_finished(self) -> None:
        if not self._minecraft_running:
            self.launch_button.setEnabled(self.account_info is not None)

    # ── UI construction ─────────────────────────────────────────────────────
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Top bar
        topbar = QFrame()
        topbar.setFixedHeight(56)
        topbar.setObjectName("topbar")
        tb = QHBoxLayout(topbar)
        tb.setContentsMargins(20, 0, 20, 0)

        logo_label = QLabel()
        logo_label.setObjectName("logoLabel")
        px = QPixmap(resource_path("img/logo.png"))
        if not px.isNull():
            logo_label.setPixmap(px.scaled(28, 28, Qt.AspectRatioMode.KeepAspectRatio,
                                            Qt.TransformationMode.SmoothTransformation))
        else:
            logo_label.setText("⛏")
            logo_label.setStyleSheet("font-size: 20px;")
        tb.addWidget(logo_label)

        title = QLabel("STONE  LAUNCHER")
        title.setObjectName("titleLabel")
        tb.addWidget(title)
        tb.addStretch()

        self.tab_play = QPushButton("PLAY")
        self.tab_play.setObjectName("navTabActive")
        self.tab_play.setCursor(Qt.CursorShape.PointingHandCursor)
        self.tab_play.clicked.connect(lambda: self._show_screen("main"))
        tb.addWidget(self.tab_play)

        self.tab_versions = QPushButton("VERSIONS")
        self.tab_versions.setObjectName("navTab")
        self.tab_versions.setCursor(Qt.CursorShape.PointingHandCursor)
        self.tab_versions.clicked.connect(lambda: self._show_screen("versions"))
        tb.addWidget(self.tab_versions)

        tb.addSpacing(20)
        ver_label = QLabel(f"v{APP_VERSION}")
        ver_label.setObjectName("verLabel")
        tb.addWidget(ver_label)

        root.addWidget(topbar)

        # Content area
        content = QFrame()
        content.setObjectName("contentArea")
        self.content_layout = QVBoxLayout(content)
        self.content_layout.setContentsMargins(0, 0, 0, 0)
        self.content_layout.setSpacing(0)
        root.addWidget(content, 1)

        self.main_screen     = self._build_main_screen()
        self.versions_screen = self._build_versions_screen()
        self.content_layout.addWidget(self.main_screen)
        self.content_layout.addWidget(self.versions_screen)
        self.versions_screen.hide()

        self._apply_stylesheet()

        self.restart_signal.connect(self.update_status)
        self.do_restart_signal.connect(self.trigger_restart)

        if self.selected_version_id in self._version_buttons:
            self._version_buttons[self.selected_version_id].setChecked(True)

    def _show_screen(self, name: str) -> None:
        is_main = (name == "main")
        self.main_screen.setVisible(is_main)
        self.versions_screen.setVisible(not is_main)
        self.tab_play.setObjectName("navTabActive" if is_main else "navTab")
        self.tab_versions.setObjectName("navTab" if is_main else "navTabActive")
        self._apply_stylesheet()

    def _build_main_screen(self) -> QFrame:
        screen = QFrame()
        screen.setObjectName("mainScreen")
        layout = QHBoxLayout(screen)
        layout.setContentsMargins(32, 24, 32, 24)
        layout.setSpacing(24)

        # Left column
        left = QVBoxLayout()
        left.setSpacing(10)

        acct_card = QFrame()
        acct_card.setObjectName("card")
        acct_l = QVBoxLayout(acct_card)
        acct_l.setContentsMargins(14, 12, 14, 12)
        acct_l.setSpacing(6)

        acct_top = QHBoxLayout()
        self.pulse_dot = PulseDot()
        acct_top.addWidget(self.pulse_dot)
        acct_top.addSpacing(6)
        hdr = QLabel("ACCOUNT")
        hdr.setObjectName("cardHeader")
        acct_top.addWidget(hdr)
        acct_top.addStretch()
        acct_l.addLayout(acct_top)

        self.account_label = QLabel("Not signed in")
        self.account_label.setObjectName("accountName")
        self.account_label.setWordWrap(True)
        acct_l.addWidget(self.account_label)

        self.login_button = QPushButton("Sign in with Microsoft")
        self.login_button.setObjectName("secondaryBtn")
        self.login_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.login_button.clicked.connect(self.toggle_login_logout)
        acct_l.addWidget(self.login_button)

        left.addWidget(acct_card)

        disc_card = QFrame()
        disc_card.setObjectName("card")
        disc_l = QVBoxLayout(disc_card)
        disc_l.setContentsMargins(14, 12, 14, 12)
        disc_l.setSpacing(4)

        disc_hdr = QLabel("POWERED BY")
        disc_hdr.setObjectName("cardHeader")
        disc_l.addWidget(disc_hdr)

        disc_text = QLabel(
            "WorldHost Open-source mod.\n"
            "A custom client is in development.\n"
            "Support the original project too!"
        )
        disc_text.setObjectName("discText")
        disc_text.setWordWrap(True)
        disc_l.addWidget(disc_text)

        left.addWidget(disc_card)
        left.addStretch()
        layout.addLayout(left, 3)

        # Right column
        right = QVBoxLayout()
        right.setSpacing(12)

        status_card = QFrame()
        status_card.setObjectName("card")
        status_l = QVBoxLayout(status_card)
        status_l.setContentsMargins(14, 12, 14, 12)
        status_l.setSpacing(8)

        status_hdr = QLabel("STATUS")
        status_hdr.setObjectName("cardHeader")
        status_l.addWidget(status_hdr)

        self.status_label = QLabel("Ready")
        self.status_label.setObjectName("statusText")
        self.status_label.setWordWrap(True)
        self.status_label.setMinimumHeight(36)
        status_l.addWidget(self.status_label)

        self.progress_bar = QProgressBar()
        self.progress_bar.setObjectName("progressBar")
        self.progress_bar.setFixedHeight(6)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setValue(0)
        status_l.addWidget(self.progress_bar)

        right.addWidget(status_card)
        right.addStretch()

        ver_row = QHBoxLayout()
        ver_lbl = QLabel("VERSION")
        ver_lbl.setObjectName("cardHeader")
        ver_row.addWidget(ver_lbl)

        self.selected_ver_label = QLabel(version_label(self.selected_version_id))
        self.selected_ver_label.setObjectName("selectedVerBadge")
        ver_row.addWidget(self.selected_ver_label)

        change_btn = QPushButton("Change")
        change_btn.setObjectName("secondaryBtn")
        change_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        change_btn.clicked.connect(lambda: self._show_screen("versions"))
        ver_row.addWidget(change_btn)
        ver_row.addStretch()
        right.addLayout(ver_row)

        self.launch_button = QPushButton("▶  LAUNCH")
        self.launch_button.setObjectName("launchBtn")
        self.launch_button.setFixedHeight(54)
        self.launch_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.launch_button.clicked.connect(self.start_launch)
        self.launch_button.setEnabled(False)
        right.addWidget(self.launch_button)

        layout.addLayout(right, 4)
        return screen

    def _build_versions_screen(self) -> QFrame:
        screen = QFrame()
        screen.setObjectName("versionsScreen")
        outer = QVBoxLayout(screen)
        outer.setContentsMargins(32, 20, 32, 20)
        outer.setSpacing(16)

        hdr = QLabel("SELECT VERSION")
        hdr.setObjectName("versionsHeader")
        hdr.setAlignment(Qt.AlignmentFlag.AlignCenter)
        outer.addWidget(hdr)

        sup_widget = QFrame()
        sup_widget.setObjectName("transparent")
        sup_grid = QGridLayout(sup_widget)
        sup_grid.setSpacing(14)
        sup_grid.setContentsMargins(0, 0, 0, 0)
        for i, v in enumerate(ALLOWED_VERSIONS):
            sup_grid.addWidget(self._make_version_card(v, unsupported=False), i // 3, i % 3)
        outer.addWidget(sup_widget)

        # Divider
        div_row = QHBoxLayout()
        div_row.setSpacing(10)
        line_l = QFrame()
        line_l.setFrameShape(QFrame.Shape.HLine)
        line_l.setObjectName("dividerLine")
        div_row.addWidget(line_l, 1)
        unsup_lbl = QLabel("NOT FULLY SUPPORTED")
        unsup_lbl.setObjectName("unsupportedHeader")
        div_row.addWidget(unsup_lbl)
        line_r = QFrame()
        line_r.setFrameShape(QFrame.Shape.HLine)
        line_r.setObjectName("dividerLine")
        div_row.addWidget(line_r, 1)
        outer.addLayout(div_row)

        unsup_widget = QFrame()
        unsup_widget.setObjectName("transparent")
        unsup_grid = QGridLayout(unsup_widget)
        unsup_grid.setSpacing(14)
        unsup_grid.setContentsMargins(0, 0, 0, 0)
        for col in range(3):
            unsup_grid.setColumnStretch(col, 1)
        for i, v in enumerate(UNSUPPORTED_VERSIONS):
            unsup_grid.addWidget(self._make_version_card(v, unsupported=True), i // 3, i % 3)
        outer.addWidget(unsup_widget)
        outer.addStretch()

        # Shaders toggle
        shaders_row = QHBoxLayout()
        shaders_row.setSpacing(10)
        shaders_row.setContentsMargins(4, 4, 0, 0)

        self.shaders_checkbox = QPushButton(
            "☑  Add Shaders (Iris)" if self.shaders_enabled else "☐  Add Shaders (Iris)"
        )
        self.shaders_checkbox.setObjectName("shadersToggle")
        self.shaders_checkbox.setCheckable(True)
        self.shaders_checkbox.setChecked(self.shaders_enabled)
        self.shaders_checkbox.setCursor(Qt.CursorShape.PointingHandCursor)
        self.shaders_checkbox.clicked.connect(self._toggle_shaders)
        shaders_row.addWidget(self.shaders_checkbox)

        shaders_note = QLabel("Iris Shaders will be downloaded on next launch (Only Fabric)")
        shaders_note.setObjectName("shadersNote")
        shaders_row.addWidget(shaders_note)
        shaders_row.addStretch()
        outer.addLayout(shaders_row)
        outer.addStretch()

        return screen

    def _make_version_card(self, v: dict, unsupported: bool = False) -> QPushButton:
        vid    = v["id"]
        colors = VERSION_BG_COLORS.get(vid, ("#1a2a3a", "#0d1520"))
        border_default = "rgba(255,255,255,0.05)" if unsupported else "rgba(255,255,255,0.08)"
        border_hover   = "rgba(255,255,255,0.15)" if unsupported else "rgba(255,255,255,0.25)"

        btn = QPushButton()
        btn.setObjectName("versionCard")
        btn.setFixedHeight(100)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setCheckable(True)
        btn.setChecked(vid == self.selected_version_id)

        btn_layout = QVBoxLayout(btn)
        btn_layout.setContentsMargins(12, 10, 12, 10)
        btn_layout.setSpacing(4)

        tags = v.get("tags", [])
        if tags:
            tags_row = QHBoxLayout()
            tags_row.setSpacing(5)
            tags_row.setContentsMargins(0, 0, 0, 0)
            for tag in tags:
                tag_lbl = QLabel(tag)
                tag_lbl.setObjectName("versionTagMuted" if unsupported else "versionTag")
                tags_row.addWidget(tag_lbl)
            tags_row.addStretch()
            btn_layout.addLayout(tags_row)
        else:
            btn_layout.addSpacing(18)

        ver_lbl = QLabel(f"VERSION\n{v['label']}")
        ver_lbl.setObjectName("versionCardLabelMuted" if unsupported else "versionCardLabel")
        btn_layout.addWidget(ver_lbl)

        btn.setStyleSheet(f"""
            QPushButton#versionCard {{
                background: qlineargradient(x1:0,y1:0,x2:1,y2:1,
                    stop:0 {colors[0]}, stop:1 {colors[1]});
                border: 2px solid {border_default};
                border-radius: 10px;
                text-align: left;
            }}
            QPushButton#versionCard:hover  {{ border: 2px solid {border_hover}; }}
            QPushButton#versionCard:checked {{ border: 2px solid #00e5a0; }}
        """)

        btn.clicked.connect(lambda _checked, v_id=vid: self._select_version(v_id))
        self._version_buttons[vid] = btn
        return btn

    def _select_version(self, version_id: str) -> None:
        version_id = normalize_version_id(version_id)
        self.selected_version_id = version_id
        for vid, btn in self._version_buttons.items():
            btn.setChecked(vid == version_id)
        self.selected_ver_label.setText(version_label(version_id))
        PreferencesStore.save(self.selected_version_id, self.shaders_enabled)
        discord_rpc.show_launcher(version_label(version_id))
        self._show_screen("main")

    def _apply_stylesheet(self) -> None:
        self.setStyleSheet("""
            QWidget {
                background: transparent;
                color: #d0d8e8;
                font-family: "Segoe UI", "Helvetica Neue", sans-serif;
                font-size: 14px;
            }
            LauncherGUI {
                background: qlineargradient(
                    x1:0 y1:0, x2:1 y2:1,
                    stop:0 #0b0e14, stop:0.5 #0f1520, stop:1 #0a1118);
            }
            #topbar {
                background: rgba(0,0,0,0.4);
                border-bottom: 1px solid rgba(255,255,255,0.07);
            }
            #titleLabel {
                font-size: 15px; font-weight: 700;
                letter-spacing: 0.22em; color: #e8eef8; margin-left: 10px;
            }
            #verLabel { font-size: 11px; color: #3a7fd5; letter-spacing: 0.08em; }
            #navTab {
                background: transparent; border: none;
                border-bottom: 2px solid transparent;
                color: #6a7a90; font-size: 12px; font-weight: 700;
                letter-spacing: 0.12em; padding: 4px 14px;
            }
            #navTab:hover { color: #a0b0c8; }
            #navTabActive {
                background: transparent; border: none;
                border-bottom: 2px solid #00e5a0;
                color: #e8eef8; font-size: 12px; font-weight: 700;
                letter-spacing: 0.12em; padding: 4px 14px;
            }
            #card {
                background: rgba(255,255,255,0.04);
                border: 1px solid rgba(255,255,255,0.08);
                border-radius: 10px;
            }
            #cardHeader {
                font-size: 10px; font-weight: 700;
                letter-spacing: 0.18em; color: #3a7fd5;
            }
            #accountName { font-size: 15px; font-weight: 600; color: #e0e8f8; }
            #statusText  { font-size: 13px; color: #a8b8d0; min-height: 40px; }
            #discText    { font-size: 11px; color: #6a7a90; line-height: 1.5; }
            #progressBar {
                background: rgba(255,255,255,0.06);
                border: none; border-radius: 3px;
            }
            #progressBar::chunk {
                background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
                    stop:0 #1e5db5, stop:1 #00e5a0);
                border-radius: 3px;
            }
            #selectedVerBadge {
                background: rgba(0,229,160,0.15);
                border: 1px solid rgba(0,229,160,0.4);
                border-radius: 6px; padding: 2px 10px;
                color: #00e5a0; font-size: 13px; font-weight: 700;
            }
            #versionsHeader {
                font-size: 13px; font-weight: 700;
                letter-spacing: 0.25em; color: #6a7a90;
            }
            #versionCardLabel {
                font-size: 16px; font-weight: 800;
                letter-spacing: 0.05em; color: #e8eef8; line-height: 1.2;
            }
            #versionTag {
                font-size: 9px; font-weight: 700; letter-spacing: 0.15em;
                color: #00e5a0; background: rgba(0,229,160,0.15);
                border-radius: 3px; padding: 1px 5px;
            }
            #unsupportedHeader {
                font-size: 10px; font-weight: 700;
                letter-spacing: 0.20em; color: #4a5a6a;
            }
            #dividerLine { color: rgba(255,255,255,0.07); }
            #versionCardLabelMuted {
                font-size: 16px; font-weight: 800;
                letter-spacing: 0.05em; color: #6a7a8a; line-height: 1.2;
            }
            #versionTagMuted {
                font-size: 9px; font-weight: 700; letter-spacing: 0.15em;
                color: #4a6a5a; background: rgba(0,229,160,0.07);
                border-radius: 3px; padding: 1px 5px;
            }
            #shadersToggle {
                background: rgba(255,255,255,0.04);
                border: 1px solid rgba(255,255,255,0.12);
                border-radius: 7px; padding: 5px 12px;
                color: #6a7a90; font-size: 11px; font-weight: 600; text-align: left;
            }
            #shadersToggle:hover {
                border-color: rgba(255,255,255,0.22); color: #a0b0c8;
            }
            #shadersToggle:checked {
                background: rgba(0,229,160,0.12);
                border: 1px solid rgba(0,229,160,0.4); color: #00e5a0;
            }
            #shadersNote { font-size: 10px; color: #3a4a5a; }
            #launchBtn {
                background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
                    stop:0 #1a4fa0, stop:1 #0f8060);
                border: none; border-radius: 10px;
                font-size: 16px; font-weight: 700;
                letter-spacing: 0.15em; color: #e8f8f0;
            }
            #launchBtn:hover {
                background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
                    stop:0 #2060c0, stop:1 #12a070);
            }
            #launchBtn:disabled {
                background: rgba(255,255,255,0.05); color: #3a4a5a;
            }
            #secondaryBtn {
                background: rgba(58,127,213,0.15);
                border: 1px solid rgba(58,127,213,0.35);
                border-radius: 7px; padding: 5px 10px;
                color: #7ab0f0; font-size: 11px; font-weight: 600;
            }
            #secondaryBtn:hover {
                background: rgba(58,127,213,0.25);
                border-color: rgba(58,127,213,0.6);
            }
            #transparent { background: transparent; border: none; }
        """)

    # ── Session / account ────────────────────────────────────────────────────
    def _restore_session(self) -> None:
        saved = AccountStore.load()
        if saved and saved.get("refresh_token"):
            try:
                self.account_info = refresh_saved_account(
                    CLIENT_ID, CLIENT_SECRET, REDIRECT_URI, saved["refresh_token"]
                )
                AccountStore.save(self.account_info)
            except Exception:
                AccountStore.clear()
                self.account_info = None

    def update_status(self, message: str) -> None:
        self.status_label.setText(message)

    def update_account_label(self) -> None:
        self.account_label.setText(
            self.account_info["username"] if self.account_info else "Not signed in"
        )

    def update_ui_state(self) -> None:
        logged_in = self.account_info is not None
        self.launch_button.setEnabled(logged_in)
        self.login_button.setText("Sign out" if logged_in else "Sign in with Microsoft")
        self.pulse_dot.set_active(logged_in)
        self.update_account_label()

    def load_versions(self) -> None:
        self.update_status("Loading versions…")
        try:
            all_versions = safe_get(MANIFEST_URL).json()["versions"]
            allowed_ids  = {v["id"] for v in ALLOWED_VERSIONS} | {v["id"] for v in UNSUPPORTED_VERSIONS}
            self.release_versions = [v for v in all_versions if v["id"] in allowed_ids]
            self.update_status("Ready")
        except Exception as e:
            self.update_status(f"Failed to load versions: {e}")

    def toggle_login_logout(self) -> None:
        if self.account_info:
            self.account_info = None
            AccountStore.clear()
            self.update_ui_state()
            self.update_status("Signed out")
        else:
            self.microsoft_login()

    def microsoft_login(self) -> None:
        if self.login_thread is not None and self.login_thread.isRunning():
            return

        self.login_button.setEnabled(False)
        self.login_button.setText("Signing in…")

        self.login_thread = MicrosoftLoginThread(self)
        self.login_thread.status.connect(self.update_status)
        self.login_thread.succeeded.connect(self._on_login_succeeded)
        self.login_thread.failed.connect(self._on_login_failed)
        self.login_thread.finished.connect(self._on_login_finished)
        self.login_thread.start()

    def _on_login_succeeded(self, account_info: dict) -> None:
        self.account_info = account_info
        AccountStore.save(self.account_info)
        self.update_ui_state()
        self.update_status(f"Signed in as {self.account_info['username']}")

    def _on_login_failed(self, message: str) -> None:
        self.update_status(f"Microsoft sign-in failed: {message}")
        QMessageBox.critical(
            self,
            "Microsoft Sign-in Failed",
            message,
        )

    def _on_login_finished(self) -> None:
        thread = self.login_thread
        self.login_thread = None
        self.update_ui_state()
        if thread is not None:
            thread.deleteLater()

    def start_launch(self) -> None:
        if not self.account_info:
            self.update_status("No account signed in")
            return
        version_entry = next((v for v in self.release_versions if v["id"] == self.selected_version_id), None)
        if not version_entry:
            self.update_status("Selected version not found")
            return
        self.launch_button.setEnabled(False)
        self.thread = LaunchThread(version_entry, self.account_info, self.shaders_enabled)
        self.thread.status.connect(self.update_status)
        self.thread.progress.connect(self.update_progress)
        self.thread.minecraft_started.connect(self._on_minecraft_started)
        self.thread.minecraft_stopped.connect(self._on_minecraft_stopped)
        self.thread.finished.connect(self._on_launch_thread_finished)
        self.thread.start()

    def _toggle_shaders(self, checked: bool) -> None:
        self.shaders_enabled = checked
        self.shaders_checkbox.setText("☑  Add Shaders (Iris)" if checked else "☐  Add Shaders (Iris)")
        PreferencesStore.save(self.selected_version_id, self.shaders_enabled)

    def update_progress(self, value: int) -> None:
        self.progress_bar.setValue(value)

    def trigger_restart(self, exe_path: str) -> None:
        apply_update(exe_path)

