from pathlib import Path

import pytest

from delegate.review import changed_lines, render_brief, scan_secrets, validate_output


def test_validate_variants():
    assert validate_output("NO FINDINGS").kind == "none"
    assert validate_output("```\nNO FINDINGS\n```").kind == "none"
    line = "🔴 src/a.py:12 — bad — breaks x"
    for separator in (" — ", " - ", " – "):
        result = validate_output(line.replace(" — ", separator))
        assert result.kind == "findings"
        assert result.findings == [("blocker", "src/a.py", 12, "bad", "breaks x")]
    for text in ("Looks good!", "", line + "\nLooks good!"):
        result = validate_output(text)
        assert result.kind == "invalid"
        assert result.findings == []
    assert validate_output("COULD NOT READ C:/x").kind == "could_not_read"
    assert validate_output("🟡 a b.py:1 — slow — waits\n\n🟢 c.py:2 - typo - unclear").findings == [
        ("major", "a b.py", 1, "slow", "waits"),
        ("nit", "c.py", 2, "typo", "unclear"),
    ]


@pytest.mark.parametrize(("addition", "masked"), [
    ("-----BEGIN " + "RSA PRIVATE KEY-----", "----…"),
    ("AKIA" + "ABCDEFGHIJKLMNOP", "AKIA…"),
    ("sk-" + "a" * 40, "sk-a…"),
    ("ghp_" + "b" * 36, "ghp_…"),
    ("github_pat_" + "c" * 40, "gith…"),
    ("AIza" + "d" * 35, "AIza…"),
    ("eyJhbGciOiJIUzI1NiJ9" + ".eyJzdWIiOiIxMjMifQ.c2lnbmF0dXJl", "eyJh…"),
    ("api_key = " + '"abcd1234abcd1234abcd"', "abcd…"),
    ("PASSWORD: " + "'Sup3rS3cretValue!!'", "Sup3…"),
])
def test_scan_secrets_hits(addition, masked):
    diff = f"--- a/app.py\n+++ b/app.py\n@@ -7,0 +8 @@\n+{addition}\n"
    hits = scan_secrets(diff, [])
    assert ("app.py", 8, masked) in [(hit.file, hit.line, hit.masked) for hit in hits]


def test_scan_secrets_misses():
    removed = "AKIA" + "ABCDEFGHIJKLMNOP"
    context = "-----BEGIN " + "RSA PRIVATE KEY-----"
    diff = (
        "--- a/app.py\n+++ b/app.py\n@@ -1,2 +1,3 @@\n"
        f"-{removed}\n {context}\n"
        '+password = os.environ["X"]\n'
        '+token = ""\n'
    )
    assert scan_secrets(diff, []) == []


@pytest.mark.parametrize("path", [".env", "config b/.env.local"])
def test_env_file_is_hit(path):
    diff = f"diff --git a/{path} b/{path}\nnew file mode 100644\n"
    hits = scan_secrets(diff, [])
    assert [(hit.file, hit.kind) for hit in hits] == [(path, "env-file")]


def test_allowlist_skips_fixture_path():
    diff = (
        "--- /dev/null\n+++ b/tests/fixtures/.env\n@@ -0,0 +1 @@\n"
        "+ghp_" + "a" * 36 + "\n"
    )
    assert scan_secrets(diff, ["tests/fixtures/**"]) == []


def test_brief_has_no_history_and_rules():
    tree = Path("repo with space")
    patch = Path("task evidence/diff.patch")
    brief = render_brief(
        "Handle missing files", "Report the path", patch, tree, {"src/a.py": [2, 3, 4, 8]}, 5,
    )
    assert "Handle missing files" in brief
    assert "Report the path" in brief
    assert tree.resolve().as_posix() in brief
    assert patch.resolve().as_posix() in brief
    assert "Recently changed" in brief and "src/a.py:2-4" in brief
    assert "Do NOT run shell/terminal commands (they are denied); read files only with your file-reading tools." in brief
    assert "Spend at most ~5 minutes; do not build test harnesses." in brief
    assert "<🔴|🟡|🟢> <file>:<line> — <problem> — <why>" in brief
    assert "If you could not read the change, output exactly: COULD NOT READ <path>" in brief
    assert "If you find nothing, output exactly: NO FINDINGS" in brief
    assert "round" not in brief.lower()
    assert "previous" not in brief.lower()


def test_changed_lines():
    diff = (
        "--- a/مجلد/a b.py\n+++ b/مجلد/a b.py\n"
        "@@ -1,3 +1,4 @@\n one\n-two\n+new two\n+extra\n three\n"
        "@@ -10,2 +11,2 @@ section\n-old\n unchanged\n+new\n"
        "\\ No newline at end of file\n"
    )
    assert changed_lines(diff) == {"مجلد/a b.py": [2, 3, 12]}
