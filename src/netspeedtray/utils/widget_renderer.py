"""
Widget rendering utilities for NetSpeedTray.

Handles drawing of network speeds and an optional mini graph for NetworkSpeedWidget, using
a configurable RenderConfig derived from the main application configuration. This renderer
supports multiple layouts (e.g., vertical, horizontal) to adapt to different UI constraints.
"""

import logging
import math
from typing import Any, Dict, List, Optional, Tuple, TYPE_CHECKING
from dataclasses import dataclass, field

from netspeedtray.core.widget_state import SpeedDataSnapshot, AggregatedSpeedData
from netspeedtray.utils.helpers import format_speed, calculate_monotone_cubic_interpolation
from PyQt6.QtGui import QPainter, QColor, QFont, QFontMetrics, QPen, QPainterPath
from PyQt6.QtCore import Qt, QPointF, QRect
from netspeedtray import constants

logger = logging.getLogger("NetSpeedTray.WidgetRenderer")


@dataclass
class RenderConfig:
    """A data class holding a snapshot of all configuration relevant to rendering."""
    # ... (existing fields) ...
    color_coding: bool
    graph_enabled: bool
    high_speed_threshold: float
    low_speed_threshold: float
    arrow_width: int
    font_family: str
    font_size: int
    font_weight: int
    default_color: str
    high_speed_color: str
    low_speed_color: str
    background_color: str = field(default_factory=lambda: constants.config.defaults.DEFAULT_BACKGROUND_COLOR)
    background_opacity: float = field(default_factory=lambda: constants.config.defaults.DEFAULT_BACKGROUND_OPACITY / 100.0)
    graph_opacity: float = field(default_factory=lambda: constants.config.defaults.DEFAULT_GRAPH_OPACITY / 100.0)
    speed_display_mode: str = constants.config.defaults.DEFAULT_SPEED_DISPLAY_MODE
    decimal_places: int = constants.config.defaults.DEFAULT_DECIMAL_PLACES
    text_alignment: str = constants.config.defaults.DEFAULT_TEXT_ALIGNMENT
    force_decimals: bool = False
    unit_type: str = constants.config.defaults.DEFAULT_UNIT_TYPE
    swap_upload_download: bool = constants.config.defaults.DEFAULT_SWAP_UPLOAD_DOWNLOAD
    hide_arrows: bool = constants.config.defaults.DEFAULT_HIDE_ARROWS
    hide_unit_suffix: bool = constants.config.defaults.DEFAULT_HIDE_UNIT_SUFFIX
    short_unit_labels: bool = constants.config.defaults.DEFAULT_SHORT_UNIT_LABELS
    max_samples: int = 1800 # Default 30 mins * 60s
    use_separate_arrow_font: bool = False
    arrow_font_family: str = constants.config.defaults.DEFAULT_FONT_FAMILY
    arrow_font_size: int = 9
    arrow_font_weight: int = constants.fonts.WEIGHT_DEMIBOLD
    
    # New: Hardware Monitoring Toggles
    monitor_cpu_enabled: bool = False
    monitor_gpu_enabled: bool = False
    monitor_ram_enabled: bool = False
    monitor_vram_enabled: bool = False
    stack_hardware_stats: bool = False
    hardware_label_style: str = "icons_colored"
    widget_display_mode: str = "network_only"
    widget_display_order: List[str] = field(default_factory=lambda: ["network", "cpu", "gpu"])
    widget_segment_gap: int = constants.config.defaults.DEFAULT_WIDGET_SEGMENT_GAP
    show_hardware_temps: bool = False
    show_hardware_power: bool = False


    @classmethod
    def from_dict(cls, config: Dict[str, Any]) -> 'RenderConfig':
        """Creates a RenderConfig instance from a standard application config dictionary."""
        try:
            opacity_raw = config.get('graph_opacity', constants.config.defaults.DEFAULT_GRAPH_OPACITY)
            opacity = float(opacity_raw) / 100.0 if opacity_raw is not None else (constants.config.defaults.DEFAULT_GRAPH_OPACITY / 100.0)
            
            hist_mins = int(config.get('history_minutes', constants.config.defaults.DEFAULT_HISTORY_MINUTES))
            rate = float(config.get('update_rate', constants.config.defaults.DEFAULT_UPDATE_RATE))
            if rate <= 0: rate = 1.0
            max_samples = int((hist_mins * 60) / rate)

            weight_raw = config.get('font_weight', constants.fonts.WEIGHT_DEMIBOLD)
            if isinstance(weight_raw, str):
                weight_val = {
                    "normal": constants.fonts.WEIGHT_NORMAL, 
                    "bold": constants.fonts.WEIGHT_BOLD
                }.get(weight_raw.lower(), constants.fonts.WEIGHT_NORMAL)
            else:
                try: weight_val = int(weight_raw)
                except: weight_val = constants.fonts.WEIGHT_DEMIBOLD

            return cls(
                color_coding=bool(config.get('color_coding', constants.config.defaults.DEFAULT_COLOR_CODING)),
                graph_enabled=bool(config.get('graph_enabled', constants.config.defaults.DEFAULT_GRAPH_ENABLED)),
                high_speed_threshold=float(config.get('high_speed_threshold', constants.config.defaults.DEFAULT_HIGH_SPEED_THRESHOLD)),
                low_speed_threshold=float(config.get('low_speed_threshold', constants.config.defaults.DEFAULT_LOW_SPEED_THRESHOLD)),
                arrow_width=constants.renderer.DEFAULT_ARROW_WIDTH,
                font_family=str(config.get('font_family', constants.config.defaults.DEFAULT_FONT_FAMILY)),
                font_size=int(config.get('font_size', constants.config.defaults.DEFAULT_FONT_SIZE)),
                font_weight=weight_val,
                default_color=str(config.get('default_color', constants.config.defaults.DEFAULT_COLOR)),
                high_speed_color=str(config.get('high_speed_color', constants.config.defaults.DEFAULT_HIGH_SPEED_COLOR)),
                low_speed_color=str(config.get('low_speed_color', constants.config.defaults.DEFAULT_LOW_SPEED_COLOR)),
                background_color=str(config.get('background_color', constants.config.defaults.DEFAULT_BACKGROUND_COLOR)),
                background_opacity=max(0.0, min(1.0, float(config.get('background_opacity', constants.config.defaults.DEFAULT_BACKGROUND_OPACITY)) / 100.0)),
                graph_opacity=max(0.0, min(1.0, opacity)),
                speed_display_mode=str(config.get('speed_display_mode', constants.config.defaults.DEFAULT_SPEED_DISPLAY_MODE)),
                decimal_places=int(config.get('decimal_places', constants.config.defaults.DEFAULT_DECIMAL_PLACES)),
                text_alignment=str(config.get('text_alignment', constants.config.defaults.DEFAULT_TEXT_ALIGNMENT)),
                force_decimals=bool(config.get('force_decimals', constants.config.defaults.DEFAULT_FORCE_DECIMALS)),
                unit_type=str(config.get('unit_type', constants.config.defaults.DEFAULT_UNIT_TYPE)),
                swap_upload_download=bool(config.get('swap_upload_download', constants.config.defaults.DEFAULT_SWAP_UPLOAD_DOWNLOAD)),
                hide_arrows=bool(config.get('hide_arrows', constants.config.defaults.DEFAULT_HIDE_ARROWS)),
                hide_unit_suffix=bool(config.get('hide_unit_suffix', constants.config.defaults.DEFAULT_HIDE_UNIT_SUFFIX)),
                hardware_label_style=str(config.get('hardware_label_style', 'icons_colored')),
                short_unit_labels=bool(config.get('short_unit_labels', constants.config.defaults.DEFAULT_SHORT_UNIT_LABELS)),
                max_samples=max_samples,
                use_separate_arrow_font=bool(config.get('use_separate_arrow_font', False)),
                arrow_font_family=str(config.get('arrow_font_family', constants.config.defaults.DEFAULT_FONT_FAMILY)),
                arrow_font_size=int(config.get('arrow_font_size', constants.config.defaults.DEFAULT_FONT_SIZE)),
                arrow_font_weight=int(config.get('arrow_font_weight', constants.fonts.WEIGHT_DEMIBOLD)),
                
                # New
                monitor_cpu_enabled=bool(config.get('monitor_cpu_enabled', False)),
                monitor_gpu_enabled=bool(config.get('monitor_gpu_enabled', False)),
                monitor_ram_enabled=bool(config.get('monitor_ram_enabled', False)),
                monitor_vram_enabled=bool(config.get('monitor_vram_enabled', False)),
                stack_hardware_stats=bool(config.get('stack_hardware_stats', False)),
                widget_display_mode=str(config.get('widget_display_mode', 'network_only')),
                widget_display_order=list(config.get('widget_display_order', ["network", "cpu", "gpu"])),
                widget_segment_gap=int(config.get('widget_segment_gap', constants.config.defaults.DEFAULT_WIDGET_SEGMENT_GAP)),
                show_hardware_temps=bool(config.get('show_hardware_temps', False)),
                show_hardware_power=bool(config.get('show_hardware_power', False))
            )
        except Exception as e:
            logger.error("Failed to create RenderConfig: %s", e)
            raise ValueError(f"Invalid rendering config: {e}")


class WidgetRenderer:
    """
    Renders network speeds and optional mini graph for NetworkSpeedWidget.
    """
    def __init__(self, config: Dict[str, Any], i18n) -> None:
            """
            Initializes renderer with config, handling setup errors.
            """
            self.logger = logger
            self.i18n = i18n
            
            # Ensure config is a RenderConfig object if a dict is passed
            if isinstance(config, dict):
                self.config = RenderConfig.from_dict(config)
            else:
                self.config = config
                
            try:
                self.paused = False
                
                # Bounding rect for coordinates
                self._last_text_rect = QRect()
                
                # Mini graph state cache tracking
                self._last_widget_size = (0, 0)
                self._last_history_hash = 0
                self._cached_upload_points = []
                self._cached_download_points = []
                
                # Caching for high-frequency paint events
                self._cached_pens = {}
                self._cached_bg_color = None
                self._cached_bg_opacity = -1.0
                self._refresh_resource_cache()
                
                self.logger.debug("WidgetRenderer initialized.")
            except Exception as e:
                self.logger.error("Failed to initialize WidgetRenderer: %s", e)
                # Fail gracefully
                self.config = None
                self.font = QFont()
                self.metrics = QFontMetrics(self.font)
                raise RuntimeError("Renderer initialization failed") from e

    def _refresh_resource_cache(self) -> None:
        """Pre-calculates colors, fonts, and pens to avoid allocation in paint loop."""
        if not self.config:
            return
            
        self.default_color = QColor(self.config.default_color)
        self.high_color = QColor(self.config.high_speed_color)
        self.low_color = QColor(self.config.low_speed_color)
        
        weight = int(self.config.font_weight)
        self.font = QFont(self.config.font_family, self.config.font_size, weight)
        self.metrics = QFontMetrics(self.font)
        
        if self.config.use_separate_arrow_font:
            self.arrow_font = QFont(self.config.arrow_font_family, self.config.arrow_font_size, int(self.config.arrow_font_weight))
        else:
            self.arrow_font = self.font
        self.arrow_metrics = QFontMetrics(self.arrow_font)
        
        # Pre-cache pens
        self._cached_pens = {
            'default': QPen(self.default_color),
            'high': QPen(self.high_color),
            'low': QPen(self.low_color),
            'cpu': QPen(QColor(constants.renderer.CPU_LINE_COLOR)),
            'gpu': QPen(QColor(constants.renderer.GPU_LINE_COLOR))
        }


    def _draw_error(self, painter: QPainter, rect: QRect, message: str) -> None:
        """Draws an error message on the widget."""
        painter.save()
        painter.fillRect(rect, QColor(150, 0, 0, 200))
        painter.setPen(Qt.GlobalColor.white)
        # Use simple fallback if config failed
        base_size = self.config.font_size if self.config else 9
        error_font = QFont(self.font)
        error_font.setPointSize(max(6, base_size - 2))
        painter.setFont(error_font)
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, message)
        painter.restore()


    def draw_background(self, painter: QPainter, rect: QRect, config: RenderConfig) -> None:
        """Draws the widget background. Ensures at least minimal opacity for hit testing."""
        # Check if we need to refresh cache (if config values moved)
        if (self._cached_bg_color is None or 
            self._cached_bg_opacity != config.background_opacity or
            self.config.background_color != config.background_color):
            
            self._cached_bg_color = QColor(config.background_color)
            self._cached_bg_opacity = config.background_opacity
            # Ensure minimum opacity for hit-testing
            min_alpha = 1.0 / 255.0
            self._cached_bg_color.setAlphaF(max(config.background_opacity, min_alpha))
            
        painter.fillRect(rect, self._cached_bg_color)

    def draw_network_speeds(self, painter: QPainter, upload: float, download: float, width: int,
                            height: int, config: RenderConfig, layout_mode: str = 'vertical',
                            x_offset: int = 0, fixed_width: Optional[int] = None,
                            speed_history: Optional[List[Any]] = None) -> None:
        """Draws current upload and download speeds."""
        try:
            # Format speeds
            up_val, up_unit = format_speed(
                upload, self.i18n, force_mega_unit=(config.speed_display_mode == "always_mbps"),
                decimal_places=config.decimal_places, unit_type=config.unit_type,
                short_labels=config.short_unit_labels, split_unit=True
            )
            dw_val, dw_unit = format_speed(
                download, self.i18n, force_mega_unit=(config.speed_display_mode == "always_mbps"),
                decimal_places=config.decimal_places, unit_type=config.unit_type,
                short_labels=config.short_unit_labels, split_unit=True
            )

            if getattr(config, "hardware_label_style", "") == "pixel_hud_blocks":
                self._draw_network_pixel_hud_block(
                    painter,
                    upload,
                    download,
                    up_val,
                    dw_val,
                    width,
                    height,
                    config,
                    x_offset,
                    speed_history,
                )
                return

            painter.setFont(self.font)
            line_height = self.metrics.height()
            ascent = self.metrics.ascent()
            
            # --- FIXED/DYNAMIC WIDTH CALCULATIONS ---
            # We want units to stay put for 3 digits, but move for 4.
            from netspeedtray.utils.helpers import get_reference_value_string
            
            # 1. Base 3-digit width for alignment stability
            # We use a 3-digit ref string to pin the "normal" unit position
            ref_str_3 = get_reference_value_string(False, config.decimal_places, config.unit_type, min_digits=3)
            base_number_width = self.metrics.horizontalAdvance(ref_str_3)
            
            # 2. Actual max width of currently displayed values
            actual_up_width = self.metrics.horizontalAdvance(up_val)
            actual_dw_width = self.metrics.horizontalAdvance(dw_val)
            actual_max_width = max(actual_up_width, actual_dw_width)
            
            # The area width follows the baseline, but expands if actual values are wider (4 digits)
            number_area_width = max(base_number_width, actual_max_width)

            # 3. Arrow Width (use max of UP/DW arrows)
            arrow_up = self.i18n.UPLOAD_ARROW
            arrow_dw = self.i18n.DOWNLOAD_ARROW
            max_arrow_width = max(
                self.arrow_metrics.horizontalAdvance(arrow_up),
                self.arrow_metrics.horizontalAdvance(arrow_dw)
            ) if not config.hide_arrows else 0
            
            # Constants for gaps
            arrow_gap = constants.renderer.ARROW_NUMBER_GAP if not config.hide_arrows else 0
            unit_gap = constants.renderer.VALUE_UNIT_GAP if not config.hide_unit_suffix else 0
            vertical_gap = 1
            margin = constants.renderer.TEXT_MARGIN
            
            # Fixed Offsets (Arrow and Number start are fixed)
            arrow_x = x_offset + margin
            number_x = arrow_x + max_arrow_width + arrow_gap
            
            # Unit offset is relative to the (potentially expanded) number area
            unit_x = number_x + number_area_width + unit_gap
            
            # Default Vertical Layout (Stack UP over DW)
            total_height = (line_height * 2) + vertical_gap
            top_y = int((height - total_height) / 2 + ascent)
            
            # Draw top line (upload by default, download when swapped)
            top_val, top_unit, top_is_upload = (dw_val, dw_unit, False) if config.swap_upload_download else (up_val, up_unit, True)
            self._draw_speed_line(painter, top_is_upload, top_val, top_unit, arrow_x, number_x, unit_x, top_y, config, number_area_width)

            # Draw bottom line (download by default, upload when swapped)
            dw_y = top_y + line_height + vertical_gap
            bot_val, bot_unit, bot_is_upload = (up_val, up_unit, True) if config.swap_upload_download else (dw_val, dw_unit, False)
            self._draw_speed_line(painter, bot_is_upload, bot_val, bot_unit, arrow_x, number_x, unit_x, dw_y, config, number_area_width)

            # Update bounding rect for context menu positioning
            max_unit_width = max(self.metrics.horizontalAdvance(up_unit), self.metrics.horizontalAdvance(dw_unit)) if not config.hide_unit_suffix else 0
            total_width = (unit_x - arrow_x) + max_unit_width
            self._last_text_rect = QRect(arrow_x, top_y, total_width, total_height)

        except Exception as e:
            self.logger.error("Failed to draw network speeds: %s", e)

    def _draw_speed_line(self, painter: QPainter, is_upload: bool, val: str, unit: str, arrow_x: int, number_x: int, unit_x: int, y: int, config: RenderConfig, number_area_width: int) -> None:
        """Unified helper to draw a single speed line (Arrow + Value + Unit) with stable alignment."""
        # Color coding
        if config.color_coding:
            try:
                # Parse back to float, respecting the locale decimal separator.
                # e.g. German "99,9" must become "99.9", not "999".
                decimal_sep = getattr(self.i18n, 'DECIMAL_SEPARATOR', '.')
                if decimal_sep == ',':
                    clean_val = val.replace(' ', '').replace(',', '.').strip()
                else:
                    clean_val = val.replace(',', '').replace(' ', '').strip()
                f_val = float(clean_val)
                if f_val >= config.high_speed_threshold: 
                    painter.setPen(self._cached_pens['high'])
                elif f_val <= config.low_speed_threshold: 
                    painter.setPen(self._cached_pens['low'])
                else: 
                    painter.setPen(self._cached_pens['default'])
            except:
                painter.setPen(self._cached_pens['default'])
        else:
            painter.setPen(self._cached_pens['default'])

        # 1. Draw Arrow
        if not config.hide_arrows:
            painter.setFont(self.arrow_font)
            arrow = self.i18n.UPLOAD_ARROW if is_upload else self.i18n.DOWNLOAD_ARROW
            painter.drawText(arrow_x, y, arrow)

        # 2. Draw Value (Right-aligned within fixed/expanded number area)
        painter.setFont(self.font)
        val_width = self.metrics.horizontalAdvance(val)
        aligned_number_x = number_x + (number_area_width - val_width)
        painter.drawText(int(aligned_number_x), y, val)
        
        # 3. Draw Unit
        if not config.hide_unit_suffix:
            painter.drawText(unit_x, y, unit)

    def _draw_network_pixel_hud_block(self, painter: QPainter, upload: float, download: float,
                                      up_val: str, down_val: str, width: int, height: int,
                                      config: RenderConfig, x_offset: int,
                                      speed_history: Optional[List[Any]] = None) -> None:
        """Draws a compact pixel HUD network block."""
        painter.save()
        try:
            label_w = constants.renderer.PIXEL_HUD_LABEL_WIDTH
            graph_w = constants.renderer.PIXEL_HUD_NETWORK_GRAPH_WIDTH
            inner_gap = constants.renderer.PIXEL_HUD_INNER_GAP
            block_w = label_w + inner_gap + graph_w
            margin = constants.renderer.TEXT_MARGIN
            block_h = max(
                constants.renderer.PIXEL_HUD_MIN_HEIGHT,
                min(height - 4, self.metrics.height() * 2 + 1),
            )
            top = int((height - block_h) / 2)
            x = x_offset + margin
            label_rect = QRect(x, top, label_w, block_h)
            graph_rect = QRect(
                x + label_w + inner_gap,
                top + 2,
                graph_w,
                max(4, block_h - 4),
            )

            upload_color = QColor(constants.graph.UPLOAD_LINE_COLOR).lighter(130)
            download_color = QColor(constants.graph.DOWNLOAD_LINE_COLOR).lighter(125)
            accent = QColor(constants.graph.UPLOAD_LINE_COLOR).lighter(125)
            self._draw_vertical_block_label(painter, "NET", label_rect, accent, self._pixel_hud_label_color(True), True)

            self._draw_pixel_hud_frame(painter, graph_rect, accent, True)

            top_bytes, bottom_bytes = (download, upload) if config.swap_upload_download else (upload, download)
            top_text, bottom_text = (down_val, up_val) if config.swap_upload_download else (up_val, down_val)
            top_color, bottom_color = (download_color, upload_color) if config.swap_upload_download else (upload_color, download_color)
            self._draw_network_bidirectional_history_bars(
                painter,
                graph_rect.adjusted(2, 2, -2, -2),
                max(0.0, float(top_bytes)),
                max(0.0, float(bottom_bytes)),
                top_color,
                bottom_color,
                speed_history,
                config.swap_upload_download,
                not config.swap_upload_download,
            )
            self._draw_network_value_overlay(painter, graph_rect.adjusted(1, 1, -1, -1), top_text, bottom_text)

            self._last_text_rect = QRect(x_offset, top, block_w + (margin * 2), block_h)
        finally:
            painter.restore()

    def _draw_network_bidirectional_history_bars(self, painter: QPainter, rect: QRect, top_value: float,
                                                 bottom_value: float, top_color: QColor, bottom_color: QColor,
                                                 speed_history: Optional[List[Any]],
                                                 swap_upload_download: bool,
                                                 top_is_upload: bool) -> None:
        """Draws 1-second upload/download history bars split by a center x-axis."""
        if rect.width() <= 2 or rect.height() <= 4:
            return

        baseline_y = rect.top() + rect.height() // 2
        top_area_h = max(1, baseline_y - rect.top() - 1)
        bottom_area_h = max(1, rect.bottom() - baseline_y - 1)
        icon_slot_w = 8
        chart_x = rect.left() + icon_slot_w + 1
        chart_w = max(4, rect.width() - icon_slot_w - 2)
        bar_gap = 1
        bar_w = 2
        max_bars = max(1, (chart_w + bar_gap) // (bar_w + bar_gap))
        samples = self._network_history_second_buckets(speed_history, top_value, bottom_value, swap_upload_download, max_bars)
        scale = max([max(up, down) for up, down in samples] + [top_value, bottom_value, 1.0])

        top_bg = QColor(top_color)
        top_bg.setAlpha(28)
        painter.fillRect(QRect(chart_x, rect.top(), chart_w, top_area_h), top_bg)
        bottom_bg = QColor(bottom_color)
        bottom_bg.setAlpha(28)
        painter.fillRect(QRect(chart_x, baseline_y + 1, chart_w, bottom_area_h), bottom_bg)

        start_x = chart_x + max(0, chart_w - ((len(samples) * bar_w) + (max(0, len(samples) - 1) * bar_gap)))
        for idx, (top_sample, bottom_sample) in enumerate(samples):
            x = start_x + idx * (bar_w + bar_gap)
            if x > rect.right():
                break

            top_h = int(round(top_area_h * min(1.0, max(0.0, top_sample) / scale)))
            if top_sample > 0 and top_h <= 0:
                top_h = 1
            if top_h > 0:
                fill = QColor(top_color)
                fill.setAlpha(235)
                painter.fillRect(QRect(x, baseline_y - top_h, bar_w, top_h), fill)

            bottom_h = int(round(bottom_area_h * min(1.0, max(0.0, bottom_sample) / scale)))
            if bottom_sample > 0 and bottom_h <= 0:
                bottom_h = 1
            if bottom_h > 0:
                fill = QColor(bottom_color)
                fill.setAlpha(235)
                painter.fillRect(QRect(x, baseline_y + 1, bar_w, bottom_h), fill)

        axis = QColor(255, 255, 255)
        axis.setAlpha(115)
        painter.fillRect(QRect(rect.left(), baseline_y, rect.width(), 1), axis)
        self._draw_network_direction_icon(
            painter,
            QRect(rect.left(), rect.top(), icon_slot_w, top_area_h),
            top_color,
            top_is_upload,
        )
        self._draw_network_direction_icon(
            painter,
            QRect(rect.left(), baseline_y + 1, icon_slot_w, bottom_area_h),
            bottom_color,
            not top_is_upload,
        )

    @staticmethod
    def _network_history_second_buckets(speed_history: Optional[List[Any]], top_value: float,
                                        bottom_value: float, swap_upload_download: bool,
                                        max_bars: int) -> List[Tuple[float, float]]:
        """Returns newest visible network samples grouped into one bar per second."""
        fallback = [(max(0.0, top_value), max(0.0, bottom_value))]
        if not speed_history:
            return fallback

        buckets: Dict[int, Tuple[float, float]] = {}
        for item in speed_history:
            try:
                timestamp = getattr(item, "timestamp", None)
                second = int(timestamp.timestamp()) if timestamp is not None else len(buckets)
                upload = max(0.0, float(getattr(item, "upload", 0.0)))
                download = max(0.0, float(getattr(item, "download", 0.0)))
                top_sample, bottom_sample = (download, upload) if swap_upload_download else (upload, download)
                old_top, old_bottom = buckets.get(second, (0.0, 0.0))
                buckets[second] = (max(old_top, top_sample), max(old_bottom, bottom_sample))
            except Exception:
                continue

        if not buckets:
            return fallback

        seconds = sorted(buckets.keys())
        latest = seconds[-1]
        earliest = max(seconds[0], latest - max_bars + 1)
        samples = [buckets.get(second, (0.0, 0.0)) for second in range(earliest, latest + 1)]
        return samples[-max_bars:] or fallback

    def _draw_network_value_overlay(self, painter: QPainter, rect: QRect, top_text: str, bottom_text: str) -> None:
        """Draws floating compact numeric overlays for the two network directions."""
        if rect.width() <= 10 or rect.height() <= 8:
            return

        mid_y = rect.top() + rect.height() // 2
        top_rect = QRect(rect.left(), rect.top(), rect.width(), max(1, mid_y - rect.top()))
        bottom_rect = QRect(rect.left(), mid_y + 1, rect.width(), max(1, rect.bottom() - mid_y))
        self._draw_network_text_badge(painter, top_rect, self._compact_network_value(top_text))
        self._draw_network_text_badge(painter, bottom_rect, self._compact_network_value(bottom_text))

    def _draw_pixel_hud_frame(self, painter: QPainter, rect: QRect, accent: QColor, available: bool) -> None:
        """Draws the cyber-pixel frame shared by compact taskbar HUD blocks."""
        if rect.width() <= 2 or rect.height() <= 2:
            return

        fill = QColor(2, 7, 13)
        fill.setAlpha(230 if available else 166)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(fill)
        painter.drawRect(rect)

        grid = QColor(255, 255, 255)
        grid.setAlpha(18 if available else 8)
        inner = rect.adjusted(2, 2, -2, -2)
        for x in range(inner.left() + 6, inner.right(), 6):
            painter.fillRect(QRect(x, inner.top(), 1, max(1, inner.height())), grid)
        for y in range(inner.top() + 5, inner.bottom(), 5):
            painter.fillRect(QRect(inner.left(), y, max(1, inner.width()), 1), grid)

        scan = QColor(accent)
        scan.setAlpha(48 if available else 22)
        painter.fillRect(QRect(rect.left() + 2, rect.top() + 2, max(1, rect.width() - 4), 1), scan)

        edge = QColor(accent)
        edge.setAlpha(245 if available else 115)
        painter.setPen(QPen(edge, 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(rect.adjusted(0, 0, -1, -1))

        corner = QColor(accent)
        corner.setAlpha(255 if available else 120)
        size = 2
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(corner)
        painter.drawRect(QRect(rect.left(), rect.top(), size, size))
        painter.drawRect(QRect(rect.right() - size + 1, rect.top(), size, size))
        painter.drawRect(QRect(rect.left(), rect.bottom() - size + 1, size, size))
        painter.drawRect(QRect(rect.right() - size + 1, rect.bottom() - size + 1, size, size))

    def _draw_network_direction_icon(self, painter: QPainter, rect: QRect, color: QColor, is_upload: bool) -> None:
        """Draws a tiny direction arrow icon for the network lane."""
        if rect.width() <= 3 or rect.height() <= 3:
            return

        painter.save()
        try:
            icon_color = QColor(color)
            icon_color.setAlpha(255)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(icon_color)

            cx = rect.left() + rect.width() / 2.0
            top = rect.top() + 1.0
            bottom = rect.bottom() - 1.0
            shaft_half = 0.8
            head_half = 2.5
            head_h = max(2.0, min(4.0, rect.height() - 2.0))

            path = QPainterPath()
            if is_upload:
                path.moveTo(cx, top)
                path.lineTo(cx - head_half, top + head_h)
                path.lineTo(cx - shaft_half, top + head_h)
                path.lineTo(cx - shaft_half, bottom)
                path.lineTo(cx + shaft_half, bottom)
                path.lineTo(cx + shaft_half, top + head_h)
                path.lineTo(cx + head_half, top + head_h)
            else:
                path.moveTo(cx, bottom)
                path.lineTo(cx - head_half, bottom - head_h)
                path.lineTo(cx - shaft_half, bottom - head_h)
                path.lineTo(cx - shaft_half, top)
                path.lineTo(cx + shaft_half, top)
                path.lineTo(cx + shaft_half, bottom - head_h)
                path.lineTo(cx + head_half, bottom - head_h)
            path.closeSubpath()
            painter.drawPath(path)
        finally:
            painter.restore()

    def _draw_network_text_badge(self, painter: QPainter, rect: QRect, value_text: str) -> None:
        """Draws a larger floating pixel-number badge in a network graph lane."""
        if rect.width() <= 16 or rect.height() <= 5:
            return

        text = str(value_text).strip() or "--"
        painter.save()
        try:
            text_rect = rect.adjusted(9, 0, -1, 0)
            scale = 2 if rect.height() >= 10 else 1
            digit_w = 3 * scale
            digit_h = 5 * scale
            gap = max(1, scale)
            total_w = (digit_w * len(text)) + (gap * max(0, len(text) - 1))
            if total_w > text_rect.width() - 4:
                scale = 1
                digit_w = 3
                digit_h = 5
                gap = 1
                total_w = (digit_w * len(text)) + (gap * max(0, len(text) - 1))

            pad_x = 2
            pad_y = max(0, min(1, int((rect.height() - digit_h) / 2)))
            badge_w = min(text_rect.width(), total_w + (pad_x * 2))
            badge_h = min(rect.height(), digit_h + (pad_y * 2))
            badge = QRect(
                text_rect.left() + max(0, int((text_rect.width() - badge_w) / 2)),
                rect.top() + max(0, int((rect.height() - badge_h) / 2)),
                badge_w,
                badge_h,
            )

            bg = QColor(4, 8, 12)
            bg.setAlpha(108)
            border = QColor(255, 255, 255)
            border.setAlpha(58)
            painter.setPen(QPen(border, 1))
            painter.setBrush(bg)
            painter.drawRect(badge.adjusted(0, 0, -1, -1))

            fg = QColor(255, 255, 255)
            fg.setAlpha(236)
            shadow = QColor(0, 0, 0)
            shadow.setAlpha(178)
            x = badge.left() + int((badge.width() - total_w) / 2)
            y = badge.top() + int((badge.height() - digit_h) / 2)
            for ch in text:
                self._draw_pixel_glyph(painter, ch, x, y + 1, scale, shadow)
                self._draw_pixel_glyph(painter, ch, x, y, scale, fg)
                x += digit_w + gap
        finally:
            painter.restore()

    @staticmethod
    def _compact_network_value(value_text: str) -> str:
        compact = str(value_text).strip().replace(",", ".")
        if len(compact) <= 5:
            return compact
        if "." in compact:
            whole, fraction = compact.split(".", 1)
            if len(whole) >= 4:
                return whole[:5]
            return f"{whole}.{fraction[:max(0, 4 - len(whole))]}"[:5].rstrip(".")
        return compact[:5]




    def draw_hardware_stats(self, painter: QPainter, cpu_usage: Optional[float], gpu_usage: Optional[float],
                           width: int, height: int, config: RenderConfig,
                           cpu_temp: Optional[float] = None, gpu_temp: Optional[float] = None,
                           ram_info: Optional[Tuple[float, float]] = None,
                           vram_info: Optional[Tuple[float, float]] = None,
                           layout_mode: str = 'vertical', x_offset: int = 0, fixed_width: Optional[int] = None,
                           cpu_power: Optional[float] = None, gpu_power: Optional[float] = None,
                           cpu_history: Optional[List[Any]] = None, gpu_history: Optional[List[Any]] = None) -> None:
        """Draws CPU and/or GPU utilization statistics with optional temperature, power, and memory."""
        try:
            order = getattr(config, 'widget_display_order', ["network", "cpu", "gpu"])
            cpu_idx = order.index("cpu") if "cpu" in order else 999
            gpu_idx = order.index("gpu") if "gpu" in order else 999

            style = getattr(config, 'hardware_label_style', 'icons_colored')
            if style == "pixel_hud_blocks":
                cpu_color = constants.renderer.PIXEL_HUD_CPU_COLOR
                gpu_color = constants.renderer.PIXEL_HUD_GPU_COLOR
            else:
                cpu_color = "#FFFFFF" if style == "icons_monochrome" else constants.renderer.CPU_LINE_COLOR
                gpu_color = "#FFFFFF" if style == "icons_monochrome" else constants.renderer.GPU_LINE_COLOR

            items = []
            if cpu_usage is not None:
                items.append((cpu_idx, ('CPU', cpu_usage, cpu_temp, ram_info, cpu_color, cpu_power)))
            if gpu_usage is not None:
                items.append((gpu_idx, ('GPU', gpu_usage, gpu_temp, vram_info, gpu_color, gpu_power)))

            items.sort(key=lambda x: x[0])
            enabled_stats = [x[1] for x in items]

            if not enabled_stats: return

            if style == "pixel_hud_blocks":
                self._draw_pixel_hud_blocks(
                    painter,
                    enabled_stats,
                    width,
                    height,
                    config,
                    x_offset,
                    {"CPU": cpu_history or [], "GPU": gpu_history or []},
                )
                return

            painter.setFont(self.font)

            line_height = self.metrics.height()
            ascent = self.metrics.ascent()
            total_height = line_height * len(enabled_stats)
            top_y = int((height - total_height) / 2 + ascent)

            margin = constants.renderer.TEXT_MARGIN
            current_x = x_offset + margin

            is_compact = getattr(config, 'widget_display_mode', 'network_only') == "compact_stack" or len(enabled_stats) > 1

            show_temps = getattr(config, "show_hardware_temps", False)
            show_power = getattr(config, "show_hardware_power", False)

            render_rows = []
            for (label, val, temp, mem_info, color_hex, power) in enabled_stats:
                main_text = f"{int(val)}%"

                # Unified parenthetical suffix: "(43°C, 7.8W)", "(43°C)", "(7.8W)", or "(N/A)"
                suffix = self._build_hw_suffix(temp, power, show_temps, show_power)
                if suffix:
                    main_text += f" {suffix}"

                mem_text = ""
                if mem_info and mem_info[0] is not None:
                    used, total = mem_info
                    mem_text = f"{used:.1f}/{total:.1f}G" if total and total > 0 else f"{used:.1f}G"

                if mem_text:
                    if is_compact:
                        main_text += f" | {mem_text}"
                        render_rows.append({'label': label, 'text': main_text, 'color': color_hex, 'draw_icon': True})
                    else:
                        render_rows.append({'label': label, 'text': main_text, 'color': color_hex, 'draw_icon': True})
                        render_rows.append({'label': label, 'text': mem_text, 'color': color_hex, 'draw_icon': False})
                else:
                    render_rows.append({'label': label, 'text': main_text, 'color': color_hex, 'draw_icon': True})
                    
            total_height = line_height * len(render_rows)
            top_y = int((height - total_height) / 2 + ascent)
            current_x = x_offset + margin
            
            style = getattr(config, 'hardware_label_style', 'icons_colored')
            for i, row in enumerate(render_rows):
                y_pos = top_y + (i * line_height)
                if row['draw_icon']:
                    if style == "text":
                        painter.setPen(QPen(QColor(row['color'])))
                        painter.drawText(current_x, y_pos, row['label'])
                        val_x = current_x + self.metrics.horizontalAdvance(row['label']) + 4
                    else:
                        self._draw_icon(painter, row['label'], current_x, y_pos, QColor(row['color']))
                        val_x = current_x + 14
                else:
                    val_x = current_x # Align with icon, no indent!
                
                painter.setPen(self.default_color)
                painter.drawText(val_x, y_pos, row['text'])
                
            # Update bounding rect for accurate spacing in side-by-side or grouped layout views
            max_text_width = max((14 if row['draw_icon'] else 0) + self.metrics.horizontalAdvance(row['text']) for row in render_rows)
            self._last_text_rect = QRect(x_offset, top_y, max_text_width + margin, total_height)

        except Exception as e:
            self.logger.error("Failed to draw hardware stats: %s", e)

    def _draw_pixel_hud_blocks(self, painter: QPainter, enabled_stats: List[Tuple[str, float, Optional[float], Any, str, Optional[float]]],
                               width: int, height: int, config: RenderConfig, x_offset: int,
                               histories: Dict[str, List[Any]]) -> None:
        """Draws compact pixel HUD hardware blocks with vertical labels and tiny charts."""
        blocks = self._build_pixel_hud_blocks(enabled_stats, config, histories)
        if not blocks:
            return

        painter.save()
        try:
            label_w = constants.renderer.PIXEL_HUD_LABEL_WIDTH
            graph_w = constants.renderer.PIXEL_HUD_GRAPH_WIDTH
            inner_gap = constants.renderer.PIXEL_HUD_INNER_GAP
            block_gap = constants.renderer.PIXEL_HUD_GAP
            block_w = label_w + inner_gap + graph_w
            margin = constants.renderer.TEXT_MARGIN
            block_h = max(
                constants.renderer.PIXEL_HUD_MIN_HEIGHT,
                min(height - 4, self.metrics.height() * 2 + 1),
            )
            top = int((height - block_h) / 2)
            current_x = x_offset + margin

            for block in blocks:
                self._draw_pixel_hud_block(
                    painter,
                    block["label"],
                    block["value"],
                    block["temp"],
                    block["history"],
                    current_x,
                    top,
                    block_w,
                    block_h,
                    block["color"],
                    block["kind"],
                    block["available"],
                )
                current_x += block_w + block_gap

            total_w = (block_w * len(blocks)) + (block_gap * max(0, len(blocks) - 1)) + (margin * 2)
            self._last_text_rect = QRect(x_offset, top, total_w, block_h)
        finally:
            painter.restore()

    def _build_pixel_hud_blocks(self, enabled_stats: List[Tuple[str, float, Optional[float], Any, str, Optional[float]]],
                                config: RenderConfig, histories: Dict[str, List[Any]]) -> List[Dict[str, Any]]:
        """Builds semantic compact blocks for utilization and memory-capacity metrics."""
        blocks: List[Dict[str, Any]] = []

        for label, value, temp, mem_info, color_hex, _power in enabled_stats:
            metric = str(label).upper()
            blocks.append({
                "label": metric,
                "value": self._bounded_percent(value),
                "temp": temp,
                "history": histories.get(metric, []),
                "color": QColor(color_hex),
                "kind": "line" if metric == "CPU" else "bars",
                "available": True,
            })

            if metric == "CPU" and getattr(config, "monitor_ram_enabled", False):
                mem_pct = self._memory_usage_percent(mem_info)
                blocks.append({
                    "label": "MEM",
                    "value": mem_pct if mem_pct is not None else 0.0,
                    "temp": None,
                    "history": [],
                    "color": QColor(constants.renderer.PIXEL_HUD_RAM_COLOR),
                    "kind": "capacity",
                    "available": mem_pct is not None,
                })
            elif metric == "GPU" and getattr(config, "monitor_vram_enabled", False):
                vram_pct = self._memory_usage_percent(mem_info)
                blocks.append({
                    "label": "VRM",
                    "value": vram_pct if vram_pct is not None else 0.0,
                    "temp": None,
                    "history": [],
                    "color": QColor(constants.renderer.PIXEL_HUD_VRAM_COLOR),
                    "kind": "grid",
                    "available": vram_pct is not None,
                })

        return blocks

    def _draw_pixel_hud_block(self, painter: QPainter, label: str, value: float, temp: Optional[float], history: List[Any],
                              x: int, top: int, width: int, height: int, color: QColor,
                              kind: str, available: bool) -> None:
        """Draws one compact hardware block using the metric-specific mini visualization."""
        label_w = constants.renderer.PIXEL_HUD_LABEL_WIDTH
        inner_gap = constants.renderer.PIXEL_HUD_INNER_GAP
        graph_rect = QRect(
            x + label_w + inner_gap,
            top + 2,
            max(4, width - label_w - inner_gap),
            max(4, height - 4),
        )
        label_rect = QRect(x, top, label_w, height)

        accent = self._pixel_hud_accent(color, available)
        self._draw_vertical_block_label(painter, label, label_rect, accent, self._pixel_hud_label_color(available), available)

        self._draw_pixel_hud_frame(painter, graph_rect, accent, available)

        pct = self._bounded_percent(value)
        inner = graph_rect.adjusted(2, 2, -2, -2)
        if kind == "capacity":
            self._draw_block_capacity(painter, inner, pct, accent, available)
        elif kind == "grid":
            self._draw_block_grid(painter, inner, pct, accent, available)
        elif kind == "bars":
            self._draw_block_bars(painter, inner, history, pct, accent, available)
        else:
            self._draw_block_sparkline(painter, inner, history, pct, accent, available)

        self._draw_block_value_text(painter, graph_rect.adjusted(1, 1, -1, -1), pct, available)

        try:
            temp_ok = temp is not None and math.isfinite(float(temp))
        except Exception:
            temp_ok = False
        if temp_ok:
            marker_color = QColor(accent)
            marker_color.setAlpha(210)
            painter.fillRect(QRect(graph_rect.right() - 2, graph_rect.top() + 2, 1, max(2, graph_rect.height() - 4)), marker_color)

    def _draw_vertical_block_label(self, painter: QPainter, label: str, rect: QRect, accent: QColor,
                                   text_color: QColor, available: bool) -> None:
        """Draws CPU/GPU letters stacked vertically inside the compact block label strip."""
        painter.save()
        try:
            accent_line = QColor(accent)
            accent_line.setAlpha(210 if available else 80)
            painter.fillRect(QRect(rect.right() - 1, rect.top() + 2, 1, max(1, rect.height() - 4)), accent_line)

            label_font = QFont(self.font.family(), max(4, min(6, rect.height() // 4)), constants.fonts.WEIGHT_BOLD)
            painter.setFont(label_font)
            pen_color = QColor(text_color)
            pen_color.setAlpha(255 if available else 130)
            letters = list(label[:3].upper())
            if not letters:
                return
            step = rect.height() / len(letters)
            shadow = self._opposite_text_color(pen_color)
            shadow.setAlpha(90 if available else 45)
            for idx, ch in enumerate(letters):
                char_rect = QRect(rect.left(), int(rect.top() + idx * step), rect.width(), max(1, int(math.ceil(step))))
                painter.setPen(QPen(shadow, 1))
                painter.drawText(char_rect.translated(1, 0), Qt.AlignmentFlag.AlignCenter, ch)
                painter.setPen(QPen(pen_color, 1))
                painter.drawText(char_rect, Qt.AlignmentFlag.AlignCenter, ch)
        finally:
            painter.restore()

    def _draw_block_sparkline(self, painter: QPainter, rect: QRect, history: List[Any], current_value: float,
                              color: QColor, available: bool) -> None:
        """Draws a tiny utilization sparkline scaled to 0-100%."""
        if rect.width() <= 1 or rect.height() <= 1:
            return

        values = []
        for item in history[-max(2, rect.width()):]:
            try:
                values.append(self._bounded_percent(getattr(item, "value", item)))
            except Exception:
                continue
        if len(values) < 2:
            values = [current_value, current_value]

        path = QPainterPath()
        denom = max(1, len(values) - 1)
        for idx, val in enumerate(values):
            x = rect.left() + (idx / denom) * rect.width()
            y = rect.bottom() - (val / 100.0) * rect.height()
            point = QPointF(x, y)
            if idx == 0:
                path.moveTo(point)
            else:
                path.lineTo(point)

        fill_h = max(1, int(rect.height() * self._bounded_percent(values[-1]) / 100.0))
        fill = QColor(color)
        fill.setAlpha(58 if available else 24)
        painter.fillRect(QRect(rect.left(), rect.bottom() - fill_h, rect.width(), fill_h), fill)

        glow = QColor(color)
        glow.setAlpha(150 if available else 48)
        painter.setPen(QPen(glow, 2))
        painter.drawPath(path)

        line = QColor(color)
        line.setAlpha(255 if available else 96)
        painter.setPen(QPen(line, 1))
        painter.drawPath(path)

        dot = QColor(color)
        dot.setAlpha(255 if available else 96)
        last_x = rect.right()
        last_y = int(rect.bottom() - (values[-1] / 100.0) * rect.height())
        painter.fillRect(QRect(last_x - 1, last_y - 1, 2, 2), dot)

    def _draw_block_bars(self, painter: QPainter, rect: QRect, history: List[Any], current_value: float,
                         color: QColor, available: bool) -> None:
        """Draws compact vertical load bars for GPU-style bursty utilization."""
        if rect.width() <= 1 or rect.height() <= 1:
            return

        max_bars = max(3, rect.width() // 3)
        values: List[float] = []
        for item in history[-max_bars:]:
            try:
                values.append(self._bounded_percent(getattr(item, "value", item)))
            except Exception:
                continue
        if len(values) < 2:
            values = [current_value] * max_bars

        bar_gap = 1
        bar_w = max(1, (rect.width() - (bar_gap * (len(values) - 1))) // len(values))
        base_color = QColor(color)
        base_color.setAlpha(42 if available else 18)
        painter.fillRect(rect, base_color)

        for idx, val in enumerate(values):
            x = rect.left() + idx * (bar_w + bar_gap)
            bar_h = max(1, int(rect.height() * val / 100.0))
            bar_color = QColor(color)
            bar_color.setAlpha(255 if available else 92)
            painter.fillRect(QRect(x, rect.bottom() - bar_h + 1, bar_w, bar_h), bar_color)

    def _draw_block_capacity(self, painter: QPainter, rect: QRect, current_value: float,
                             color: QColor, available: bool) -> None:
        """Draws a horizontal capacity gauge for RAM usage."""
        if rect.width() <= 1 or rect.height() <= 1:
            return

        gauge = rect.adjusted(0, max(1, rect.height() // 4), 0, -max(1, rect.height() // 4))
        track = QColor(color)
        track.setAlpha(46 if available else 18)
        painter.fillRect(gauge, track)

        fill_w = max(1, int(gauge.width() * self._bounded_percent(current_value) / 100.0)) if available else 0
        if fill_w:
            fill = QColor(color)
            fill.setAlpha(255)
            painter.fillRect(QRect(gauge.left(), gauge.top(), fill_w, gauge.height()), fill)

        tick = QColor(255, 255, 255)
        tick.setAlpha(95 if available else 36)
        for i in range(1, 4):
            x = gauge.left() + int(gauge.width() * i / 4)
            painter.fillRect(QRect(x, gauge.top(), 1, gauge.height()), tick)

    def _draw_block_grid(self, painter: QPainter, rect: QRect, current_value: float,
                         color: QColor, available: bool) -> None:
        """Draws a tiled capacity grid for VRAM usage."""
        if rect.width() <= 1 or rect.height() <= 1:
            return

        cols, rows, gap = 5, 3, 1
        cell_w = max(1, (rect.width() - gap * (cols - 1)) // cols)
        cell_h = max(1, (rect.height() - gap * (rows - 1)) // rows)
        total = cols * rows
        active = int(math.ceil(total * self._bounded_percent(current_value) / 100.0)) if available else 0

        for idx in range(total):
            col = idx % cols
            row = rows - 1 - (idx // cols)
            cell = QRect(
                rect.left() + col * (cell_w + gap),
                rect.top() + row * (cell_h + gap),
                cell_w,
                cell_h,
            )
            cell_color = QColor(color)
            cell_color.setAlpha(255 if idx < active else (42 if available else 16))
            painter.fillRect(cell, cell_color)

    def _draw_block_value_text(self, painter: QPainter, rect: QRect, current_value: float, available: bool) -> None:
        """Draws the compact numeric value inside a pixel HUD graph block."""
        value_text = "--" if not available else f"{int(round(self._bounded_percent(current_value)))}"
        self._draw_pixel_value_badge(painter, rect, value_text, available)

    def _draw_pixel_value_badge(self, painter: QPainter, rect: QRect, value_text: str, available: bool) -> None:
        """Draws a small floating pixel-number badge without affecting layout."""
        if rect.width() <= 8 or rect.height() <= 4:
            return

        text = str(value_text).strip() or "--"
        painter.save()
        try:
            scale = max(1, min(2, rect.height() // 7))
            digit_w = 3 * scale
            digit_h = 5 * scale
            gap = max(1, scale)
            total_w = (digit_w * len(text)) + (gap * max(0, len(text) - 1))
            if total_w > rect.width() - 4:
                scale = 1
                digit_w = 3
                digit_h = 5
                gap = 1
                total_w = (digit_w * len(text)) + (gap * max(0, len(text) - 1))

            pad_x = 2
            pad_y = 1 if rect.height() <= 8 else 2

            backing = QRect(
                rect.left() + int((rect.width() - total_w - (pad_x * 2)) / 2),
                rect.top() + int((rect.height() - digit_h - (pad_y * 2)) / 2),
                total_w + (pad_x * 2),
                digit_h + (pad_y * 2),
            )

            bg = QColor(4, 8, 12)
            bg.setAlpha(108 if available else 70)
            border = QColor(255, 255, 255)
            border.setAlpha(58 if available else 32)
            painter.setPen(QPen(border, 1))
            painter.setBrush(bg)
            painter.drawRect(backing.adjusted(0, 0, -1, -1))

            fg = QColor(255, 255, 255)
            fg.setAlpha(236 if available else 150)
            shadow = QColor(0, 0, 0)
            shadow.setAlpha(178 if available else 105)
            x = backing.left() + pad_x
            y = backing.top() + pad_y
            for ch in text:
                self._draw_pixel_glyph(painter, ch, x, y + 1, scale, shadow)
                self._draw_pixel_glyph(painter, ch, x, y, scale, fg)
                x += digit_w + gap
        finally:
            painter.restore()

    @staticmethod
    def _draw_pixel_glyph(painter: QPainter, ch: str, x: int, y: int, scale: int, color: QColor) -> None:
        glyphs = {
            "0": ("111", "101", "101", "101", "111"),
            "1": ("010", "110", "010", "010", "111"),
            "2": ("111", "001", "111", "100", "111"),
            "3": ("111", "001", "111", "001", "111"),
            "4": ("101", "101", "111", "001", "001"),
            "5": ("111", "100", "111", "001", "111"),
            "6": ("111", "100", "111", "101", "111"),
            "7": ("111", "001", "010", "010", "010"),
            "8": ("111", "101", "111", "101", "111"),
            "9": ("111", "101", "111", "001", "111"),
            ".": ("000", "000", "000", "000", "010"),
            ",": ("000", "000", "000", "010", "100"),
            "-": ("000", "000", "111", "000", "000"),
        }
        rows = glyphs.get(ch, glyphs["-"])
        for row_idx, row in enumerate(rows):
            for col_idx, pixel in enumerate(row):
                if pixel == "1":
                    painter.fillRect(QRect(x + col_idx * scale, y + row_idx * scale, scale, scale), color)

    @staticmethod
    def _memory_usage_percent(mem_info: Optional[Tuple[float, float]]) -> Optional[float]:
        if not mem_info or mem_info[0] is None:
            return None
        try:
            used, total = mem_info
            used_value = float(used)
            total_value = float(total)
            if not math.isfinite(used_value) or not math.isfinite(total_value) or total_value <= 0:
                return None
            return max(0.0, min(100.0, (used_value / total_value) * 100.0))
        except Exception:
            return None

    @staticmethod
    def _pixel_hud_accent(color: QColor, available: bool) -> QColor:
        accent = QColor(color)
        if not available:
            accent = QColor(130, 142, 154)
        return accent.lighter(125)

    def _pixel_hud_label_color(self, available: bool) -> QColor:
        label_color = QColor(self.default_color)
        if not label_color.isValid():
            label_color = QColor(255, 255, 255)
        if not available:
            label_color.setAlpha(150)
        return label_color

    @staticmethod
    def _opposite_text_color(color: QColor) -> QColor:
        luminance = (0.2126 * color.redF()) + (0.7152 * color.greenF()) + (0.0722 * color.blueF())
        return QColor(255, 255, 255) if luminance < 0.5 else QColor(0, 0, 0)

    @staticmethod
    def _bounded_percent(value: Any) -> float:
        try:
            numeric = float(value)
        except Exception:
            return 0.0
        if not math.isfinite(numeric):
            return 0.0
        return max(0.0, min(100.0, numeric))


    def _draw_icon(self, painter: QPainter, icon_type: str, x: int, y_ascent: int, color: Optional[QColor] = None) -> None:
        """Draws a tiny symbolic icon for CPU or GPU."""
        painter.save()

        # Icon box size
        size = 11
        rect = QRect(x, y_ascent - size + 1, size, size)
        
        draw_color = color if color else self.default_color
        pen = QPen(draw_color, 1)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        
        if icon_type == 'CPU':
            # Microchip with visible legs extending from PCB edge
            for dx in [3, 5, 7]:
                painter.drawLine(rect.left() + dx, rect.top(), rect.left() + dx, rect.top() + 1)
                painter.drawLine(rect.left() + dx, rect.bottom() - 1, rect.left() + dx, rect.bottom())
            for dy in [3, 5, 7]:
                painter.drawLine(rect.left(), rect.top() + dy, rect.left() + 1, rect.top() + dy)
                painter.drawLine(rect.right() - 1, rect.top() + dy, rect.right(), rect.top() + dy)

            # Draw PCB package (Outer outline)
            painter.drawRect(rect)
                
            # Draw Integrated Heatspreader (Inner filled package)
            ihs_rect = rect.adjusted(2, 2, -2, -2)
            painter.setBrush(painter.pen().color())
            painter.drawRect(ihs_rect) # Solid-filled IHS
            painter.setBrush(Qt.BrushStyle.NoBrush)
            
            # Silicon Die Core (Inner dark center)
            core_rect = rect.adjusted(4, 4, -4, -4) 
            painter.fillRect(core_rect, QColor("#121212")) 

        elif icon_type == 'GPU':
            # Graphics card with 'G' and Fan
            card_rect = rect.adjusted(0, 3, 0, -1)
            painter.drawRect(card_rect)
            # Bracket on left
            painter.drawLine(rect.left(), rect.top(), rect.left(), rect.bottom())
            # Fan circle
            fan_size = 5
            fan_rect = QRect(rect.center().x() - 1, rect.center().y() + 1, fan_size, fan_size)
            painter.drawEllipse(fan_rect.adjusted(-2, -1, -2, -1))
            
            # Tiny 'G'
            small_font = QFont(self.font.family(), 6)
            painter.setFont(small_font)
            painter.drawText(card_rect, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, "G")
            
        painter.restore()


    def _build_hw_suffix(self, temp: Optional[float], power: Optional[float],
                        show_temps: bool, show_power: bool) -> str:
        """Builds a parenthetical suffix string from available hardware extras.

        Examples: "(43°C, 7.8W)", "(43°C)", "(7.8W)", "(N/A)", or "" if nothing enabled.
        """
        if not show_temps and not show_power:
            return ""

        parts = []
        has_any_data = False

        if show_temps:
            try:
                temp_ok = temp is not None and math.isfinite(float(temp))
            except Exception:
                temp_ok = False
            if temp_ok:
                parts.append(f"{int(float(temp))}°C")
                has_any_data = True
            else:
                parts.append(None)  # placeholder — will be replaced with N/A if nothing else has data

        if show_power:
            try:
                power_ok = power is not None and math.isfinite(float(power))
            except Exception:
                power_ok = False
            if power_ok:
                parts.append(f"{float(power):.1f}W")
                has_any_data = True
            else:
                parts.append(None)

        if not has_any_data:
            return f"({self.i18n.DEFAULT_TEXT})"

        # Filter out None placeholders (partial data is fine — just show what we have)
        valid_parts = [p for p in parts if p is not None]
        return f"({', '.join(valid_parts)})"

    def draw_mini_graph(self, painter: QPainter, width: int, height: int, config: RenderConfig,
                        history: List[Any], layout_mode: str = 'vertical', 
                        is_hardware: bool = False, hardware_color: str = "#FFFFFF") -> None:
        """Draws a mini graph of history (speed or hardware utilization)."""
        if not config.graph_enabled or len(history) < constants.renderer.MIN_GRAPH_POINTS:
            return

        try:
            side_margin = constants.renderer.GRAPH_LEFT_PADDING
            top_margin = constants.renderer.GRAPH_MARGIN
            bottom_margin = constants.renderer.GRAPH_BOTTOM_PADDING
            
            graph_rect = QRect(side_margin, top_margin, width - (side_margin * 2), height - (top_margin + bottom_margin))
            if graph_rect.width() <= 0 or graph_rect.height() <= 0: return

            # Hash check for caching
            current_hash = hash((tuple(history), is_hardware))

            if self._last_widget_size != (width, height) or self._last_history_hash != current_hash:
                num_points = len(history)
                
                if is_hardware:
                    from netspeedtray.core.widget_state import HardwareStatSnapshot
                    # Hardware is 0-100%
                    max_y = 100.0
                else:
                    # Speed history
                    max_speed_val = max(
                        max(d.upload for d in history),
                        max(d.download for d in history)
                    ) if history else 0
                    
                    if len(history) > 10:
                        all_speeds = [d.upload for d in history] + [d.download for d in history]
                        all_speeds_sorted = sorted(all_speeds)
                        percentile_95 = all_speeds_sorted[int(len(all_speeds_sorted) * 0.95)]
                        if percentile_95 > 0 and max_speed_val > percentile_95 * 3.0:
                            max_speed_val = percentile_95

                    padded_max_speed = max_speed_val * constants.renderer.GRAPH_Y_AXIS_PADDING_FACTOR
                    max_y = max(padded_max_speed, constants.renderer.MIN_Y_SCALE)
                
                num_points = len(history)
                step_x = graph_rect.width() / (num_points - 1) if num_points > 1 else graph_rect.width()
                right_edge = float(graph_rect.right())
                base_y = float(graph_rect.bottom())
                h = float(graph_rect.height())

                raw_x = [right_edge - (num_points - 1 - i) * step_x for i in range(num_points)]
                
                def make_smooth_polyline(accessor):
                    raw_y = [accessor(d) for d in history]
                    cx, cy = calculate_monotone_cubic_interpolation(raw_x, raw_y, density=5)
                    points = [QPointF(x, base_y - (max(0, y) / max_y) * h) for x, y in zip(cx, cy)]
                    return points

                if is_hardware:
                    self._cached_upload_points = make_smooth_polyline(lambda d: d.value)
                    self._cached_download_points = []
                else:
                    self._cached_upload_points = make_smooth_polyline(lambda d: d.upload)
                    self._cached_download_points = make_smooth_polyline(lambda d: d.download)

                self._last_widget_size = (width, height)
                self._last_history_hash = current_hash

            painter.save()
            painter.setOpacity(config.graph_opacity)
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)

            from PyQt6.QtGui import QLinearGradient, QBrush, QPolygonF

            def draw_area(points, color_hex):
                if not points: return
                poly_points = [QPointF(points[0].x(), float(graph_rect.bottom()))]
                poly_points.extend(points)
                poly_points.append(QPointF(points[-1].x(), float(graph_rect.bottom())))
                
                grad = QLinearGradient(0, graph_rect.top(), 0, graph_rect.bottom())
                c = QColor(color_hex)
                c.setAlpha(120)
                grad.setColorAt(0.0, c)
                c.setAlpha(0)
                grad.setColorAt(1.0, c)
                
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QBrush(grad))
                painter.drawPolygon(QPolygonF(poly_points))

            if is_hardware:
                draw_area(self._cached_upload_points, hardware_color)
            else:
                draw_area(self._cached_upload_points, constants.graph.UPLOAD_LINE_COLOR)
                draw_area(self._cached_download_points, constants.graph.DOWNLOAD_LINE_COLOR)
            
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
            stroke_width = 1.5 

            if is_hardware:
                hw_pen = QPen(QColor(hardware_color), stroke_width)
                hw_pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
                painter.setPen(hw_pen)
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawPolyline(self._cached_upload_points)
            else:
                upload_pen = QPen(QColor(constants.graph.UPLOAD_LINE_COLOR), stroke_width)
                upload_pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
                painter.setPen(upload_pen)
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawPolyline(self._cached_upload_points)

                download_pen = QPen(QColor(constants.graph.DOWNLOAD_LINE_COLOR), stroke_width)
                download_pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
                painter.setPen(download_pen)
                painter.drawPolyline(self._cached_download_points)

            painter.restore()
        except Exception as e:
            self.logger.error("Failed to draw mini graph: %s", e)


    def update_config(self, config_dict: Dict[str, Any]) -> None:
        """Updates rendering configuration."""
        try:
            self.config = RenderConfig.from_dict(config_dict)
            self.default_color = QColor(self.config.default_color)
            self.high_color = QColor(self.config.high_speed_color)
            self.low_color = QColor(self.config.low_speed_color)
            self.font = QFont(self.config.font_family, self.config.font_size, self.config.font_weight)
            self.metrics = QFontMetrics(self.font)
            
            # Update Arrow Font
            if self.config.use_separate_arrow_font:
                self.arrow_font = QFont(self.config.arrow_font_family, self.config.arrow_font_size, int(self.config.arrow_font_weight))
            else:
                self.arrow_font = self.font
            self.arrow_metrics = QFontMetrics(self.arrow_font)

            self._cached_upload_points = []
            self._cached_download_points = []
            self._last_history_hash = 0
            self.logger.debug("Renderer config updated.")
        except Exception as e:
            self.logger.error("Failed to update config: %s", e)


    def get_last_text_rect(self) -> QRect:
        """Returns last text bounding rect."""
        return self._last_text_rect


    def pause(self) -> None:
        """Pauses graph updates."""
        self.paused = True
        self.logger.debug("Renderer paused.")


    def resume(self) -> None:
        """Resumes graph updates."""
        self.paused = False
        self.logger.debug("Renderer resumed.")
