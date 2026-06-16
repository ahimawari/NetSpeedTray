import pytest
from unittest.mock import MagicMock
from datetime import datetime, timedelta
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

if __name__ == "__main__":
    test_peak_label_placement_logic()
