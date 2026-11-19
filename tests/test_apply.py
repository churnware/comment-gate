import unittest

from comment_gate import apply, comments


def run(src, lang, *decisions):
    """decisions: (body_substring, action, text)"""
    decided = []
    for u in comments.units(src, lang):
        for match, action, text in decisions:
            if match in u.body:
                decided.append((u, {"action": action, "text": text, "reason": ""}))
                break
    new, ok, bad = apply.apply_decisions(src, lang, decided)
    return new, len(ok), len(bad)


class ApplyTests(unittest.TestCase):
    def test_delete_standalone_line_group(self):
        src = "def f():\n    # set x\n    # to one\n    x = 1\n    return x\n"
        new, ok, bad = run(src, "python", ("set x", "delete", ""))
        self.assertEqual(new, "def f():\n    x = 1\n    return x\n")
        self.assertEqual((ok, bad), (1, 0))

    def test_delete_trailing(self):
        new, *_ = run("let i = 0;\ni++; // increment i\n", "js", ("increment", "delete", ""))
        self.assertEqual(new, "let i = 0;\ni++;\n")

    def test_delete_inline_block(self):
        new, *_ = run("f(a /* the a */, b);\n", "js", ("the a", "delete", ""))
        self.assertEqual(new, "f(a, b);\n")

    def test_delete_inline_block_never_glues_tokens(self):
        new, *_ = run("return/* why */x;\n", "js", ("why", "delete", ""))
        self.assertEqual(new, "return x;\n")
        new, *_ = run("let a = b /* c */ + d;\n", "js", ("c", "delete", ""))
        self.assertEqual(new, "let a = b + d;\n")

    def test_skeleton_distinguishes_glued_tokens(self):
        self.assertNotEqual(apply.skeleton("return x;\n", "js"), apply.skeleton("returnx;\n", "js"))
        self.assertEqual(apply.skeleton("f(a , b); // c\n", "js"), apply.skeleton("f(a, b);\n", "js"))

    def test_rewrite_line_group_keeps_indent_and_marker(self):
        src = "  /// Long winded\n  /// text here\n  fn x() {}\n"
        new, *_ = run(src, "c", ("Long", "rewrite", "Short.\n\nSecond para."))
        self.assertEqual(new, "  /// Short.\n  ///\n  /// Second para.\n  fn x() {}\n")

    def test_rewrite_strips_echoed_markers(self):
        new, *_ = run("# Here we now compute it\nx = 1\n", "python", ("Here", "rewrite", "# Compute x."))
        self.assertEqual(new, "# Compute x.\nx = 1\n")

    def test_rewrite_rewraps_to_original_width(self):
        src = ("    // The canvas keeps a fixed logical size; we only scale its backing\n"
               "    // store for high-DPI screens. CSS fits it to the window.\n    f();\n")
        text = ("The canvas keeps a fixed logical size. Backing-store scaling handles high-DPI "
                "screens, and CSS fits the canvas to the window.")
        new, ok, _ = run(src, "js", ("canvas", "rewrite", text))
        self.assertEqual(ok, 1)
        lines = new.splitlines()[:-1]
        self.assertGreater(len(lines), 1)
        self.assertTrue(all(l.startswith("    // ") and len(l) <= 74 for l in lines), lines)
        self.assertEqual(" ".join(l[7:] for l in lines), text)

    def test_rewrite_wraps_long_single_line_at_80(self):
        new, *_ = run("# short note\nx = 1\n", "python", ("short", "rewrite", "word " * 30))
        self.assertTrue(all(len(l) <= 80 for l in new.splitlines()))
        self.assertGreater(len(new.splitlines()), 2)

    def test_rewrite_leaves_indented_lines_and_trailing_alone(self):
        long = "x" * 100
        new, *_ = run("// a\n// b\nf();\n", "js", ("a", "rewrite", f"Example:\n    {long}"))
        self.assertIn(f"//     {long}\n", new)
        new, *_ = run("f(); // a\n", "js", ("a", "rewrite", "word " * 30))
        self.assertEqual(len(new.splitlines()), 1)

    def test_rewrite_list_items_get_hanging_indent(self):
        new, *_ = run("// a\n// b\nf();\n", "js", ("a", "rewrite", "- " + "item text " * 12))
        lines = new.splitlines()[:-1]
        self.assertTrue(lines[0].startswith("// - "))
        self.assertTrue(all(l.startswith("//   ") for l in lines[1:]), lines)

    def test_rewrite_straightens_smart_quotes(self):
        lq, rq, apos = chr(0x201C), chr(0x201D), chr(0x2019)
        new, *_ = run("// the arrow length\nf();\n", "js",
                      ("arrow", "rewrite", f"The arrow{apos}s length, {lq}spin{rq}."))
        self.assertEqual(new, "// The arrow's length, \"spin\".\nf();\n")
        src = f"// the arrow{apos}s length\nf();\n"
        new, *_ = run(src, "js", ("arrow", "rewrite", f"The arrow{apos}s length."))
        self.assertIn(apos, new)

    def test_rewrite_trailing_joins_lines(self):
        new, *_ = run("x = 1  # a b c\n", "python", ("a b", "rewrite", "one\ntwo"))
        self.assertEqual(new, "x = 1  # one two\n")

    def test_rewrite_jsdoc(self):
        src = "  /**\n   * Very long.\n   */\n  f() {}\n"
        new, *_ = run(src, "js", ("Very", "rewrite", "Short.\n@returns nothing"))
        self.assertEqual(new, "  /**\n   * Short.\n   * @returns nothing\n   */\n  f() {}\n")

    def test_rewrite_single_line_block(self):
        new, *_ = run("/* wordy wordy */\nx();\n", "js", ("wordy", "rewrite", "terse"))
        self.assertEqual(new, "/* terse */\nx();\n")

    def test_rewrite_docstring(self):
        src = 'def f():\n    """This function is the function that does f."""\n    return 1\n'
        new, *_ = run(src, "python", ("does f", "rewrite", "Do f."))
        self.assertEqual(new, 'def f():\n    """Do f."""\n    return 1\n')

    def test_multiline_docstring(self):
        src = 'def f():\n    """Old."""\n    return 1\n'
        new, *_ = run(src, "python", ("Old", "rewrite", "Summary.\n\nArgs:\n    none"))
        self.assertEqual(new, 'def f():\n    """Summary.\n\n    Args:\n        none\n    """\n    return 1\n')

    def test_docstring_delete_rejected(self):
        src = 'def f():\n    """Doc."""\n    return 1\n'
        new, ok, bad = run(src, "python", ("Doc", "delete", ""))
        self.assertEqual((new, ok, bad), (src, 0, 1))

    def test_code_injection_rejected(self):
        src = "/* note */\nx();\n"
        new, ok, bad = run(src, "js", ("note", "rewrite", "fine */ evil(); /* more"))
        self.assertEqual((new, ok, bad), (src, 0, 1))

    def test_line_comment_newline_cannot_inject_code(self):
        src = "x();  // note\n"
        new, ok, _ = run(src, "js", ("note", "rewrite", "fine\nevil();"))
        self.assertEqual(new, "x();  // fine evil();\n")
        self.assertEqual(ok, 1)

    def test_docstring_quote_injection_rejected(self):
        src = 'def f():\n    """Doc."""\n'
        new, ok, bad = run(src, "python", ("Doc", "rewrite", 'x"""; import os; """'))
        self.assertEqual((new, ok, bad), (src, 0, 1))

    def test_crlf_preserved(self):
        src = "x = 1\r\n# a\r\n# b\r\ny = 2\r\n"
        new, *_ = run(src, "python", ("a", "rewrite", "one\ntwo\nthree"))
        self.assertEqual(new, "x = 1\r\n# one\r\n# two\r\n# three\r\ny = 2\r\n")

    def test_multiple_edits_bottom_up(self):
        src = "a(); // one\nb(); // two\n// three\nc();\n"
        new, ok, _ = run(src, "js", ("one", "delete", ""), ("two", "rewrite", "2"), ("three", "delete", ""))
        self.assertEqual(new, "a();\nb(); // 2\nc();\n")
        self.assertEqual(ok, 3)


if __name__ == "__main__":
    unittest.main()
