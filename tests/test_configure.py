import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from comment_gate import config, configure, installer, models, ui


class ConfigureTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.project = base / "proj"
        self.project.mkdir()
        env = {k: v for k, v in os.environ.items() if not k.startswith("COMMENT_GATE")}
        env["COMMENT_GATE_HOME"] = str(base / "home")
        self.codex_home = base / "codex"
        env["CODEX_HOME"] = str(self.codex_home)
        patcher = mock.patch.dict(os.environ, env, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)

    def write_global(self, data):
        config.write_file(None, data)

    def field(self, editor, key):
        editor.index = next(i for i, f in enumerate(configure.FIELDS) if f.key == key)

    def test_layers_defaults_global_project_env(self):
        self.write_global({"mode": "report", "timeout_sec": 120})
        config.write_file(self.project, {"mode": "suggest"})
        cfg = config.load(self.project)
        self.assertEqual((cfg["mode"], cfg["timeout_sec"], cfg["max_batches"]), ("suggest", 120, 6))
        with mock.patch.dict(os.environ, {"COMMENT_GATE_MODE": "apply"}):
            self.assertEqual(config.load(self.project)["mode"], "apply")

    def test_unknown_global_key_is_rejected(self):
        self.write_global({"modee": "apply"})
        with self.assertRaisesRegex(ValueError, "modee"):
            config.load(self.project)

    def test_install_creates_a_file_that_inherits(self):
        installer.install(self.project)
        self.assertEqual(config.read_file(self.project), {})
        installer.install(self.project, mode="report")
        self.assertEqual(config.read_file(self.project), {"mode": "report"})

    def test_project_editor_saves_only_what_it_sets(self):
        self.write_global({"reasoning_effort": "high"})
        config.write_file(self.project, {"mode": "suggest", "unrelated": 1})
        e = configure.Editor(self.project)
        self.field(e, "reasoning_effort")
        self.assertNotIn("reasoning_effort", e.own)
        e.handle("right")
        self.field(e, "mode")
        e.handle("r")
        self.assertEqual(e.values["mode"], "apply")
        self.assertTrue(e.dirty)
        e.save()
        self.assertFalse(e.dirty)
        self.assertEqual(config.read_file(self.project), {"unrelated": 1, "reasoning_effort": "minimal"})

    def test_text_field_backspace_unsets_over_global(self):
        self.write_global({"model": "gpt-x"})
        e = configure.Editor(self.project)
        self.field(e, "model")
        e.handle("backspace")
        e.save()
        self.assertEqual(config.read_file(self.project), {"model": None})

    def test_int_fields_respect_their_floor(self):
        e = configure.Editor(None)
        self.field(e, "max_batches")
        for _ in range(10):
            e.handle("left")
        self.assertEqual(e.values["max_batches"], 1)

    def test_quit_with_unsaved_changes_needs_a_second_esc(self):
        e = configure.Editor(None)
        e.handle("right")
        self.assertIsNone(e.handle("esc"))
        self.assertEqual(e.handle("esc"), "quit")
        self.assertEqual(config.read_file(None), {})

    def test_global_timeout_change_updates_installed_hooks(self):
        installer.install(self.project)
        self.assertEqual(installer.hook_timeout(self.project, False), 120)
        e = configure.Editor(None)
        self.field(e, "timeout_sec")
        e.handle("right")
        e.save()
        self.assertEqual(installer.hook_timeout(self.project, False), 135)

    def write_models(self):
        def model(slug, priority, efforts, visibility="list"):
            return {"slug": slug, "display_name": slug.upper(), "description": f"about {slug}",
                    "visibility": visibility, "priority": priority,
                    "supported_reasoning_levels": [{"effort": e, "description": f"{e} thinking"} for e in efforts]}

        self.codex_home.mkdir()
        (self.codex_home / "models_cache.json").write_text(json.dumps({"models": [
            model("gpt-6-luna", 3, ["low", "medium"]),
            model("gpt-6.2-astra", 2, ["low", "medium", "high", "max"]),
            model("internal", 1, ["low"], visibility="hide"),
            model("gpt-6.2-sol", 1, ["low", "high"]),
        ]}), encoding="utf-8")

    def test_models_are_read_in_codex_order_without_hidden_ones(self):
        self.write_models()
        self.assertEqual([m.slug for m in models.load()], ["gpt-6.2-sol", "gpt-6.2-astra", "gpt-6-luna"])
        found = models.search(models.load(), "62a")
        self.assertEqual([m.slug for m in found], ["gpt-6.2-astra"])
        self.assertEqual([m.slug for m in models.search(models.load(), "luna")], ["gpt-6-luna"])

    def test_missing_or_broken_model_cache_gives_no_models(self):
        self.assertEqual(models.load(), [])
        self.codex_home.mkdir()
        (self.codex_home / "models_cache.json").write_text("{nope", encoding="utf-8")
        self.assertEqual(models.load(), [])

    def test_arrows_cycle_through_codex_models(self):
        self.write_models()
        e = configure.Editor(None)
        self.field(e, "model")
        seen = []
        for _ in range(4):
            e.handle("right")
            seen.append(e.values["model"])
        self.assertEqual(seen, ["gpt-6.2-sol", "gpt-6.2-astra", "gpt-6-luna", None])
        e.handle("left")
        self.assertEqual(e.values["model"], "gpt-6-luna")

    def test_picker_filters_completes_and_picks(self):
        self.write_models()
        e = configure.Editor(None)
        self.field(e, "model")
        e.handle("enter")
        self.assertIsNotNone(e.picker)
        for key in "astra":
            e.handle(key)
        self.assertEqual([v for v, _ in e.picker.options()], ["gpt-6.2-astra", "astra"])
        e.handle("\t")
        self.assertEqual(e.picker.query, "gpt-6.2-astra")
        e.handle("enter")
        self.assertIsNone(e.picker)
        self.assertEqual(e.values["model"], "gpt-6.2-astra")
        self.assertIn("about gpt-6.2-astra", e._help())

    def test_picker_keeps_a_model_typed_by_hand_and_esc_cancels(self):
        self.write_models()
        e = configure.Editor(None)
        self.field(e, "model")
        e.handle("enter")
        for key in "my-model":
            e.handle(key)
        e.handle("up")
        e.handle("enter")
        self.assertEqual(e.values["model"], "my-model")
        self.assertIn("not in Codex's model list", e._help())
        e.handle("enter")
        self.assertEqual(e.picker.index, 0)
        e.handle("s")
        e.handle("esc")
        self.assertIsNone(e.picker)
        self.assertEqual((e.values["model"], config.read_file(None)), ("my-model", {}))

    def test_efforts_follow_the_selected_model(self):
        self.write_models()
        e = configure.Editor(None)
        self.assertEqual(e.efforts(), ("low", "high", "medium", "max"))
        self.field(e, "model")
        e.handle("right")
        e.handle("right")
        self.field(e, "reasoning_effort")
        self.assertEqual(e.efforts(), ("low", "medium", "high", "max"))
        self.assertIn("low thinking", e._help())
        e.handle("left")
        self.assertEqual(e.values["reasoning_effort"], "max")
        self.field(e, "model")
        e.handle("right")
        self.assertTrue(e.effort_unsupported())
        self.field(e, "reasoning_effort")
        self.assertIn("gpt-6-luna supports no max effort", e._help())
        e.handle("right")
        self.assertEqual(e.values["reasoning_effort"], "low")
        self.assertFalse(e.effort_unsupported())

    def test_markers_leave_a_fading_trail(self):
        self.write_models()
        e = configure.Editor(None)
        self.field(e, "mode")
        with mock.patch.object(ui, "COLOR", True):
            e.handle("down")
            self.assertEqual(set(e.trail.rows), {1})
            e.handle("enter")
            self.assertFalse(e.picker.trail.fading)
            e.handle("down")
            self.assertEqual(set(e.picker.trail.rows), {0})
            e.handle("a")
            self.assertFalse(e.picker.trail.fading)
        for _ in range(6):
            e.trail.step()
        self.assertFalse(e.trail.fading)

    def test_picker_frame_fits_the_terminal(self):
        self.write_models()
        e = configure.Editor(self.project)
        self.field(e, "model")
        e.handle("enter")
        for line in (ui.fit(l, 79) for l in e.lines()):
            self.assertLessEqual(ui.visible_len(line), 79)

    def test_every_frame_line_fits_the_terminal(self):
        e = configure.Editor(self.project)
        e.status = "x" * 300
        for f in range(ui.FRAMES):
            e.wheel.angle = -f * 0.3
            for line in (ui.fit(l, 79) for l in e.lines()):
                self.assertLessEqual(ui.visible_len(line), 79)


if __name__ == "__main__":
    unittest.main()
