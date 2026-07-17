"""Interactive Goals editor — the one command behind `make edit-goals`.

It opens goals.json in VS Code and stays running until you close the tab:

  * on every save it validates + auto-fixes (sorts each phase's rungs
    newest-first, reformats) and, if it passes, publishes to Neon — the change
    is live on the Goals tab at once. A save that fails prints what to fix and
    waits for the next save.
  * when you close the tab it does a final validation and then commits goals.json
    and pushes to origin (the Pi picks it up on its next pull).

Run via scripts/edit-goals.sh so .envrc (CONN_STR) is loaded.
"""

import hashlib
import os
import subprocess
import sys
import time

import push_goals
import validate_goals as vg

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
GOALS_JSON = os.path.join(REPO_ROOT, "goals.json")


def _c(code, s):
    return f"\033[{code}m{s}\033[0m" if sys.stdout.isatty() else s


def _green(s):
    return _c("32", s)


def _red(s):
    return _c("31", s)


def _dim(s):
    return _c("2", s)


def _sha(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def _git(*args):
    return subprocess.run(
        ["git", "-C", REPO_ROOT, *args], capture_output=True, text=True
    )


def _write_canonical(fixed):
    """Rewrite goals.json in its canonical (sorted, formatted) form if the
    on-disk text differs. Returns True if the file was rewritten."""
    text = vg.dumps(fixed)
    with open(GOALS_JSON, encoding="utf-8") as fh:
        if fh.read() == text:
            return False
    with open(GOALS_JSON, "w", encoding="utf-8") as fh:
        fh.write(text)
    return True


def _handle_save():
    try:
        data = vg.load(GOALS_JSON)
        fixed, changes = vg.validate_and_fix(data)
    except vg.GoalsError as e:
        print(_red("✗ not published — fix these and save again:"))
        print(_dim("  " + str(e).replace("\n", "\n  ")))
        return
    _write_canonical(fixed)
    if changes:
        print(_dim("↺ auto-fixed: " + "; ".join(changes)))
    _, phases = push_goals.push(fixed)
    print(_green(f"✓ published ({phases} phases) — live on the Goals tab now."))


def _commit_and_push():
    if not _git("status", "--porcelain", "goals.json").stdout.strip():
        print(_dim("No changes to goals.json — nothing to commit."))
        return
    _git("add", "goals.json")
    commit = _git("commit", "-m", "Update goals")
    if commit.returncode != 0:
        print(_red("commit failed:\n" + (commit.stderr or commit.stdout)))
        return
    push = _git("push")
    if push.returncode != 0:
        print(_red("committed, but push failed:\n" + (push.stderr or push.stdout)))
        print(_dim("Run `git push` yourself when ready."))
        return
    print(_green("✓ committed & pushed — the Pi picks it up on its next pull."))


def main():
    if not os.path.exists(GOALS_JSON):
        print(_red(f"{GOALS_JSON} not found"))
        return 1
    print(
        _dim(
            "Opening goals.json — save to validate & publish; close the tab to commit & push."
        )
    )
    proc = subprocess.Popen(["code", "--wait", GOALS_JSON])
    last = _sha(GOALS_JSON)  # baseline: only react to saves made after opening
    try:
        while proc.poll() is None:
            time.sleep(1.0)
            try:
                current = _sha(GOALS_JSON)
            except FileNotFoundError:
                continue
            if current == last:
                continue
            _handle_save()
            last = _sha(GOALS_JSON)  # our own auto-fix rewrite must not re-trigger
    except KeyboardInterrupt:
        proc.terminate()
        print(_dim("\nInterrupted — nothing committed."))
        return 1

    try:
        data = vg.load(GOALS_JSON)
        fixed, _ = vg.validate_and_fix(data)
    except vg.GoalsError as e:
        print(_red("✗ goals.json is still invalid — NOT committing:"))
        print(_dim("  " + str(e).replace("\n", "\n  ")))
        print(_dim("Re-run `make edit-goals`, fix it, then close the tab again."))
        return 1
    _write_canonical(fixed)
    _commit_and_push()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
