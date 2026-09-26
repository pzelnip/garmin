from notes_html import render


def test_two_space_nested_list_renders_nested():
    out = render("- outer\n  - inner\n")
    assert "<ul>\n<li>outer\n<ul>\n<li>inner</li>" in out


def test_bold_and_ordered_list():
    out = render("1. **new** symptom\n2. second\n")
    assert "<ol>" in out
    assert "<strong>new</strong>" in out


def test_raw_html_is_escaped():
    out = render("<script>alert(1)</script>\n")
    assert "<script>" not in out
    assert "&lt;script&gt;" in out


def test_title_comes_from_first_h1():
    out = render("intro\n\n# Notes for physio & more\n\n## Section\n")
    assert "<title>Notes for physio &amp; more</title>" in out


def test_has_viewport_meta():
    assert 'name="viewport"' in render("# x\n")
