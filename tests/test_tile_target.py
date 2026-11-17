import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import imagepastor as app


class Control:
    def __init__(self, kind, name, parent=None, url="", bounds=(0, 0, 400, 600)):
        self.element_info = SimpleNamespace(control_type=kind, name=name, runtime_id=(42, id(self)))
        self._parent = parent
        self._url = url
        self._bounds = bounds

    def parent(self):
        return self._parent

    def get_value(self):
        return self._url

    def rectangle(self):
        left, top, right, bottom = self._bounds
        return SimpleNamespace(left=left, top=top, right=right, bottom=bottom)

    def is_visible(self):
        return True

    def is_enabled(self):
        return True

    def is_selected(self):
        return True

    def has_keyboard_focus(self):
        return True


class TileTargetTests(unittest.TestCase):
    def setUp(self):
        self.old = app.paste_target_hwnd, app.paste_target_composer_title, app.paste_target_composer_url, app.paste_target_document_id
        app.paste_target_document_id = None
        self.doc = Control("Document", "Training Help", url="https://chatgpt.com/c/one")
        self.other_doc = Control("Document", "Other conversation", url="https://chatgpt.com/c/two",
                                 bounds=(400, 0, 800, 600))
        self.editor = Control("Edit", "Ask ChatGPT", self.doc)
        self.other = Control("Edit", "Ask ChatGPT", self.other_doc)
        self.editors = [self.editor, self.other]
        self.window = SimpleNamespace(descendants=lambda control_type:
                                      self.editors if control_type == "Edit" else [Control("TabItem", "Training Help")])

    def tearDown(self):
        app.paste_target_hwnd, app.paste_target_composer_title, app.paste_target_composer_url, app.paste_target_document_id = self.old

    def test_chat_title_and_editor_label_are_distinct(self):
        self.assertIs(app.find_chatgpt_composer(self.window, "Training Help"), self.editor)
        self.editor.element_info.name = "Chat with ChatGPT"
        self.assertIs(app.find_chatgpt_composer(self.window, "Training Help"), self.editor)

    def test_mouse_selects_tile_even_when_another_tab_is_selected(self):
        self.assertIs(app.find_chatgpt_composer(self.window, point=app.POINT(500, 200)), self.other)

    def test_missing_saved_title_does_not_fall_back_to_active_chat(self):
        self.assertIsNone(app.find_chatgpt_composer(self.window, "Missing chat"))

    def test_duplicate_titles_require_exact_url_and_unique_match(self):
        self.other_doc.element_info.name = "Training Help"
        self.assertIsNone(app.find_chatgpt_composer(self.window, "Training Help"))
        self.assertIs(app.find_chatgpt_composer(self.window, "Training Help", "https://chatgpt.com/c/two"), self.other)
        self.other_doc._url = self.doc._url
        self.assertIsNone(app.find_chatgpt_composer(self.window, "Training Help", self.doc._url))
        self.assertIs(app.find_chatgpt_composer(self.window, "Training Help", self.doc._url,
                                               document_id=app.document_runtime_id(self.other_doc)), self.other)

    def test_wrong_host_or_unverified_document_is_not_a_target(self):
        for url in ("https://chatgpt.com.example.org/c/one", "https://example.org", ""):
            self.doc._url = url
            self.assertIsNone(app.find_chatgpt_composer(self.window, "Training Help"))

    def test_empty_host_allowlist_does_not_fall_back_to_defaults(self):
        self.assertIsNone(app.find_chatgpt_composer(self.window, "Training Help", allowed_hosts=[]))

    def test_document_value_pattern_is_supported(self):
        self.doc.iface_value = SimpleNamespace(CurrentValue=self.doc._url)
        with patch.object(self.doc, "get_value", side_effect=AssertionError("Use ValuePattern")):
            self.assertIs(app.find_chatgpt_composer(self.window, "Training Help"), self.editor)

    def test_document_legacy_url_is_supported_without_reading_editor_text(self):
        self.doc.legacy_properties = lambda: {"Value": self.doc._url}
        with patch.object(self.doc, "get_value", side_effect=AttributeError("No ValuePattern")):
            self.assertIs(app.find_chatgpt_composer(self.window, "Training Help"), self.editor)

    def test_mouse_outside_chat_tiles_does_not_choose_another_tile(self):
        self.assertIsNone(app.find_chatgpt_composer(self.window, point=app.POINT(900, 100)))

    def test_target_hotkey_accepts_hovered_chat_when_foreground_is_elsewhere(self):
        with patch.object(app, "get_cursor_pos", return_value=app.POINT(500, 200)), \
                patch.object(app.user32, "WindowFromPoint", return_value=100), \
                patch.object(app.user32, "GetAncestor", side_effect=lambda hwnd, flag: hwnd), \
                patch.object(app.user32, "GetForegroundWindow", return_value=200), \
                patch.object(app, "is_valid_chromium_window", return_value=True), \
                patch.object(app, "Desktop", None), \
                patch.object(app, "chromium_window_wrapper", return_value=self.window), \
                patch.object(app, "ensure_chatgpt_tab", side_effect=AssertionError("Do not gate on another address bar")):
            app.set_paste_target({"chatgpt_allowed_hosts": ["chatgpt.com"]})
        self.assertEqual((app.paste_target_hwnd, app.paste_target_composer_title, app.paste_target_composer_url),
                         (100, "Other conversation", "https://chatgpt.com/c/two"))

    def test_native_hit_test_disambiguates_overlapping_document_rectangles(self):
        self.other_doc._bounds = self.doc._bounds
        diagnostics = {}
        desktop = SimpleNamespace(from_point=lambda x, y: self.other)
        with patch.object(app, "Desktop", return_value=desktop):
            self.assertIs(app.composer_under_pointer(self.window, app.POINT(200, 200),
                                                     ["chatgpt.com"], diagnostics), self.other)
        self.assertTrue(diagnostics["pointer_document_verified"])

    def test_unavailable_saved_tile_does_not_redirect_to_another_window(self):
        app.paste_target_hwnd = 123
        with patch.object(app, "paste_target_is_available", return_value=False), \
                patch.object(app, "find_chatgpt_window") as fallback:
            self.assertIsNone(app.resolve_chatgpt_window({"chatgpt_allowed_hosts": ["chatgpt.com"]}))
            fallback.assert_not_called()
        self.assertEqual(app.paste_target_hwnd, 123)

    def test_no_verified_composer_never_uses_coordinate_fallback_or_pastes(self):
        with patch.object(app, "resolve_chatgpt_window", return_value=123), \
                patch.object(app, "get_cursor_pos", return_value=app.POINT()), \
                patch.object(app.user32, "ShowWindow"), patch.object(app.user32, "SetForegroundWindow"), \
                patch.object(app.user32, "GetForegroundWindow", return_value=123), \
                patch.object(app.user32, "GetAncestor", return_value=123), \
                patch.object(app, "chromium_window_wrapper", return_value=self.window), \
                patch.object(app, "find_chatgpt_composer", return_value=None), \
                patch.object(app, "left_click") as click, patch.object(app, "send_ctrl_v") as paste:
            self.assertFalse(app.paste_into_chatgpt({"paste_delay_ms": 0, "chatgpt_allowed_hosts": ["chatgpt.com"],
                                                   "restore_mouse_position": False, "auto_click_composer": True}))
            click.assert_not_called()
            paste.assert_not_called()

    def test_paste_clicks_only_editor_and_requires_its_keyboard_focus(self):
        self.editor._bounds = (40, 500, 360, 550)
        for focused in (True, False):
            with self.subTest(focused=focused):
                app.paste_target_composer_title = "Training Help"
                app.paste_target_composer_url = self.doc._url
                app.paste_target_document_id = app.document_runtime_id(self.doc)
                with patch.object(app, "resolve_chatgpt_window", return_value=123), \
                        patch.object(app, "get_cursor_pos", return_value=app.POINT()), \
                        patch.object(app.user32, "ShowWindow"), patch.object(app.user32, "SetForegroundWindow"), \
                        patch.object(app.user32, "GetForegroundWindow", return_value=123), \
                        patch.object(app.user32, "GetAncestor", return_value=123), \
                        patch.object(app, "chromium_window_wrapper", return_value=self.window), \
                        patch.object(app, "input_modifiers_down", return_value=False), \
                        patch.object(self.editor, "has_keyboard_focus", return_value=focused), \
                        patch.object(app, "left_click") as click, patch.object(app, "send_ctrl_v") as paste:
                    self.assertEqual(app.paste_into_chatgpt({"paste_delay_ms": 0, "chatgpt_allowed_hosts": ["chatgpt.com"],
                                                           "restore_mouse_position": False}), focused)
                    click.assert_called_once_with(200, 525)
                    self.assertEqual(paste.call_count, int(focused))

    def test_address_check_never_selects_tabs_even_with_legacy_scan_flag(self):
        with patch.object(app, "chromium_window_wrapper", return_value=self.window), \
                patch.object(app, "chromium_window_has_allowed_address", return_value=False), \
                patch.object(self.window, "descendants", side_effect=AssertionError("Do not inspect/select tabs")):
            self.assertFalse(app.ensure_chatgpt_tab(123, ["chatgpt.com"], scan_tabs=True))

    def test_any_held_modifier_is_detected(self):
        for held in (app.VK_CONTROL, app.VK_SHIFT, app.VK_MENU, app.VK_LWIN, app.VK_RWIN):
            with self.subTest(held=held), patch.object(app.user32, "GetAsyncKeyState",
                                                      side_effect=lambda vk: 0x8000 if vk == held else 0):
                self.assertTrue(app.input_modifiers_down())

    def test_capture_uses_outer_page_not_nested_iframe_or_browser_ui(self):
        browser_ui = Control("Document", "Vivaldi", url="chrome-extension://vivaldi/window.html", bounds=(0, 0, 1200, 800))
        page = Control("Document", "Source tab", parent=browser_ui, url="https://example.org", bounds=(50, 80, 550, 700))
        iframe = Control("Document", "Nested frame", parent=page, url="https://nested.example.org", bounds=(100, 100, 400, 600))
        hit = Control("Text", "Content", parent=iframe)
        with patch.object(app, "Desktop", return_value=SimpleNamespace(from_point=lambda x, y: hit)):
            self.assertEqual(app.vivaldi_tile_rect(app.POINT(200, 200), (0, 0, 1200, 800)), (50, 80, 550, 700))

    def test_missing_page_or_bad_bounds_cancels_capture(self):
        for hit in (Control("Document", "Browser UI", url="chrome-extension://vivaldi/window.html"),
                    Control("Document", "Page", url="https://example.org", bounds=(0, 0, 0, 0))):
            with patch.object(app, "Desktop", return_value=SimpleNamespace(from_point=lambda x, y: hit)):
                with self.assertRaises(RuntimeError):
                    app.vivaldi_tile_rect(app.POINT(200, 200), (0, 0, 1200, 800))

    def test_vivaldi_capture_grabs_only_tile_region(self):
        with patch.object(app, "get_cursor_pos", return_value=app.POINT(200, 200)), \
                patch.object(app, "hovered_root_window", return_value=123), \
                patch.object(app, "window_rect", return_value=(0, 0, 1200, 800)), \
                patch.object(app, "window_text", return_value="Source tab - Vivaldi"), \
                patch.object(app, "is_valid_chromium_window", return_value=True), \
                patch.object(app, "vivaldi_tile_rect", return_value=(50, 80, 550, 700)), \
                patch.object(app.ImageGrab, "grab") as grab:
            app.capture_window_under_cursor({"save_captures": False})
            grab.assert_called_once_with(bbox=(50, 80, 550, 700), all_screens=True)


if __name__ == "__main__":
    unittest.main()
