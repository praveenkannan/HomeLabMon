import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class SelectedPanelAssetTests(unittest.TestCase):
    def test_status_js_includes_readable_selected_panel_hooks(self):
        js = (ROOT / 'www' / 'status.js').read_text(encoding='utf-8')
        self.assertIn('selected-timeline-list', js)
        self.assertIn('probe-metric-grid', js)
        self.assertIn('selected-meta-grid', js)
        self.assertIn('selected-panel-flash', js)
        self.assertIn('.slice(0, 6)', js)
        self.assertIn('/api/v1/devices/', js)
        self.assertIn('Last 6 Hours', js)

    def test_status_css_includes_selected_panel_layout_hooks(self):
        css = (ROOT / 'www' / 'status.css').read_text(encoding='utf-8')
        self.assertIn('.selected-panel-scroll', css)
        self.assertIn('.selected-timeline-list', css)
        self.assertIn('grid-template-columns: repeat(2, minmax(0, 1fr));', css)
        self.assertIn('.probe-metric-grid', css)
        self.assertIn('.selected-meta-grid', css)
        self.assertIn('@keyframes selectedPanelFlash', css)
        self.assertIn('.selected-device-panel.selected-panel-flash', css)
        self.assertIn('@keyframes selectedPanelLivePulse', css)
        self.assertIn('@media (max-width: 1220px)', css)
        self.assertIn('grid-template-columns: 1fr;', css)
        self.assertIn('align-self: stretch;', css)
        self.assertIn('height: 100%;', css)
        self.assertIn('overflow: auto;', css)


if __name__ == '__main__':
    unittest.main()
