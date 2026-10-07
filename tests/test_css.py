from toty_webui.app import CUSTOM_CSS


def test_mobile_breakpoint_present():
    assert "@media (max-width: 640px)" in CUSTOM_CSS
    assert "min-height: 44px" in CUSTOM_CSS
    assert "overflow-x: hidden" in CUSTOM_CSS
