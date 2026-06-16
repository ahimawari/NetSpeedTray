import pytest
from unittest.mock import MagicMock
from datetime import datetime, timedelta
from types import SimpleNamespace
from netspeedtray.core.widget_state import AggregatedSpeedData
from netspeedtray.utils.widget_renderer import WidgetRenderer
from netspeedtray.views.graph.renderer import GraphRenderer

def test_peak_label_placement_logic():
    # Mock dependencies for GraphRenderer
    parent_widget = MagicMock()
    i18n = MagicMock()
    
    # We need to mock _init_matplotlib to avoid actual window/canvas creation
    with MagicMock() as mock_init:
        GraphRenderer._init_matplotlib = mock_init
        renderer = GraphRenderer(parent_widget, i18n)
    
    ax = MagicMock()
    
    # CASE 1: Mid-graph peak (No flipping)
    # x: [0, 100], y: [0, 100], peak at (50, 50)
    ax.get_xlim.return_value = (0, 100)
    ax.get_ylim.return_value = (0, 100)
    
    offset, ha, va = renderer._get_peak_label_placement(ax, 50, 50)
    assert ha == 'left'
    assert va == 'bottom'
    assert offset == (8, 8)
    
    # CASE 2: Right-edge peak (Flip horizontal)
    # x: [0, 100], y: [0, 100], peak at (85, 50) -> x_norm = 0.85 >= 0.8
    offset, ha, va = renderer._get_peak_label_placement(ax, 85, 50)
    assert ha == 'right'
    assert va == 'bottom'
    assert offset == (-8, 8)
    
    # CASE 3: Top-edge peak (Flip vertical)
    # x: [0, 100], y: [0, 100], peak at (50, 95) -> y_norm = 0.95 >= 0.9
    offset, ha, va = renderer._get_peak_label_placement(ax, 50, 95)
    assert ha == 'left'
    assert va == 'top'
    assert offset == (8, -8)
    
    # CASE 4: Top-right corner (Flip both)
    # peak at (90, 92)
    offset, ha, va = renderer._get_peak_label_placement(ax, 90, 92)
    assert ha == 'right'
    assert va == 'top'
    assert offset == (-8, -8)

    print("Peak label placement tests passed!")


def test_network_pixel_hud_history_buckets_one_second_columns():
    base = datetime(2026, 1, 1, 12, 0, 0)
    history = [
        AggregatedSpeedData(upload=10.0, download=20.0, timestamp=base),
        AggregatedSpeedData(upload=30.0, download=5.0, timestamp=base + timedelta(milliseconds=500)),
        AggregatedSpeedData(upload=0.0, download=40.0, timestamp=base + timedelta(seconds=2)),
    ]

    samples = WidgetRenderer._network_history_second_buckets(history, 1.0, 1.0, False, 4)

    assert samples == [
        (30.0, 20.0),
        (0.0, 0.0),
        (0.0, 40.0),
    ]


def test_network_pixel_hud_history_buckets_respect_swap_order():
    base = datetime(2026, 1, 1, 12, 0, 0)
    history = [
        AggregatedSpeedData(upload=10.0, download=20.0, timestamp=base),
    ]

    samples = WidgetRenderer._network_history_second_buckets(history, 1.0, 1.0, True, 4)

    assert samples == [(20.0, 10.0)]


def test_pixel_hud_blocks_merge_cpu_gpu_temperature_module():
    renderer = WidgetRenderer.__new__(WidgetRenderer)
    config = SimpleNamespace(
        show_hardware_temps=True,
        monitor_ram_enabled=True,
        monitor_vram_enabled=True,
    )
    enabled_stats = [
        ("CPU", 23.0, 71.2, (18.5, 31.5), "#18E8FF", None),
        ("GPU", 45.0, None, (2.0, 8.0), "#FF4FB3", None),
    ]

    blocks = renderer._build_pixel_hud_blocks(enabled_stats, config, {})
    labels = [block["label"] for block in blocks]

    assert labels == ["CPU", "MEM", "GPU", "VRM", "TMP"]
    assert blocks[4]["kind"] == "temps"
    assert blocks[4]["available"] is True
    assert blocks[4]["value"] == (71.2, None)
    assert WidgetRenderer._temperature_pair_text(71.2, None) == "71/--"


def test_pixel_hud_blocks_hide_temperature_modules_when_disabled():
    renderer = WidgetRenderer.__new__(WidgetRenderer)
    config = SimpleNamespace(
        show_hardware_temps=False,
        monitor_ram_enabled=False,
        monitor_vram_enabled=False,
    )
    enabled_stats = [
        ("CPU", 23.0, 71.2, None, "#18E8FF", None),
        ("GPU", 45.0, 55.0, None, "#FF4FB3", None),
    ]

    blocks = renderer._build_pixel_hud_blocks(enabled_stats, config, {})

    assert [block["label"] for block in blocks] == ["CPU", "GPU"]

if __name__ == "__main__":
    test_peak_label_placement_logic()
