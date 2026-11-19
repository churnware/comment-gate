import unittest

from comment_gate import comments, diffscan


def bodies(text, lang):
    return [u.body for u in comments.units(text, lang)]


class ExtractionTests(unittest.TestCase):
    def test_js_skips_strings_templates_and_regex(self):
        src = (
            'const a = "// not";\n'
            "const b = `/* nope */ ${x} // nope`;\n"
            "const r = /https?:\\/\\//g; // trailing\n"
            "const d = x / y; // after division\n"
        )
        self.assertEqual(bodies(src, "js"), ["trailing", "after division"])

    def test_adjacent_line_comments_group(self):
        src = "  // one\n  // two\n\n  // three\nx(); // four\n"
        us = comments.units(src, "js")
        self.assertEqual([u.body for u in us], ["one\ntwo", "three", "four"])
        self.assertTrue(us[-1].code_before)

    def test_jsdoc_body(self):
        src = "/**\n * Adds.\n *\n * @param a first\n */\nfunction f(a) {}\n"
        (u,) = comments.units(src, "js")
        self.assertEqual((u.opener, u.body), ("/**", "Adds.\n\n@param a first"))

    def test_python_comments_and_docstrings(self):
        src = 'def f():\n    """Doc.\n\n    More.\n    """\n    s = "# no"  # yes\n    return s\n'
        us = comments.units(src, "python")
        self.assertEqual([(u.kind, u.body) for u in us], [("doc", "Doc.\n\nMore."), ("line", "yes")])

    def test_python_non_docstring_strings_ignored(self):
        self.assertEqual(bodies('x = 1\n"""not a docstring"""\n', "python"), [])

    def test_shell_hash_requires_word_boundary(self):
        self.assertEqual(bodies('echo $# ${#a} "# q" # real\n', "hash"), ["real"])

    def test_other_families(self):
        self.assertEqual(bodies("SELECT 1; -- why\n/* block */\n", "sql"), ["why", "block"])
        self.assertEqual(bodies("<div><!-- note --></div>\n", "markup"), ["note"])
        self.assertEqual(bodies("<# help #>\n$x = 1 # set\n", "ps1"), ["help", "set"])
        self.assertEqual(bodies("a { color: red; } /* c */\n", "css"), ["c"])

    def test_crlf(self):
        us = comments.units("x = 1  # a\r\n# b\r\n", "python")
        self.assertEqual([u.body for u in us], ["a", "b"])

    def test_language_for(self):
        self.assertEqual(comments.language_for("a/b.tsx"), "js")
        self.assertEqual(comments.language_for("Makefile"), "hash")
        self.assertIsNone(comments.language_for("data.bin"))


class ProtectionTests(unittest.TestCase):
    def check(self, src, lang, expected):
        u = comments.units(src, lang)[0]
        self.assertEqual(comments.protected(u, src), expected, src)

    def test_directives_protected(self):
        for src, lang in [
            ("// eslint-disable-next-line no-console\n", "js"),
            ("// @ts-expect-error legacy\n", "js"),
            ("x = 1  # noqa: E501\n", "python"),
            ("x = 1  # type: ignore[attr]\n", "python"),
            ("#!/usr/bin/env bash\n", "hash"),
            ("//go:build linux\n", "go"),
            ("// SPDX-License-Identifier: MIT\n", "c"),
            ("/* istanbul ignore next */\n", "js"),
            ("// #region helpers\n", "js"),
            ("/* global window, document */\n", "js"),
        ]:
            self.check(src, lang, True)

    def test_ordinary_comments_not_protected(self):
        for src in ["// increment the counter\n", "// Global lock guards the cache.\n", "// the region of interest\n"]:
            self.check(src, "js", False)

    def test_extra_patterns(self):
        src = "// KEEP: vendor note\n"
        u = comments.units(src, "js")[0]
        self.assertTrue(comments.protected(u, src, [r"^KEEP:"]))


class MarkupEmbedTests(unittest.TestCase):
    SRC = (
        "<!-- page -->\n<style>\n  /* glow */\n  h1 { color: red; }\n</style>\n"
        '<script type="application/json">{"a": "// not a comment"}</script>\n'
        '<script src="x.js"/>\n'
        '<script type="module">\n  // set up the loop\n  const s = "<!-- no -->"; /* inline */ run(s);\n</script>\n'
        '<style lang="sass">\n  // sass is left alone\n</style>\n'
        "<!-- tail -->\n"
    )

    def test_script_and_style_comments_extracted(self):
        units = comments.units(self.SRC, "markup")
        self.assertEqual([u.body for u in units], ["page", "glow", "set up the loop", "inline", "tail"])
        self.assertEqual([u.lang for u in units], ["", "css", "js", "js", ""])

    def test_vue_script_lang(self):
        src = '<template><!-- t --></template>\n<script lang="ts">\n// typed\n</script>\n'
        self.assertEqual(bodies(src, "markup"), ["t", "typed"])


class DiffScopeTests(unittest.TestCase):
    def test_only_new_or_changed_comments(self):
        old = "// existing\nlet a = 1;\n// stays\n"
        new = "// existing\nlet a = 1; // added\n// stays\n// brand new\n"
        self.assertEqual([u.body for u in diffscan.candidates(old, new, "js")], ["added", "brand new"])

    def test_new_file_is_fully_reviewed(self):
        self.assertEqual(len(diffscan.candidates(None, "// a\nx;\n// b\n", "js")), 2)

    def test_protected_excluded(self):
        self.assertEqual(diffscan.candidates(None, "// eslint-disable\n// note\n", "js"), [])


if __name__ == "__main__":
    unittest.main()
