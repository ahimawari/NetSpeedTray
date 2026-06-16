from __future__ import annotations

# --- Standard Library Imports ---
import logging
import math
import os
import sys
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple, TYPE_CHECKING

# --- Third-Party Imports ---
import win32api
import win32con
import win32gui
from win32con import MONITOR_DEFAULTTONEAREST
from PyQt6.QtCore import QEvent, QObject, QPoint, QRect, QSize, QTimer, Qt
from PyQt6.QtGui import (
    QCloseEvent, QColor, QContextMenuEvent, QFont, QFontMetrics, QHideEvent,
    QIcon, QMouseEvent, QPaintEvent, QPainter, QShowEvent
)
from PyQt6.QtWidgets import QApplication, QMenu, QMessageBox, QWidget

# --- First-Party (Local) Imports ---
from netspeedtray import constants
from netspeedtray.core.controller import StatsController as CoreController
from netspeedtray.core.timer_manager import SpeedTimerManager
from netspeedtray.core.monitor_thread import StatsMonitorThread
from netspeedtray.core.tray_manager import TrayIconManager
from netspeedtray.core.widget_state import WidgetState as CoreWidgetState
from netspeedtray.utils.config import ConfigManager as CoreConfigManager
from netspeedtray.core.position_manager import PositionManager, WindowState
from netspeedtray.core.input_handler import InputHandler
from netspeedtray.utils.taskbar_utils import (
    get_taskbar_info, is_taskbar_obstructed, is_taskbar_visible,
    get_process_name_from_hwnd
)

from netspeedtray.utils.widget_renderer import WidgetRenderer as CoreWidgetRenderer, RenderConfig
from netspeedtray.utils.helpers import format_speed
from netspeedtray.core.system_events import SystemEventHandler
from netspeedtray.views.widget.layout import WidgetLayoutManager
from netspeedtray.views.widget.theme import WidgetThemeManager
from netspeedtray.views.widget.details_popup import DetailRow, ModuleDetailPopup
from netspeedtray.core.startup_manager import StartupManager
from netspeedtray.core.config_controller import ConfigController
from netspeedtray.core.update_checker import UpdateChecker

# --- Type Checking ---
if TYPE_CHECKING:
    from netspeedtray.constants.i18n import I18nStrings
    from netspeedtray.views.graph import GraphWindow
    from netspeedtray.views.settings import SettingsDialog
    from netspeedtray.views.app_activity import AppActivityWindow


class NetworkSpeedWidget(QWidget):
    """Main widget for displaying network speeds near the Windows system tray."""

    MIN_UPDATE_INTERVAL = constants.config.defaults.MINIMUM_UPDATE_RATE


    def __init__(self, taskbar_height: int = constants.taskbar.taskbar.DEFAULT_HEIGHT, config: Optional[Dict[str, Any]] = None, i18n: Optional[constants.i18n.I18nStrings] = None, parent: QObject | None = None) -> None:
        """Initialize the NetworkSpeedTray with core components and UI setup."""
        super().__init__(parent)
        self.logger = logging.getLogger(f"{constants.app.APP_NAME}.{self.__class__.__name__}")
        self.logger.debug("Initializing NetworkSpeedWidget...")
        self.settings_dialog: Optional[SettingsDialog] = None

        # --- Core Application State ---
        self.session_start_time = datetime.now()
        self.config_manager = CoreConfigManager()
        
        # Initialize ConfigController
        # NOTE: We pass 'self' (the widget) to the controller. This requires careful handling
        # in the controller to avoid circular discrepancies, but allows it to orchestrate updates.
        self.config_controller = ConfigController(self, self.config_manager)
        
        self.config: Dict[str, Any] = config or self.config_controller.load_initial_config(taskbar_height)
        
        if i18n is None:
            raise ValueError("An i18n instance must be provided to NetworkSpeedWidget.")
        self.i18n = i18n
        
        # These MUST be initialized before _init_managers() because it checks self.current_metrics
        self.current_font: QFont = None
        self.current_metrics: QFontMetrics = None

        self._init_managers() # Initialize managers first
        self.theme_manager.apply_theme_aware_defaults()

        # --- Declare all instance attributes for clarity ---
        self.widget_state: CoreWidgetState
        self.timer_manager: SpeedTimerManager
        self.controller: CoreController
        self.renderer: CoreWidgetRenderer
        self.position_manager: PositionManager
        self.input_handler: InputHandler
        self.layout_manager: WidgetLayoutManager
        self.theme_manager: WidgetThemeManager
        self.tray_manager: TrayIconManager
        self.monitor_thread: StatsMonitorThread
        self._cached_layout_mode: str = 'vertical'  # Updated on taskbar changes
        self.graph_window: Optional[GraphWindow] = None
        self.app_activity_window: Optional[AppActivityWindow] = None
        self.detail_popup: Optional[ModuleDetailPopup] = None
        self.update_checker: Optional[UpdateChecker] = None
        self.app_icon: QIcon
        # Note: self.current_font and self.current_metrics are initialized earlier before _init_managers()
        
        self.upload_speed: float = 0.0
        self.download_speed: float = 0.0
        self.cpu_usage: float = 0.0
        self.gpu_usage: float = 0.0
        self.cpu_temp: Optional[float] = None
        self.gpu_temp: Optional[float] = None
        self.cpu_power: Optional[float] = None
        self.gpu_power: Optional[float] = None
        self.ram_used: Optional[float] = None
        self.ram_total: Optional[float] = None
        self.vram_used: Optional[float] = None
        self.vram_total: Optional[float] = None
        
        # Cycling State
        self._cycle_index: int = 0
        self._current_cycle_mode: str = "network_only"
        self._cycle_timer = QTimer(self)
        self._cycle_timer.timeout.connect(self._rotate_cycle)
        self._hover_detail_timer = QTimer(self)
        self._hover_detail_timer.setSingleShot(True)
        self._hover_detail_timer.timeout.connect(self._show_pending_hover_detail)
        self._double_click_poll_timer = QTimer(self)
        self._double_click_poll_timer.setInterval(40)
        self._double_click_poll_timer.timeout.connect(self._poll_native_click_state)

        self.taskbar_height: int = taskbar_height
        self._dragging: bool = False
        self._drag_offset: QPoint = QPoint()
        self._poll_left_down: bool = False
        self._poll_press_inside: bool = False
        self._poll_press_pos: Optional[QPoint] = None
        self._poll_press_dragging: bool = False
        self._poll_last_click_time_ms: float = 0.0
        self._poll_last_click_pos: Optional[QPoint] = None
        self.startup_manager: StartupManager
        self.is_paused: bool = False
        self._last_immediate_hide_time: float = 0.0 # For the race condition fix
        self._is_context_menu_visible: bool = False
        self.last_tray_rect: Optional[Tuple[int, int, int, int]] = None
        self._taskbar_lost_count: int = 0
        self._will_quit_app: bool = False # Flag to distinguish hide vs exit
        self._module_hit_rects: List[Tuple[str, QRect]] = []
        self._detail_popup_key: Optional[str] = None
        self._detail_popup_anchor: Optional[QRect] = None
        self._detail_popup_sticky: bool = False
        self._pending_hover_key: Optional[str] = None
        self._pending_hover_anchor: Optional[QRect] = None
        
        # Hooks for system events
        self.system_event_handler: SystemEventHandler

        
        # Timers for periodic checks
        # self._tray_watcher_timer moved to PositionManager
        self._state_watcher_timer = QTimer(self) # The "Safety Net" timer
        
        self.setVisible(False)
        self.logger.debug("Widget initially hidden to stabilize position and size.")

        # --- Initialization Steps ---
        try:
            self.layout_manager.setup_window_properties()
            self._init_ui_components()
            self._init_core_components()
            
            # Now that all components are initialized, perform the initial resize.
            self.layout_manager.resize_widget_for_font()
            
            self._setup_connections()
            self._setup_timers()
            self.position_manager.update_position()
            self._synchronize_startup_task()
            
            QTimer.singleShot(0, self._delayed_initial_show)

            self.logger.debug("NetworkSpeedWidget initialized successfully.")

        except Exception as e:
            self.logger.critical("Initialization failed: %s", e, exc_info=True)
            raise RuntimeError(f"Failed to initialize NetworkSpeedWidget: {e}") from e


    def _setup_timers(self) -> None:
        """Configures all application timers."""
        # PositionManager handles tray monitoring
        if hasattr(self, 'position_manager'):
            self.position_manager.start_monitoring()
        self.logger.debug("PositionManager monitoring started.")

        # This is the "Safety Net" timer. It runs to catch states missed by events.
        self._state_watcher_timer.setInterval(constants.timeouts.STATE_WATCHER_INTERVAL_MS)
        self._state_watcher_timer.timeout.connect(self._execute_refresh)
        self._state_watcher_timer.start()
        self.logger.debug(f"Safety net state watcher timer started ({constants.timeouts.STATE_WATCHER_INTERVAL_MS}ms).")

        # Update Checker — delayed startup check
        self.update_checker = UpdateChecker(self.config, self)
        self.update_checker.update_available.connect(self._on_update_available)
        if self.update_checker.should_check():
            QTimer.singleShot(5000, self.update_checker.check_now)

        # Cycle Timer
        if self.config.get("widget_display_mode") == "cycle":
            self._cycle_timer.start(constants.renderer.renderer.CYCLE_INTERVAL_MS)
            self.logger.debug("Cycle timer started.")

        self._double_click_poll_timer.start()
        self.logger.debug("Native click polling timer started.")


    def _init_core_components(self) -> None:
        """
        Initialize non-UI core logic components.

        Sets up WidgetState, SpeedTimerManager, StatsController, and WidgetRenderer.
        """
        self.logger.debug("Initializing core components...")
        if not self.config:
            raise RuntimeError("Cannot initialize core: Config missing")
        try:
            self.widget_state = CoreWidgetState(self.config)
            self.timer_manager = SpeedTimerManager(self.config, parent=self)
            
            # Background Monitoring Thread
            # Determine effective monitor interval. Support SMART sentinel (-1.0)
            cfg_rate = self.config.get("update_rate", constants.config.defaults.DEFAULT_UPDATE_RATE)

            # If the user is using "auto" scaling (unit auto-selection), running in
            # SMART adaptive mode produces very frequent UI changes that can be
            # visually jarring (e.g. rapid unit switching). Enforce a safe fallback:
            # when speed_display_mode == "auto" and update_rate signals SMART (<=0),
            # fall back to a sensible default fixed rate to avoid live-mode jitter.
            speed_mode = str(self.config.get("speed_display_mode", constants.config.defaults.DEFAULT_SPEED_DISPLAY_MODE))
            if isinstance(cfg_rate, (int, float)) and cfg_rate < 0:
                if speed_mode == "auto":
                    # Log and fallback to default fixed update rate (do not persist silently)
                    self.logger.warning(
                        "Incompatible settings: speed_display_mode='auto' with SMART update_rate. Falling back to default update rate %.1fs to avoid live-mode jitter.",
                        constants.config.defaults.DEFAULT_UPDATE_RATE
                    )
                    cfg_rate = constants.config.defaults.DEFAULT_UPDATE_RATE
                    effective_interval = max(constants.config.defaults.MINIMUM_UPDATE_RATE, min(float(cfg_rate), constants.timers.MAXIMUM_UPDATE_RATE_SECONDS))
                else:
                    # SMART mode (-1.0): Use adaptive interval
                    effective_interval = constants.timers.SMART_MODE_INTERVAL_MS / 1000.0
            else:
                # Fixed interval: Clamp to allowed min/max
                effective_interval = max(constants.config.defaults.MINIMUM_UPDATE_RATE, min(float(cfg_rate), constants.timers.MAXIMUM_UPDATE_RATE_SECONDS))

            self.monitor_thread = StatsMonitorThread(interval=effective_interval, config=self.config)
            
            self.controller = CoreController(config=self.config, widget_state=self.widget_state)
            self.controller.set_view(self)
            self.renderer = CoreWidgetRenderer(self.config, self.i18n)
            
            # Note: We no longer start timer_manager for speeds; monitor_thread drives them.
            # self.timer_manager.start_timer() 
            self.logger.debug("Core components initialized; monitor thread ready.")
        except Exception as e:
            self.logger.error("Failed to initialize core components: %s", e, exc_info=True)
            raise RuntimeError("Failed to initialize core application components") from e


    def _init_managers(self) -> None:
        """Initialize all helper managers."""
        self.logger.debug("Initializing Managers...")
        
        # 1. Layout & Theme Managers
        self.layout_manager = WidgetLayoutManager(self)
        self.theme_manager = WidgetThemeManager(self)
        self.startup_manager = StartupManager()
        
        if not self.current_metrics:
            self.layout_manager.init_font()

        try:
            # Position Manager
            taskbar_info = get_taskbar_info()
            window_state = WindowState(
                config=self.config,
                widget=self,
                taskbar_info=taskbar_info,
                font_metrics=self.current_metrics
            )
            self.position_manager = PositionManager(window_state, parent=self)
            # Note: InputHandler is initialized in _init_ui_components() after tray_manager is created
            
            self.logger.debug("Managers initialized successfully.")

        except Exception as e:
            self.logger.critical(f"Failed to initialize managers: {e}", exc_info=True)
            raise RuntimeError("Manager initialization failed") from e











    def _execute_refresh(self, hwnd: int = 0) -> None:
        """
        The AUTHORITATIVE refresh trigger. This version includes a grace period
        to handle temporary taskbar detection failures (e.g., during shell restarts).
        """
        if self._is_context_menu_visible or self._dragging:
            return
        
        try:
            taskbar_info = get_taskbar_info()

            # Implement the "coasting" logic for taskbar detection failures.
            # Implement the "coasting" logic for taskbar detection failures.
            if taskbar_info.hwnd == 0: # hwnd=0 signifies a fallback object from get_taskbar_info
                self._taskbar_lost_count += 1
                if self._taskbar_lost_count % 10 == 0: # Log warning every 10 seconds
                    self.logger.warning(
                        f"Taskbar detection failing. Coasting on fallback/safe mode. "
                        f"Failure count: {self._taskbar_lost_count}"
                    )
                # Removed logic that hides widget after 5 failures. 
                # We now rely on 'safe fallback position' (bottom-right of screen) instead.
            else:
                # If we successfully found a real taskbar, reset the counter.
                self._taskbar_lost_count = 0

            if hwnd == 0:
                hwnd = win32gui.GetForegroundWindow()

            # Allow user override to keep widget visible even when a fullscreen window is present
            keep_visible = self.config.get("keep_visible_fullscreen", False)
            should_be_visible = is_taskbar_visible(taskbar_info) and (keep_visible or not is_taskbar_obstructed(taskbar_info, hwnd))

            if self.isVisible() != should_be_visible:
                self.setVisible(should_be_visible)
            
            # Only update position if we are supposed to be visible.
            if self.isVisible():
                if not self.config.get("free_move", False):
                    self.position_manager.update_position(fresh_taskbar_info=taskbar_info)
                
                # Always re-assert topmost status when visible to prevent falling behind taskbar (#77)
                self._ensure_win32_topmost()

        except Exception as e:
            self.logger.error(f"Critical error in _execute_refresh (failure count: {self._taskbar_lost_count}): {e}")
            # If we've had many consecutive failures, only then hide as a last resort.
            # Otherwise, keep it visible and hope for recovery on next tick.
            if self._taskbar_lost_count > 30 and self.isVisible():
                 self.logger.warning("Hiding widget as a last resort after sustained detection failure.")
                 self.setVisible(False)


    def _delayed_initial_show(self) -> None:
        """Triggers the initial authoritative visibility check."""
        self.logger.debug("Executing delayed initial show...")
        try:
            # Replace the call to the old manager with the proven, authoritative function.
            self._execute_refresh()
            
            if self.isVisible():
                self.logger.debug("Widget shown after stabilization")
        except Exception as e:
            self.logger.error(f"Error in delayed initial show: {e}", exc_info=True)
            # Ensure widget is hidden if an error occurs during the initial check.
            self.setVisible(False)


    def pause(self) -> None:
        """Pause widget updates (for future use, not active by default)."""
        if self.is_paused:
            self.logger.debug("Widget already paused")
            return
        self.logger.info("Pausing widget updates")
        self.is_paused = True
        if self.controller:
            self.controller.pause()
        if self.timer_manager:
            self.timer_manager.stop_timer()
        if self.renderer:
            self.renderer.pause()
        self.update_config({'paused': True})
        self.update()


    def resume(self) -> None:
        """Resume widget updates (for future use, not active by default)."""
        if not self.is_paused:
            self.logger.debug("Widget already running")
            return
        self.logger.info("Resuming widget updates")
        self.is_paused = False
        if self.controller:
            self.controller.resume()
        if self.timer_manager:
            self.timer_manager.start_timer()
        if self.renderer:
            self.renderer.resume()
        self.update_config({'paused': False})
        self.update()


    def update_display_speeds(self, upload_mbps: float, download_mbps: float) -> None:
        """
        Slot for the controller's `display_speed_updated` signal.
        Receives aggregated speeds in Mbps and schedules a repaint of the widget.
        """
        self.upload_speed = upload_mbps
        self.download_speed = download_mbps
        self._refresh_visible_detail_popup()
        self.update() # Trigger a repaint


    def update_cpu_usage(self, usage: float) -> None:
        """Update CPU usage and trigger repaint."""
        self.cpu_usage = usage
        self._refresh_visible_detail_popup()
        if self.config.get("widget_display_mode") in ["cpu_only", "combined", "side_by_side", "cycle"]:
            self.layout_manager.resize_widget_for_font()
            self.update()

    def update_gpu_usage(self, usage: float) -> None:
        """Update GPU usage and trigger repaint."""
        self.gpu_usage = usage
        self._refresh_visible_detail_popup()
        if self.config.get("widget_display_mode") in ["gpu_only", "combined", "side_by_side", "cycle"]:
            self.layout_manager.resize_widget_for_font()
            self.update()

    def update_cpu_temp(self, temp: float) -> None:
        """Update CPU temperature and trigger repaint."""
        self.cpu_temp = temp
        self._refresh_visible_detail_popup()
        if self.config.get("widget_display_mode") in ["cpu_only", "combined", "side_by_side", "cycle"]:
            self.layout_manager.resize_widget_for_font()
            self.update()

    def update_gpu_temp(self, temp: float) -> None:
        """Update GPU temperature and trigger repaint."""
        self.gpu_temp = temp
        self._refresh_visible_detail_popup()
        if self.config.get("widget_display_mode") in ["gpu_only", "combined", "side_by_side", "cycle"]:
            self.layout_manager.resize_widget_for_font()
            self.update()

    def update_cpu_power(self, power: float) -> None:
        """Update CPU power draw and trigger repaint."""
        self.cpu_power = power
        self._refresh_visible_detail_popup()
        if self.config.get("widget_display_mode") in ["cpu_only", "combined", "side_by_side", "cycle"]:
            self.update()

    def update_gpu_power(self, power: float) -> None:
        """Update GPU power draw and trigger repaint."""
        self.gpu_power = power
        self._refresh_visible_detail_popup()
        if self.config.get("widget_display_mode") in ["gpu_only", "combined", "side_by_side", "cycle"]:
            self.update()

    def update_ram_info(self, used: float, total: float) -> None:
        """Update RAM info and trigger repaint."""
        self.ram_used = used
        self.ram_total = total
        self._refresh_visible_detail_popup()
        if self.config.get("widget_display_mode") in ["cpu_only", "combined", "side_by_side", "cycle"]:
            self.layout_manager.resize_widget_for_font()
            self.update()

    def update_vram_info(self, used: float, total: float) -> None:
        """Update VRAM info and trigger repaint."""
        self.vram_used = used
        self.vram_total = total if total >= 0 else None
        self._refresh_visible_detail_popup()
        if self.config.get("widget_display_mode") in ["gpu_only", "combined", "side_by_side", "cycle"]:
            self.layout_manager.resize_widget_for_font()
            self.update()

    def update_display_hardware(self, cpu: Optional[float] = None, gpu: Optional[float] = None) -> None:
        # Legacy method kept for safety but we prefer the individual ones above
        if cpu is not None: self.cpu_usage = cpu
        if gpu is not None: self.gpu_usage = gpu
        self._refresh_visible_detail_popup()
        self.update()


    def _rotate_cycle(self) -> None:
        """Rotates the displayed metric when in 'cycle' mode."""
        modes = ["network_only"]
        if self.config.get("monitor_cpu_enabled", False): modes.append("cpu_only")
        if self.config.get("monitor_gpu_enabled", False): modes.append("gpu_only")
        
        if not modes: return
        
        self._cycle_index = (self._cycle_index + 1) % len(modes)
        self._current_cycle_mode = modes[self._cycle_index]
        self.update() # Trigger repaint with new mode


    def schedule_hover_detail(self, local_pos: QPoint, global_pos: QPoint) -> None:
        """Schedules a module detail popup for pointer hover."""
        if self._detail_popup_sticky:
            return

        module_key, anchor = self._module_popup_target_at_point(local_pos, global_pos)
        if not module_key or anchor is None:
            self.unsetCursor()
            self._hover_detail_timer.stop()
            self.hide_hover_detail()
            return

        self.setCursor(Qt.CursorShape.PointingHandCursor)
        if module_key == self._pending_hover_key and self._hover_detail_timer.isActive():
            return
        if module_key == self._detail_popup_key and self.detail_popup and self.detail_popup.isVisible():
            return

        self._pending_hover_key = module_key
        self._pending_hover_anchor = anchor
        self._hover_detail_timer.start(260)

    def cancel_pending_detail_popup(self) -> None:
        """Cancels pending hover detail popups."""
        self._hover_detail_timer.stop()
        self._pending_hover_key = None
        self._pending_hover_anchor = None

    def hide_hover_detail(self) -> None:
        """Hides non-sticky hover details."""
        self._hover_detail_timer.stop()
        if not self._detail_popup_sticky and self.detail_popup:
            self.detail_popup.hide()
            self._detail_popup_key = None
            self._detail_popup_anchor = None

    def hide_detail_popup(self, force: bool = False) -> None:
        """Hides any visible detail popup."""
        if force:
            self._detail_popup_sticky = False
        self.cancel_pending_detail_popup()
        if self.detail_popup:
            self.detail_popup.hide()
        self._detail_popup_key = None
        self._detail_popup_anchor = None

    def _show_pending_hover_detail(self) -> None:
        if self._pending_hover_key and self._pending_hover_anchor:
            self._show_detail_popup(self._pending_hover_key, self._pending_hover_anchor, sticky=False)

    def show_hardware_detail_overview(self, global_pos: Optional[QPoint] = None) -> None:
        """Shows the full hardware detail popup anchored to the clicked module."""
        self.cancel_pending_detail_popup()

        anchor = QRect(self.mapToGlobal(QPoint(0, 0)), self.size())
        if global_pos is not None:
            local_pos = self.mapFromGlobal(global_pos)
            hit = self._module_hit_at_point(local_pos)
            if hit:
                _, rect = hit
                anchor = QRect(self.mapToGlobal(rect.topLeft()), rect.size())

        self._show_detail_popup("overview", anchor, sticky=True)

    def _show_detail_popup(self, module_key: str, anchor: QRect, sticky: bool, keep_position: bool = False) -> None:
        if self.detail_popup is None:
            self.detail_popup = ModuleDetailPopup(parent=None)

        title, rows, accent = self._build_module_detail(module_key)
        if not rows:
            return

        if keep_position and self._detail_popup_anchor is not None:
            anchor = self._detail_popup_anchor

        self.detail_popup.set_content(title, rows, accent)
        self.detail_popup.show_for_rect(anchor)
        self._detail_popup_key = module_key
        self._detail_popup_anchor = anchor
        self._detail_popup_sticky = sticky

    def _refresh_visible_detail_popup(self) -> None:
        # Keep visible popups stable. Rebuilding them on every 1s stats tick causes
        # visible flicker and can make a sticky click popup feel like it is moving.
        return

    def _module_key_at_point(self, point: QPoint) -> Optional[str]:
        hit = self._module_hit_at_point(point)
        return hit[0] if hit else None

    def _module_hit_at_point(self, point: QPoint) -> Optional[Tuple[str, QRect]]:
        for key, rect in reversed(self._module_hit_rects):
            if rect.contains(point):
                return key, rect
        return None

    def _module_popup_target_at_point(self, local_pos: QPoint, fallback_global_pos: QPoint) -> Tuple[Optional[str], Optional[QRect]]:
        """Returns the hit module and its stable global popup anchor rect."""
        hit = self._module_hit_at_point(local_pos)
        if not hit:
            return None, QRect(fallback_global_pos, fallback_global_pos)

        key, rect = hit
        return key, QRect(self.mapToGlobal(rect.topLeft()), rect.size())

    def _reset_module_hit_rects(self) -> None:
        self._module_hit_rects = []

    def _register_module_hit_rect(self, module_key: str, rect: QRect) -> None:
        if rect.isNull() or rect.width() <= 0 or rect.height() <= 0:
            return
        clipped = rect.adjusted(-2, -2, 2, 2).intersected(self.rect())
        if clipped.isEmpty():
            return
        self._module_hit_rects.append((module_key, clipped))

    def _register_network_hit_rect(self, x_offset: int = 0) -> None:
        width = getattr(self.layout_manager, '_network_width', self.width() - x_offset)
        self._register_module_hit_rect("network", QRect(x_offset, 0, max(1, int(width)), self.height()))

    def _ordered_hardware_metric_keys(self, include_cpu: bool, include_gpu: bool) -> List[str]:
        order = list(getattr(self.renderer.config, 'widget_display_order', ["network", "cpu", "gpu"]))
        keys: List[str] = []
        for key in order + ["cpu", "gpu"]:
            if key == "cpu" and include_cpu and "cpu" not in keys:
                keys.append("cpu")
            elif key == "gpu" and include_gpu and "gpu" not in keys:
                keys.append("gpu")
        return keys

    def _pixel_hud_hardware_keys(self, include_cpu: bool, include_gpu: bool) -> List[str]:
        config = self.renderer.config
        keys: List[str] = []
        for key in self._ordered_hardware_metric_keys(include_cpu, include_gpu):
            keys.append(key)
            if key == "cpu" and getattr(config, "monitor_ram_enabled", False):
                keys.append("ram")
            elif key == "gpu" and getattr(config, "monitor_vram_enabled", False):
                keys.append("vram")
        if getattr(config, "show_hardware_temps", False) and (include_cpu or include_gpu):
            keys.append("temp")
        return keys

    def _pixel_hud_block_rects(self, x_offset: int, keys: List[str], network: bool = False) -> List[Tuple[str, QRect]]:
        if not keys:
            return []

        label_w = constants.renderer.PIXEL_HUD_LABEL_WIDTH
        graph_w = constants.renderer.PIXEL_HUD_NETWORK_GRAPH_WIDTH if network else constants.renderer.PIXEL_HUD_GRAPH_WIDTH
        inner_gap = constants.renderer.PIXEL_HUD_INNER_GAP
        block_gap = constants.renderer.PIXEL_HUD_GAP
        block_w = label_w + inner_gap + graph_w
        margin = constants.renderer.TEXT_MARGIN
        metrics_h = self.current_metrics.height() if self.current_metrics else constants.renderer.PIXEL_HUD_MIN_HEIGHT
        block_h = max(
            constants.renderer.PIXEL_HUD_MIN_HEIGHT,
            min(self.height() - 4, metrics_h * 2 + 1),
        )
        top = int((self.height() - block_h) / 2)
        current_x = x_offset + margin

        rects: List[Tuple[str, QRect]] = []
        for key in keys:
            rects.append((key, QRect(current_x, top, block_w, block_h)))
            current_x += block_w + block_gap
        return rects

    def _register_hardware_hit_rects(self, include_cpu: bool, include_gpu: bool, x_offset: int = 0) -> None:
        config = self.renderer.config
        if getattr(config, 'hardware_label_style', '') == "pixel_hud_blocks":
            for key, rect in self._pixel_hud_block_rects(
                x_offset,
                self._pixel_hud_hardware_keys(include_cpu, include_gpu),
                network=False,
            ):
                self._register_module_hit_rect(key, rect)
            return

        rect = self.renderer.get_last_text_rect()
        if rect.isNull() or rect.width() <= 0:
            return

        keys = self._ordered_hardware_metric_keys(include_cpu, include_gpu)
        if len(keys) <= 1:
            self._register_module_hit_rect(keys[0] if keys else "hardware", QRect(rect.left(), 0, rect.width(), self.height()))
            return

        segment_h = max(1, self.height() // len(keys))
        for idx, key in enumerate(keys):
            self._register_module_hit_rect(key, QRect(rect.left(), idx * segment_h, rect.width(), segment_h))

    def _format_speed_detail(self, mbps: float) -> str:
        try:
            bytes_per_sec = (float(mbps) * constants.network.units.MEGA_DIVISOR) / constants.network.units.BITS_PER_BYTE
            return format_speed(
                bytes_per_sec,
                self.i18n,
                force_mega_unit=self.config.get("speed_display_mode") == "always_mbps",
                decimal_places=int(self.config.get("decimal_places", constants.config.defaults.DEFAULT_DECIMAL_PLACES)),
                unit_type=str(self.config.get("unit_type", constants.config.defaults.DEFAULT_UNIT_TYPE)),
                short_labels=bool(self.config.get("short_unit_labels", constants.config.defaults.DEFAULT_SHORT_UNIT_LABELS)),
            )
        except Exception:
            return self.i18n.DEFAULT_TEXT

    def _detail_label(self, key: str) -> str:
        zh = getattr(self.i18n, "language", "") == "zh_CN"
        labels = {
            "current": ("当前", "Current"),
            "usage": ("使用率", "Usage"),
            "temperature": ("温度", "Temperature"),
            "power": ("功耗", "Power"),
            "memory": ("内存", "Memory"),
            "vram": ("显存", "VRAM"),
            "used": ("已用", "Used"),
            "total": ("总量", "Total"),
            "available": ("可用", "Available"),
            "source": ("来源", "Source"),
            "samples": ("样本", "Samples"),
            "mode": ("模式", "Mode"),
            "updated": ("更新", "Updated"),
            "overview": ("硬件详情", "Hardware Details"),
            "network": (self.i18n.ORDER_TYPE_NETWORK, self.i18n.ORDER_TYPE_NETWORK),
            "cpu": (self.i18n.ORDER_TYPE_CPU, self.i18n.ORDER_TYPE_CPU),
            "gpu": (self.i18n.ORDER_TYPE_GPU, self.i18n.ORDER_TYPE_GPU),
        }
        value = labels.get(key, (key, key))
        return value[0] if zh else value[1]

    def _format_optional_percent(self, value: Optional[float]) -> str:
        try:
            if value is None or not math.isfinite(float(value)):
                return self.i18n.DEFAULT_TEXT
            return f"{float(value):.0f}%"
        except Exception:
            return self.i18n.DEFAULT_TEXT

    def _format_optional_temp(self, value: Optional[float]) -> str:
        try:
            if value is None or not math.isfinite(float(value)):
                return self.i18n.DEFAULT_TEXT
            return f"{float(value):.0f}°C"
        except Exception:
            return self.i18n.DEFAULT_TEXT

    def _format_optional_power(self, value: Optional[float]) -> str:
        try:
            if value is None or not math.isfinite(float(value)):
                return self.i18n.DEFAULT_TEXT
            return f"{float(value):.1f} W"
        except Exception:
            return self.i18n.DEFAULT_TEXT

    def _format_memory_detail(self, used: Optional[float], total: Optional[float]) -> str:
        try:
            if used is None or not math.isfinite(float(used)):
                return self.i18n.DEFAULT_TEXT
            if total is None or not math.isfinite(float(total)) or float(total) <= 0:
                return f"{float(used):.1f} GB"
            pct = (float(used) / float(total)) * 100.0
            return f"{float(used):.1f}/{float(total):.1f} GB ({pct:.0f}%)"
        except Exception:
            return self.i18n.DEFAULT_TEXT

    def _format_memory_percent(self, used: Optional[float], total: Optional[float]) -> str:
        try:
            if used is None or total is None or float(total) <= 0:
                return self.i18n.DEFAULT_TEXT
            return f"{(float(used) / float(total)) * 100.0:.0f}%"
        except Exception:
            return self.i18n.DEFAULT_TEXT

    def _active_interface_summary(self) -> str:
        try:
            speed_data = getattr(self.controller, "current_speed_data", {}) or {}
            if len(speed_data) == 1:
                return next(iter(speed_data.keys()))
            if len(speed_data) > 1:
                suffix = "个接口" if getattr(self.i18n, "language", "") == "zh_CN" else "interfaces"
                return f"{len(speed_data)} {suffix}"
            primary = getattr(self.controller, "primary_interface", None)
            return primary or self.i18n.DEFAULT_TEXT
        except Exception:
            return self.i18n.DEFAULT_TEXT

    def _hardware_bridge_source(self) -> str:
        try:
            bridge_path = getattr(self.monitor_thread, "_lhm_bridge_path", "")
            if bridge_path and os.path.exists(bridge_path):
                return "NetSpeedTray Hardware Bridge"
        except Exception:
            pass
        return self.i18n.DEFAULT_TEXT

    def _module_title(self, module_key: str) -> str:
        if module_key == "overview":
            return self._detail_label("overview")
        if module_key == "network":
            return self._detail_label("network")
        if module_key == "cpu":
            return self._detail_label("cpu")
        if module_key == "gpu":
            return self._detail_label("gpu")
        if module_key == "ram":
            return "RAM"
        if module_key == "vram":
            return "VRAM"
        if module_key == "temp":
            return "CPU / GPU Temp"
        return module_key.upper()

    def _module_accent(self, module_key: str) -> str:
        accents = {
            "network": constants.graph.UPLOAD_LINE_COLOR,
            "cpu": constants.renderer.PIXEL_HUD_CPU_COLOR,
            "gpu": constants.renderer.PIXEL_HUD_GPU_COLOR,
            "ram": constants.renderer.PIXEL_HUD_RAM_COLOR,
            "vram": constants.renderer.PIXEL_HUD_VRAM_COLOR,
            "temp": constants.renderer.PIXEL_HUD_TEMP_COLOR,
            "overview": "#18E8FF",
        }
        return accents.get(module_key, "#18E8FF")

    def _build_module_detail(self, module_key: str) -> Tuple[str, List[DetailRow], str]:
        accent = self._module_accent(module_key)

        if module_key == "network":
            rows = [
                DetailRow(self.i18n.UPLOAD_LABEL, self._format_speed_detail(self.upload_speed), constants.graph.UPLOAD_LINE_COLOR),
                DetailRow(self.i18n.DOWNLOAD_LABEL, self._format_speed_detail(self.download_speed), constants.graph.DOWNLOAD_LINE_COLOR),
                DetailRow(self.i18n.INTERFACE_LABEL, self._active_interface_summary()),
                DetailRow(self._detail_label("mode"), str(self.config.get("interface_mode", "auto"))),
                DetailRow(self._detail_label("samples"), str(len(self.widget_state.aggregated_history))),
            ]
        elif module_key == "cpu":
            rows = [
                DetailRow(self._detail_label("usage"), self._format_optional_percent(self.cpu_usage), accent),
                DetailRow(self._detail_label("temperature"), self._format_optional_temp(self.cpu_temp)),
                DetailRow(self._detail_label("power"), self._format_optional_power(self.cpu_power)),
                DetailRow("RAM", self._format_memory_detail(self.ram_used, self.ram_total), constants.renderer.PIXEL_HUD_RAM_COLOR),
                DetailRow(self._detail_label("samples"), str(len(self.widget_state.cpu_history))),
            ]
        elif module_key == "gpu":
            rows = [
                DetailRow(self._detail_label("usage"), self._format_optional_percent(self.gpu_usage), accent),
                DetailRow(self._detail_label("temperature"), self._format_optional_temp(self.gpu_temp)),
                DetailRow(self._detail_label("power"), self._format_optional_power(self.gpu_power)),
                DetailRow("VRAM", self._format_memory_detail(self.vram_used, self.vram_total), constants.renderer.PIXEL_HUD_VRAM_COLOR),
                DetailRow(self._detail_label("samples"), str(len(self.widget_state.gpu_history))),
            ]
        elif module_key == "ram":
            rows = [
                DetailRow(self._detail_label("used"), self._format_memory_detail(self.ram_used, self.ram_total), accent),
                DetailRow(self._detail_label("usage"), self._format_memory_percent(self.ram_used, self.ram_total), accent),
                DetailRow(self._detail_label("available"), self._format_memory_detail(
                    (self.ram_total - self.ram_used) if self.ram_total is not None and self.ram_used is not None else None,
                    self.ram_total,
                )),
            ]
        elif module_key == "vram":
            rows = [
                DetailRow(self._detail_label("used"), self._format_memory_detail(self.vram_used, self.vram_total), accent),
                DetailRow(self._detail_label("usage"), self._format_memory_percent(self.vram_used, self.vram_total), accent),
                DetailRow(self._detail_label("available"), self._format_memory_detail(
                    (self.vram_total - self.vram_used) if self.vram_total is not None and self.vram_used is not None else None,
                    self.vram_total,
                )),
            ]
        elif module_key == "temp":
            rows = [
                DetailRow(f"{self._detail_label('cpu')} {self._detail_label('temperature')}", self._format_optional_temp(self.cpu_temp), constants.renderer.PIXEL_HUD_CPU_COLOR),
                DetailRow(f"{self._detail_label('gpu')} {self._detail_label('temperature')}", self._format_optional_temp(self.gpu_temp), constants.renderer.PIXEL_HUD_GPU_COLOR),
                DetailRow(f"{self._detail_label('cpu')} {self._detail_label('power')}", self._format_optional_power(self.cpu_power)),
                DetailRow(f"{self._detail_label('gpu')} {self._detail_label('power')}", self._format_optional_power(self.gpu_power)),
                DetailRow(self._detail_label("source"), self._hardware_bridge_source()),
            ]
        else:
            rows = [
                DetailRow(self._detail_label("network"), "", self._module_accent("network"), is_heading=True),
                DetailRow(self.i18n.UPLOAD_LABEL, self._format_speed_detail(self.upload_speed), constants.graph.UPLOAD_LINE_COLOR),
                DetailRow(self.i18n.DOWNLOAD_LABEL, self._format_speed_detail(self.download_speed), constants.graph.DOWNLOAD_LINE_COLOR),
                DetailRow(self._detail_label("cpu"), "", self._module_accent("cpu"), is_heading=True),
                DetailRow(self._detail_label("usage"), self._format_optional_percent(self.cpu_usage), constants.renderer.PIXEL_HUD_CPU_COLOR),
                DetailRow(self._detail_label("temperature"), self._format_optional_temp(self.cpu_temp)),
                DetailRow(self._detail_label("power"), self._format_optional_power(self.cpu_power)),
                DetailRow("RAM", self._format_memory_detail(self.ram_used, self.ram_total), constants.renderer.PIXEL_HUD_RAM_COLOR),
                DetailRow(self._detail_label("gpu"), "", self._module_accent("gpu"), is_heading=True),
                DetailRow(self._detail_label("usage"), self._format_optional_percent(self.gpu_usage), constants.renderer.PIXEL_HUD_GPU_COLOR),
                DetailRow(self._detail_label("temperature"), self._format_optional_temp(self.gpu_temp)),
                DetailRow(self._detail_label("power"), self._format_optional_power(self.gpu_power)),
                DetailRow("VRAM", self._format_memory_detail(self.vram_used, self.vram_total), constants.renderer.PIXEL_HUD_VRAM_COLOR),
                DetailRow(self._detail_label("source"), self._hardware_bridge_source()),
            ]

        return self._module_title(module_key), rows, accent





    # _load_initial_config removed as it is now handled by ConfigController class


    def _on_theme_changed(self) -> None:
        """Delegates theme change handling."""
        self.theme_manager.on_theme_changed()





    def _init_ui_components(self) -> None:
        """Initialize UI-related elements: icon, tray, event handler."""
        self.logger.debug("Initializing UI components...")

        self.tray_manager = TrayIconManager(self, self.i18n)
        self.tray_manager.initialize()
        
        # Input Handler must be initialized here, after tray_manager and position_manager exist
        self.input_handler = InputHandler(
            widget=self,
            position_manager=self.position_manager,
            tray_manager=self.tray_manager
        )
        
        self.system_event_handler = SystemEventHandler(self)






    def _setup_connections(self) -> None:
        """
        Connects signals from core components and initializes the WinEventHooks for
        stable, debounced visibility management.
        """
        self.logger.debug("Setting up signal connections and WinEventHooks...")
        if not all([self.widget_state, self.timer_manager, self.controller]):
            raise RuntimeError("Core components missing during signal connection setup.")
        try:
            # Connect core component signals
            self.monitor_thread.stats_ready.connect(self.controller.handle_stats)
            self.controller.display_speed_updated.connect(self.update_display_speeds)
            
            # New Hardware signals
            self.controller.cpu_usage_updated.connect(lambda val: self.update_display_hardware(cpu=val))
            self.controller.gpu_usage_updated.connect(lambda val: self.update_display_hardware(gpu=val))
            
            # One-time LHM notice
            self.monitor_thread.lhm_not_detected.connect(self._on_lhm_not_detected)

            # Start the monitoring thread
            self.monitor_thread.start()

            # 1. System Event Handler (replaces manual WinEventHooks)
            self.system_event_handler.foreground_app_changed.connect(self._execute_refresh)
            self.system_event_handler.taskbar_changed.connect(self.update_position)
            self._refresh_cached_layout_mode()
            self.system_event_handler.theme_changed.connect(self._on_theme_changed)
            
            # For immediate hide, we just connect to a lambda that hides self
            self.system_event_handler.immediate_hide_requested.connect(lambda: self.setVisible(False))
            
            # Handle taskbar restarts
            self.system_event_handler.taskbar_restarted.connect(lambda: [QTimer.singleShot(i * constants.timeouts.TASKBAR_RESTART_RECOVERY_DELAY_MS, self._execute_refresh) for i in range(constants.timeouts.TASKBAR_RESTART_RETRIES)])
            
            self.system_event_handler.start()

            
            self.logger.debug("Signal connections and WinEventHooks established successfully.")
        except Exception as e:
            self.logger.error("Error setting up signal connections: %s", e, exc_info=True)
            raise RuntimeError("Failed to establish critical signal connections") from e

        
    def _validate_lazy_imports(self) -> None:
        """Validates lazy imports to catch potential issues early."""
        self.logger.debug("Validating lazy imports...")
        try:
            from netspeedtray.views.settings import SettingsDialog
            from netspeedtray.views.graph import GraphWindow
            self.logger.debug("Lazy imports validated successfully.")
        except ImportError as e:
            self.logger.error("Lazy import validation failed: %s", e, exc_info=True)



    def paintEvent(self, event: QPaintEvent) -> None:
        """Handles all painting for the widget by delegating to the renderer."""
        if not self.isVisible():
            return
        
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            
            # 1. Base Hit-Test Layer (nearly transparent)
            painter.fillRect(self.rect(), QColor(0, 0, 0, 1))

            render_config = self.renderer.config
            display_mode = render_config.widget_display_mode
            if display_mode == "cycle":
                display_mode = self._current_cycle_mode

            # 2. Visual Background
            self.renderer.draw_background(painter, self.rect(), render_config)
            self._reset_module_hit_rects()

            if not self.renderer or not self.current_metrics:
                self._draw_paint_error(painter, "Render Error")
                return
            
            layout_mode = self._cached_layout_mode
            
            # 3. Mini-Graph Layer
            if render_config.graph_enabled:
                self._draw_widget_graph(painter, render_config, display_mode, layout_mode)

            # 4. Foreground Content (Text/HUD)
            painter.setFont(self.current_font)
            self._draw_widget_foreground(painter, render_config, display_mode, layout_mode)
            
        except Exception as e:
            self.logger.error(f"Error in paintEvent: {e}", exc_info=True)
        finally:
            if painter.isActive():
                painter.end()

    def _draw_widget_graph(self, painter: QPainter, config: RenderConfig, mode: str, layout: str) -> None:
        """Draws the mini-graph background layer based on current mode."""
        if mode == "side_by_side":
            return  # Handled inside the segment renderer loop for accurate width scoping
        if mode == "network_only" and getattr(config, 'hardware_label_style', '') == "pixel_hud_blocks":
            return
        elif mode == "cpu_only":
            history = list(self.widget_state.cpu_history)
            self.renderer.draw_mini_graph(painter, self.width(), self.height(), config, history, layout, is_hardware=True, hardware_color=constants.graph.CPU_LINE_COLOR)
        elif mode == "gpu_only":
            history = list(self.widget_state.gpu_history)
            self.renderer.draw_mini_graph(painter, self.width(), self.height(), config, history, layout, is_hardware=True, hardware_color=constants.graph.GPU_LINE_COLOR)
        else:
            history = self.widget_state.get_aggregated_speed_history()
            self.renderer.draw_mini_graph(painter, self.width(), self.height(), config, history, layout)

    def _draw_widget_foreground(self, painter: QPainter, config: RenderConfig, mode: str, layout: str) -> None:
        """Draws the text and HUD foreground layer based on current mode."""
        if mode == "side_by_side":
            self._draw_side_by_side_layout(painter, config, layout)
        elif mode == "network_only":
            up_bytes = (self.upload_speed * constants.network.units.MEGA_DIVISOR) / constants.network.units.BITS_PER_BYTE
            dw_bytes = (self.download_speed * constants.network.units.MEGA_DIVISOR) / constants.network.units.BITS_PER_BYTE
            history = self.widget_state.get_aggregated_speed_history()
            self.renderer.draw_network_speeds(
                painter, up_bytes, dw_bytes, self.width(), self.height(), config, layout,
                speed_history=history,
            )
            self._register_network_hit_rect(0)
        elif mode == "cpu_only":
            ram = (self.ram_used, self.ram_total) if config.monitor_ram_enabled else None
            self.renderer.draw_hardware_stats(painter, self.cpu_usage, None, self.width(), self.height(), config, self.cpu_temp, None, ram, None, layout, cpu_power=self.cpu_power, cpu_history=list(self.widget_state.cpu_history))
            self._register_hardware_hit_rects(True, False, 0)
        elif mode == "gpu_only":
            vram = (self.vram_used, self.vram_total) if config.monitor_vram_enabled else None
            self.renderer.draw_hardware_stats(painter, None, self.gpu_usage, self.width(), self.height(), config, None, self.gpu_temp, None, vram, layout, gpu_power=self.gpu_power, gpu_history=list(self.widget_state.gpu_history))
            self._register_hardware_hit_rects(False, True, 0)
        elif mode == "combined":
            ram = (self.ram_used, self.ram_total) if config.monitor_ram_enabled else None
            vram = (self.vram_used, self.vram_total) if config.monitor_vram_enabled else None
            self.renderer.draw_hardware_stats(painter, self.cpu_usage, self.gpu_usage, self.width(), self.height(), config, self.cpu_temp, self.gpu_temp, ram, vram, layout, cpu_power=self.cpu_power, gpu_power=self.gpu_power, cpu_history=list(self.widget_state.cpu_history), gpu_history=list(self.widget_state.gpu_history))
            self._register_hardware_hit_rects(True, True, 0)

    def _draw_side_by_side_layout(self, painter: QPainter, config: RenderConfig, layout: str) -> None:
        """Helper for multi-segment side-by-side painting."""
        active_keys = []
        stack_hw = getattr(config, 'stack_hardware_stats', False)
        
        for k in config.widget_display_order:
            if k == "network":
                active_keys.append(k)
            elif k == "cpu" and config.monitor_cpu_enabled:
                if stack_hw and config.monitor_gpu_enabled:
                    if "hardware" not in active_keys:
                        active_keys.append("hardware")
                else:
                    active_keys.append("cpu")
            elif k == "gpu" and config.monitor_gpu_enabled:
                if stack_hw and config.monitor_cpu_enabled:
                    if "hardware" not in active_keys:
                        active_keys.append("hardware")
                else:
                    active_keys.append("gpu")
        if not active_keys: active_keys = ["network"]
        
        current_x = 0
        segment_gap = int(getattr(config, 'widget_segment_gap', constants.config.defaults.DEFAULT_WIDGET_SEGMENT_GAP))
        segment_gap = max(0, min(40, segment_gap))
        
        for key in active_keys:
            if key == "network":
                if config.graph_enabled and getattr(config, 'hardware_label_style', '') != "pixel_hud_blocks":
                    painter.save()
                    # Offset the canvas to draw graph only behind the network segment
                    painter.translate(current_x, 0)
                    history = self.widget_state.get_aggregated_speed_history()
                    net_width = getattr(self.layout_manager, '_network_width', self.width())
                    self.renderer.draw_mini_graph(painter, net_width, self.height(), config, history, layout)
                    painter.restore()
                    
                up_bytes = (self.upload_speed * constants.network.units.MEGA_DIVISOR) / constants.network.units.BITS_PER_BYTE
                dw_bytes = (self.download_speed * constants.network.units.MEGA_DIVISOR) / constants.network.units.BITS_PER_BYTE
                history = self.widget_state.get_aggregated_speed_history()
                self.renderer.draw_network_speeds(
                    painter, up_bytes, dw_bytes, self.width(), self.height(), config, layout,
                    x_offset=current_x, speed_history=history,
                )
                self._register_network_hit_rect(current_x)
            elif key == "cpu" and config.monitor_cpu_enabled:
                ram = (self.ram_used, self.ram_total) if config.monitor_ram_enabled else None
                self.renderer.draw_hardware_stats(painter, self.cpu_usage, None, self.width(), self.height(), config, self.cpu_temp, None, ram, None, layout, x_offset=current_x, cpu_power=self.cpu_power, cpu_history=list(self.widget_state.cpu_history))
                self._register_hardware_hit_rects(True, False, current_x)
            elif key == "gpu" and config.monitor_gpu_enabled:
                vram = (self.vram_used, self.vram_total) if config.monitor_vram_enabled else None
                self.renderer.draw_hardware_stats(painter, None, self.gpu_usage, self.width(), self.height(), config, None, self.gpu_temp, None, vram, layout, x_offset=current_x, gpu_power=self.gpu_power, gpu_history=list(self.widget_state.gpu_history))
                self._register_hardware_hit_rects(False, True, current_x)
            elif key == "hardware":
                ram = (self.ram_used, self.ram_total) if config.monitor_ram_enabled else None
                vram = (self.vram_used, self.vram_total) if config.monitor_vram_enabled else None
                self.renderer.draw_hardware_stats(painter, self.cpu_usage, self.gpu_usage, self.width(), self.height(), config, self.cpu_temp, self.gpu_temp, ram, vram, layout, x_offset=current_x, cpu_power=self.cpu_power, gpu_power=self.gpu_power, cpu_history=list(self.widget_state.cpu_history), gpu_history=list(self.widget_state.gpu_history))
                self._register_hardware_hit_rects(True, True, current_x)
            
            if key == "network":
                current_x += getattr(self.layout_manager, '_network_width', self.renderer.get_last_text_rect().width()) + segment_gap
            else:
                current_x += self.renderer.get_last_text_rect().width() + constants.layout.WIDGET_SEGMENT_GAP_BETWEEN_HARDWARE_PX

    def _draw_paint_error(self, painter: Optional[QPainter], text: str) -> None:
        """Draws a visual error indicator on the widget background."""
        try:
            if painter is None or not painter.isActive():
                p = QPainter(self)
                created_painter = True
            else:
                p = painter
                created_painter = False

            error_color = QColor(constants.color.RED)
            error_color.setAlpha(200) # Keep alpha for translucency
            p.fillRect(self.rect(), error_color)
            p.setPen(Qt.GlobalColor.white)
            if self.current_font:
                p.setFont(self.current_font)
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, text)

            if created_painter:
                p.end()

        except Exception as paint_err:
            self.logger.critical(f"CRITICAL: Failed to draw paint error indicator: {paint_err}", exc_info=True)






    def mousePressEvent(self, event: QMouseEvent) -> None:
        """Delegates mouse press events to the InputHandler."""
        if self.input_handler:
            self.input_handler.handle_mouse_press(event)


    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        """Delegates mouse move events to the InputHandler."""
        if self.input_handler:
            self.input_handler.handle_mouse_move(event)


    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        """Delegates mouse release events to the InputHandler."""
        if self.input_handler:
            self.input_handler.handle_mouse_release(event)


    def changeEvent(self, event: QEvent) -> None:
        """
        This event is handled for proper superclass behavior, but all custom
        logic is now managed by the debounced WinEventHooks to prevent blinking.
        """
        super().changeEvent(event)


    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        """Delegates double-click events to the InputHandler."""
        if self.input_handler:
            self.input_handler.handle_double_click(event)

    def _poll_native_click_state(self) -> None:
        """Detect double-clicks even when Qt misses taskbar-window mouse events."""
        try:
            if sys.platform != "win32" or not self.isVisible():
                self._reset_polled_click_state()
                return

            left_down = bool(win32api.GetAsyncKeyState(win32con.VK_LBUTTON) & 0x8000)
            cursor_x, cursor_y = win32gui.GetCursorPos()
            cursor_pos = QPoint(cursor_x, cursor_y)
            inside_widget = self._is_screen_point_inside_widget(cursor_x, cursor_y)

            if left_down and not self._poll_left_down:
                self._poll_press_inside = inside_widget
                self._poll_press_pos = cursor_pos
                self._poll_press_dragging = False
            elif left_down and self._poll_left_down and self._poll_press_pos is not None:
                if (cursor_pos - self._poll_press_pos).manhattanLength() >= QApplication.startDragDistance():
                    self._poll_press_dragging = True
            elif not left_down and self._poll_left_down:
                if self._poll_press_inside and inside_widget and not self._poll_press_dragging:
                    self._handle_polled_click_release(cursor_pos)
                self._poll_press_inside = False
                self._poll_press_pos = None
                self._poll_press_dragging = False

            self._poll_left_down = left_down
        except Exception as e:
            self.logger.error("Error polling native click state: %s", e, exc_info=True)
            self._reset_polled_click_state()

    def _is_screen_point_inside_widget(self, x: int, y: int) -> bool:
        try:
            left, top, right, bottom = win32gui.GetWindowRect(int(self.winId()))
            return left <= x < right and top <= y < bottom
        except Exception:
            rect = self.frameGeometry()
            return rect.contains(QPoint(x, y))

    def _handle_polled_click_release(self, pos: QPoint) -> None:
        now_ms = time.monotonic() * 1000.0
        is_double_click = (
            self._poll_last_click_pos is not None
            and now_ms - self._poll_last_click_time_ms <= QApplication.doubleClickInterval()
            and (pos - self._poll_last_click_pos).manhattanLength() <= QApplication.startDragDistance()
        )

        if is_double_click:
            self._poll_last_click_time_ms = 0.0
            self._poll_last_click_pos = None
            if self.input_handler and hasattr(self.input_handler, "show_hardware_details_once"):
                self.input_handler.show_hardware_details_once(pos)
            else:
                self.show_hardware_detail_overview(pos)
            return

        self._poll_last_click_time_ms = now_ms
        self._poll_last_click_pos = QPoint(pos)

    def _reset_polled_click_state(self) -> None:
        self._poll_left_down = False
        self._poll_press_inside = False
        self._poll_press_pos = None
        self._poll_press_dragging = False


    def leaveEvent(self, event: QEvent) -> None:
        """Hide transient hover detail when the pointer leaves the widget."""
        self.unsetCursor()
        if self.input_handler:
            self.input_handler.handle_leave()
        super().leaveEvent(event)


    def contextMenuEvent(self, event: QContextMenuEvent) -> None:
        """
        Shows the context menu. This handler is the primary mechanism for
        keyboard-invoked context menus and a fallback for mouse events.
        """
        try:
            self.hide_detail_popup(force=True)
            if self.tray_manager:
                self.tray_manager.show_context_menu()
            event.accept()
        except Exception as e:
            self.logger.error(f"Error showing context menu: {e}", exc_info=True)
            event.ignore()


    def showEvent(self, event: QShowEvent) -> None:
        self.logger.debug(f"Widget showEvent triggered. New visibility: {self.isVisible()}")
        super().showEvent(event)


    def hideEvent(self, event: QHideEvent) -> None:
        self.logger.debug("Widget hideEvent triggered.")
        self.hide_detail_popup(force=True)
        super().hideEvent(event)


    def closeEvent(self, event: QCloseEvent) -> None:
        """
        Handles widget closure.
        By default, closing the widget just hides it (standard Tray behavior).
        The application only exits if _will_quit_app is set to True.
        """
        if self._will_quit_app:
            self.logger.info("Application exit requested. Cleaning up...")
            try:
                self.cleanup()
                event.accept()
                
                # Explicitly ensure the app quits loop
                app = QApplication.instance()
                if app:
                    app.quit()
                    
            except Exception as e:
                self.logger.error(f"Error during shutdown cleanup: {e}", exc_info=True)
                event.accept()
        else:
            self.logger.debug("Close event received but not quitting app. Hiding widget.")
            self.setVisible(False)
            event.ignore() # Prevent destruction of the widget

    def fully_exit_application(self) -> None:
        """Helper to cleanly exit the entire application."""
        self.logger.info("Fully exiting application...")
        self._will_quit_app = True
        self.close()






 

    @property
    def position_manager_property(self):
        # Expose position manager for binding if needed, though self.position_manager exists
        return self.position_manager

    def _ensure_win32_topmost(self) -> None:
        """Delegates to PositionManager."""
        self.position_manager.ensure_topmost()

    def _enforce_topmost_status(self) -> None:
        """Delegates to PositionManager."""
        self.position_manager.enforce_topmost_status()

    def reset_to_default_position(self) -> None:
        """
        Resets the widget to its default position using PositionManager.
        """
        self.logger.info("Resetting widget position to default.")
        self.position_manager.reset_to_default()
        
        # Save the cleared config state
        self.update_config({'position_x': None, 'position_y': None})


    def apply_all_settings(self) -> None:
        """Delegates to ConfigController."""
        self.config_controller.apply_all_settings()


    def handle_settings_changed(self, updated_config: Dict[str, Any], save_to_disk: bool = True) -> None:
        """Delegates to ConfigController."""
        self.config_controller.handle_settings_changed(updated_config, save_to_disk)


    def show_settings(self) -> None:
        """Creates and displays the settings dialog as a normal, non-modal window."""
        self.logger.debug("Showing settings dialog...")
        try:
            from netspeedtray.views.settings import SettingsDialog

            if self.settings_dialog is None:
                self.logger.debug("Creating new SettingsDialog instance.")
                # Create the dialog as a top-level window (parent=None)
                self.settings_dialog = SettingsDialog(
                    main_widget=self,
                    config=self.config.copy(),
                    version=constants.app.VERSION,
                    i18n=self.i18n,
                    available_interfaces=self.get_unified_interface_list(),
                    is_startup_enabled=self.is_startup_enabled()
                )
                # Connect signal for live preview updates (don't save to disk during preview)
                self.settings_dialog.settings_changed.connect(
                    lambda cfg: self.handle_settings_changed(cfg, save_to_disk=False)
                )

            if not self.settings_dialog.isVisible():
                # Also update the interface list when showing an existing dialog
                self.settings_dialog.update_interface_list(self.get_unified_interface_list())
                self.settings_dialog.reset_with_config(
                    config=self.config.copy(),
                    is_startup_enabled=self.is_startup_enabled()
                )
                self.settings_dialog.show()
            else:
                self.logger.debug("Settings dialog already visible. Activating.")
                # Also update the interface list when re-activating the dialog
                self.settings_dialog.update_interface_list(self.get_unified_interface_list())
                self.settings_dialog.raise_()
                self.settings_dialog.activateWindow()

        except Exception as e:
            self.logger.error(f"Error showing settings: {e}", exc_info=True)
            QMessageBox.critical(self, self.i18n.ERROR_TITLE, f"Could not open settings:\n\n{str(e)}")



    def _rollback_config(self, old_config: Dict[str, Any]) -> None:
        """Delegates to ConfigController."""
        self.config_controller.rollback_config(old_config)


    def update_config(self, updates: Dict[str, Any], save_to_disk: bool = True) -> None:
        """Delegates to ConfigController."""
        self.config_controller.update_config(updates, save_to_disk)


    def handle_graph_settings_update(self, updates: Dict[str, Any]) -> None:
        """
        Public method called by the GraphWindow to update and save configuration.
        This centralizes the saving logic and prevents race conditions.
        """
        self.logger.debug(f"Received settings update from graph window: {updates}")
        # The update_config method already updates the in-memory config and saves to disk.
        # We can just call it directly.
        self.update_config(updates)





    def open_graph_window(self) -> None:
        """Creates and displays the speed history graph window."""
        self.logger.debug("Request to show graph window.")
        if not self.i18n or not self.config or not self.widget_state:
            self.logger.error("Cannot show graph: Required components missing.")
            QMessageBox.critical(self, "Error", "Internal error: Required components not available.")
            return

        try:
            # --- Optimization: Lazy import ---
            # By placing the import here, matplotlib is only loaded when the user
            # requests the graph, speeding up initial application startup.
            from netspeedtray.views.graph import GraphWindow

            if self.graph_window is None:
                self.logger.debug("Creating new GraphWindow instance.")
                
                self.graph_window = GraphWindow(
                    main_widget=self, # Pass self as the main_widget reference
                    parent=None,      # Set the Qt parent to None to decouple
                    i18n=self.i18n,
                    session_start_time=self.session_start_time
                )
                
                # Connect the signal AFTER the instance exists.
                self.widget_state.db_worker.database_updated.connect(
                    self.graph_window._populate_interface_filter
                )
                
                # CLEAN FIX: Listen for destruction to restore Z-order/Visibility
                # This decouples the child from the parent's implementation details.
                self.graph_window.window_closed.connect(self._on_graph_window_closed)

                # Show the window.
                self.graph_window.show()

            else:
                self.logger.debug("Graph window already exists. Showing and activating.")
                self.graph_window.show()
                self.graph_window.showNormal()
                self.graph_window.raise_()
                self.graph_window.activateWindow()
        except Exception as e:
            self.logger.error(f"Error showing graph window: {e}", exc_info=True)
            QMessageBox.critical(self, "Error", f"Could not open the graph window:\n\n{str(e)}")

    def open_app_activity_window(self) -> None:
        """Creates and displays the per-application network activity window."""
        self.logger.debug("Request to show app activity window.")
        if not self.i18n:
            self.logger.error("Cannot show app activity view: i18n not initialized.")
            QMessageBox.critical(self, "Error", "Internal error: Required components not available.")
            return

        try:
            from netspeedtray.views.app_activity import AppActivityWindow

            if self.app_activity_window is None or not self.app_activity_window.isVisible():
                self.logger.debug("Creating new AppActivityWindow instance.")
                self.app_activity_window = AppActivityWindow(
                    main_widget=self,
                    parent=None,
                    i18n=self.i18n,
                )
                self.app_activity_window.window_closed.connect(self._on_app_activity_window_closed)
                self.app_activity_window.show()
            else:
                self.logger.debug("App activity window already exists. Activating.")
                self.app_activity_window.show()
                self.app_activity_window.raise_()
                self.app_activity_window.activateWindow()
        except Exception as e:
            self.logger.error(f"Error showing app activity window: {e}", exc_info=True)
            QMessageBox.critical(self, self.i18n.ERROR_TITLE, f"Could not open app activity window:\n\n{str(e)}")


    def check_for_updates(self) -> None:
        """Manually trigger an update check (from menu)."""
        if self.update_checker:
            self.update_checker.update_available.connect(self._on_update_available_manual, Qt.ConnectionType.SingleShotConnection)
            self.update_checker.up_to_date.connect(self._on_up_to_date_manual, Qt.ConnectionType.SingleShotConnection)
            self.update_checker.check_failed.connect(self._on_check_failed_manual, Qt.ConnectionType.SingleShotConnection)
            self.update_checker.check_now()

    def _on_update_available(self, latest_version: str, release_url: str) -> None:
        """Handle update available from automatic startup check."""
        self._show_update_dialog(latest_version, release_url)

    def _on_update_available_manual(self, latest_version: str, release_url: str) -> None:
        """Handle update available from manual menu check."""
        self._show_update_dialog(latest_version, release_url)

    def _on_up_to_date_manual(self) -> None:
        """Show up-to-date message for manual check."""
        QMessageBox.information(
            None, self.i18n.UPDATE_UP_TO_DATE_TITLE,
            self.i18n.UPDATE_UP_TO_DATE_TEXT.format(current=constants.app.VERSION)
        )

    def _on_check_failed_manual(self, error: str) -> None:
        """Show error message for manual check."""
        QMessageBox.warning(self, "Update Check", self.i18n.UPDATE_CHECK_FAILED_TEXT)

    def _show_update_dialog(self, latest_version: str, release_url: str) -> None:
        """Show update available dialog with Download / Skip / Not Now."""
        msg = QMessageBox(self)
        msg.setWindowTitle(self.i18n.UPDATE_AVAILABLE_TITLE)
        msg.setText(self.i18n.UPDATE_AVAILABLE_TEXT.format(
            current=constants.app.VERSION, latest=latest_version.lstrip("vV")
        ))
        msg.setIcon(QMessageBox.Icon.Information)

        download_btn = msg.addButton(self.i18n.UPDATE_DOWNLOAD_BUTTON, QMessageBox.ButtonRole.AcceptRole)
        skip_btn = msg.addButton(self.i18n.UPDATE_SKIP_BUTTON, QMessageBox.ButtonRole.DestructiveRole)
        msg.addButton(self.i18n.UPDATE_DISMISS_BUTTON, QMessageBox.ButtonRole.RejectRole)

        msg.exec()

        if msg.clickedButton() == download_btn:
            import webbrowser
            webbrowser.open(release_url)
        elif msg.clickedButton() == skip_btn:
            self.config["skipped_version"] = latest_version.lstrip("vV")
            self.update_config({"skipped_version": self.config["skipped_version"]})

    def show_support_dialog(self) -> None:
        """Show the support/donate dialog."""
        import webbrowser
        msg = QMessageBox(self)
        msg.setWindowTitle(self.i18n.SUPPORT_DIALOG_TITLE)
        msg.setText(self.i18n.SUPPORT_DIALOG_TEXT)
        msg.setIcon(QMessageBox.Icon.Information)

        github_btn = msg.addButton(self.i18n.SUPPORT_GITHUB_SPONSORS, QMessageBox.ButtonRole.ActionRole)
        kofi_btn = msg.addButton(self.i18n.SUPPORT_KOFI, QMessageBox.ButtonRole.ActionRole)
        bmc_btn = msg.addButton(self.i18n.SUPPORT_BMC, QMessageBox.ButtonRole.ActionRole)
        star_btn = msg.addButton(self.i18n.SUPPORT_STAR_GITHUB, QMessageBox.ButtonRole.ActionRole)
        msg.addButton(QMessageBox.StandardButton.Close)

        msg.exec()

        if msg.clickedButton() == github_btn:
            webbrowser.open("https://github.com/sponsors/erez-c137")
        elif msg.clickedButton() == kofi_btn:
            webbrowser.open("https://ko-fi.com/erezc137")
        elif msg.clickedButton() == bmc_btn:
            webbrowser.open("https://buymeacoffee.com/erez.c137")
        elif msg.clickedButton() == star_btn:
            webbrowser.open("https://github.com/erez-c137/NetSpeedTray")

    def _on_lhm_not_detected(self) -> None:
        """Show a one-time notice when temperature/power readings require LibreHardwareMonitor."""
        QMessageBox.information(
            self,
            self.i18n.LHM_NOT_DETECTED_TITLE,
            self.i18n.LHM_NOT_DETECTED_TEXT
        )

    def _on_graph_window_closed(self) -> None:
        """
        Handles the destruction of the graph window.
        Triggers a delayed refresh to restore the main widget's Z-order and visibility
        after the focus transition completes.
        """
        self.logger.debug("Graph window destroyed. Restoring main widget state.")
        self.graph_window = None
        
        # Explicitly force visibility first to prevent "disappeared" state
        # The subsequent refresh will handle obstruction logic, but we assume
        # the user wants to see the widget after closing the graph.
        self.show()
        self.raise_()
        self.activateWindow()

        # Use singleShot with a slightly longer delay to allow Windows focus settling
        QTimer.singleShot(constants.timeouts.GRAPH_CLOSE_REFRESH_DELAY_MS, self._execute_refresh)

    def _on_app_activity_window_closed(self) -> None:
        """Handles app activity window destruction."""
        self.logger.debug("App activity window destroyed.")
        self.app_activity_window = None


    # update_config (redundant definition) removed


    def get_config(self) -> Dict[str, Any]:
        return self.config.copy() if self.config else {}


    def get_widget_size(self) -> QSize:
        return self.size()


    def set_app_version(self, version: str) -> None:
        self.app_version = version
        self.logger.debug(f"Application version set to: {version}")


    def update_position(self) -> None:
        """
        The single, authoritative method to reposition the widget based on its current state.
        """
        self.logger.debug("Authoritative request to update widget position.")
        self._refresh_cached_layout_mode()
        if self.position_manager:
            try:
                self.position_manager.update_position()
            except Exception as e:
                self.logger.error(f"Error during position update: {e}", exc_info=True)


    def _refresh_cached_layout_mode(self) -> None:
        """Update cached layout mode from current taskbar edge position."""
        try:
            taskbar_info = get_taskbar_info()
            edge = taskbar_info.get_edge_position()
            self._cached_layout_mode = 'horizontal' if edge in (
                constants.TaskbarEdge.LEFT, constants.TaskbarEdge.RIGHT
            ) else 'vertical'
        except Exception:
            pass  # Keep previous cached value

    def is_startup_enabled(self, force_check: bool = False) -> bool:
        """Checks if startup is enabled via StartupManager."""
        return self.startup_manager.is_startup_enabled(force_check)


    def toggle_startup(self, enable: bool) -> None:
        """Toggles startup via StartupManager."""
        try:
            self.startup_manager.toggle_startup(enable)
            self.config['start_with_windows'] = enable
            self.update_config({'start_with_windows': enable})
            self.logger.info(f"Application startup successfully {'enabled' if enable else 'disabled'}.")
        except Exception as e:
            self.logger.error(f"Failed to {'enable' if enable else 'disable'} startup: {e}", exc_info=True)
            QMessageBox.warning(
                self,
                "Startup Error",
                f"Could not {'enable' if enable else 'disable'} automatic startup.\n\n{e}"
            )


    def _synchronize_startup_task(self) -> None:
        """Synchronizes startup state using StartupManager."""
        should_be_enabled = self.config.get("start_with_windows", constants.config.defaults.DEFAULT_START_WITH_WINDOWS)
        self.startup_manager.synchronize_startup_task(should_be_enabled)


    def update_retention_period(self, days: int) -> None:
        """
        Public method called by child windows (like GraphWindow) to update
        the data retention period and trigger the necessary backend logic.
        
        Args:
            days: The new retention period in days.
        """
        self.logger.info("Request received to update data retention period to %d days.", days)
        if not self.widget_state:
            self.logger.error("Cannot update retention period: WidgetState is not available.")
            return
        
        # 1. Update the in-memory config dictionary.
        self.config["keep_data"] = days
        
        # 2. Persist the change immediately to the config file.
        self.update_config(self.config)
        
        # 3. Notify the WidgetState, which will trigger the grace period logic.
        self.widget_state.update_retention_period()

    def get_unified_interface_list(self) -> List[str]:
        """
        Returns a comprehensive, sorted list of network interfaces by combining
        currently active interfaces with all interfaces found in the history database.
        This serves as the single source of truth for all UI elements.
        """
        if not self.controller or not self.widget_state:
            self.logger.warning("Cannot get unified interface list: core components not initialized.")
            return []
        
        try:
            # Call the controller directly, as it is the true source of the live list.
            live_interfaces = set(self.controller.get_available_interfaces())
            
            # Get interfaces from the database history
            historical_interfaces = set(self.widget_state.get_distinct_interfaces())
            
            # Combine them, which automatically handles duplicates, then sort for a consistent UI.
            unified_list = sorted(list(live_interfaces.union(historical_interfaces)))
            
            self.logger.debug(f"Unified interface list created with {len(unified_list)} items.")
            return unified_list
        except Exception as e:
            self.logger.error(f"Error creating unified interface list: {e}", exc_info=True)
            return [] # Return an empty list on error
        

    def get_active_interfaces(self) -> List[str]:
        """
        Provides a passthrough to the controller's method for getting a list
        of currently active network interfaces.
        """
        if self.controller:
            return self.controller.get_active_interfaces()
        return []


    def cleanup(self) -> None:
        """Performs necessary cleanup and a single, final save of the configuration."""
        self.logger.debug("Performing widget cleanup...")
        try:
            # --- Stop all external event listeners and timers ---
            # self.foreground_hook and movesize_hook are likely legacy, but keeping check is harmless
            if hasattr(self, 'system_event_handler') and self.system_event_handler:
                self.system_event_handler.stop()
            elif hasattr(self, 'foreground_hook') and self.foreground_hook: 
                self.foreground_hook.stop()
            
            # Stop PositionManager monitoring
            if self.position_manager:
                self.position_manager.stop_monitoring()
            
            if self._state_watcher_timer.isActive(): self._state_watcher_timer.stop()
            if self._hover_detail_timer.isActive(): self._hover_detail_timer.stop()
            if self.detail_popup:
                self.detail_popup.close()
                self.detail_popup = None
            
            # --- Stop the background monitor thread ---
            if hasattr(self, 'monitor_thread') and self.monitor_thread:
                self.logger.debug("Stopping StatsMonitorThread...")
                self.monitor_thread.stop()

            # --- Clean up core components ---
            if self.timer_manager: self.timer_manager.cleanup()
            if self.controller: self.controller.cleanup()
            if self.widget_state: self.widget_state.cleanup()

            # (The rest of the cleanup method for saving config remains the same)
            if self.graph_window:
                final_graph_settings = {
                    "graph_window_pos": {"x": self.graph_window.pos().x(), "y": self.graph_window.pos().y()},
                    "dark_mode": self.graph_window._is_dark_mode,
                    "history_period_slider_value": self.graph_window._history_period_value,
                }
                self.update_config(final_graph_settings, save_to_disk=False)
                self.graph_window._is_closing = True
                self.graph_window.close()
                self.graph_window = None

            if self.app_activity_window:
                self.app_activity_window.close()
                self.app_activity_window = None

            if self.config.get("free_move", False):
                pos = self.pos()
                self.update_config({"position_x": pos.x(), "position_y": pos.y()}, save_to_disk=False)
            else:
                self.update_config({"position_x": None, "position_y": None}, save_to_disk=False)
            
            self.logger.debug("Performing final configuration save...")
            self.config_manager.save(self.config)

            self.logger.debug("Widget cleanup finished successfully.")
        except Exception as e:
            self.logger.error(f"Unexpected error during cleanup: %s", e, exc_info=True)
