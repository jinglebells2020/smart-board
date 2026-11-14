"""The student notebook: one Markdown file per student, kept on disk.

deepagents loads it as the agent's memory at the start of every turn
(create_deep_agent(memory=[...])), so the tutor remembers this student
between runs. After each check the agent adds one entry with its own
file tool (edit_file): what happened, the quiz result and one line on
what to try next time.

The agent can reach this folder only: it sits behind a deepagents
FilesystemBackend whose root is the notebook folder, and permissions
allow writing this one student's file and nothing else.

Plain Python fills in what the agent cannot know when it writes the
entry (the quiz is answered later, on the page), and adds the entry
itself if the agent forgot, so the notebook is always complete.
"""

import re
import time
from pathlib import Path

# The agent adds each entry just above this line (edit_file replaces it).
# HTML comments are not shown to the model in memory, so it costs nothing.
MARKER = "<!-- new entries go above this line -->"
QUIZ_OPEN = "Quiz: not answered yet."
ROUTE = "/notebook/"  # where the notebook folder appears to the agent


def safe_name(student):
    """A student name that is safe as a file name: letters, digits, - and _."""
    name = re.sub(r"[^a-z0-9_-]+", "-", str(student).strip().lower()).strip("-")
    return name or "student"


class Notebook:
    def __init__(self, folder, student="student"):
        self.folder = Path(folder)
        self.student = str(student)
        self.name = safe_name(student)
        self.path = self.folder / f"{self.name}.md"

    @property
    def agent_path(self):
        """The path the agent uses for this file, e.g. /notebook/maya.md."""
        return f"{ROUTE}{self.name}.md"

    def ensure(self):
        """Create the file the first time."""
        self.folder.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.write_text(
                f"# Notebook: {self.student}\n\n"
                "The tutor's notes about this student, one line per check.\n"
                "Read them before helping: build on what worked, and watch for\n"
                "mistakes that came back.\n\n"
                "## Checks\n\n"
                f"{MARKER}\n", encoding="utf-8")
        return self.path

    def text(self):
        return self.path.read_text(encoding="utf-8") if self.path.exists() else ""

    def entries(self):
        return [line for line in self.text().splitlines() if line.startswith("- ")]

    def start_over(self):
        """A new student in the same seat: keep the old notebook under a
        dated name and start an empty one."""
        if self.path.exists():
            stamp = time.strftime("%Y%m%d-%H%M%S")
            self.path.rename(self.path.with_name(f"{self.name}.{stamp}.md"))
        self.ensure()

    # --- Done by plain Python ----------------------------------------------
    def has_entry(self, entry):
        """Did the agent write this entry (the part Python prepared)?"""
        return entry in self.text()

    def add(self, line):
        """Add one entry above the marker (used when the agent forgot)."""
        self.ensure()
        text = self.text()
        if MARKER not in text:
            text = text.rstrip("\n") + f"\n\n{MARKER}\n"
        self.path.write_text(text.replace(MARKER, f"{line}\n{MARKER}", 1), encoding="utf-8")

    def fill_quiz(self, label, correct):
        """The student answered the quiz on the page: write the result into
        the latest entry about that mistake that is still waiting for it."""
        lines = self.text().splitlines()
        for i in range(len(lines) - 1, -1, -1):
            if lines[i].startswith("- ") and label in lines[i] and QUIZ_OPEN in lines[i]:
                result = "right" if correct else "wrong"
                lines[i] = lines[i].replace(QUIZ_OPEN, f"Quiz: {result}.", 1)
                self.path.write_text("\n".join(lines) + "\n", encoding="utf-8")
                return True
        return False


def entry_start(lesson_id, result, lesson):
    """The first part of a notebook entry for one check: the facts, from
    plain Python. The agent adds " Next time: ..." to it.
    None for a check with nothing to note (power off, missing rows)."""
    day = time.strftime("%b %d").replace(" 0", " ")
    status = result.get("status")
    if status == "mistake":
        return f"- {day}, lesson {lesson_id}: {mistake_label(lesson, result['mistake_id'])}. {QUIZ_OPEN}"
    if status == "ok":
        return f"- {day}, lesson {lesson_id}: circuit correct, lesson passed."
    if status == "unknown":
        details = result.get("clue", {}).get("details", [])
        what = details[0]["number"] + " " + details[0]["direction"] if details else "close"
        return (f"- {day}, lesson {lesson_id}: a mistake that is not on the list "
                f"({what}).")
    return None


def mistake_label(lesson, mistake_id):
    """The words a notebook entry uses for a mistake (its mini-lesson title)."""
    spec = next((m for m in lesson.get("mistakes", []) if m["id"] == mistake_id), {})
    return f"mistake \"{spec.get('mini_lesson', {}).get('title') or mistake_id}\""


def notebook_tools_backend(notebook):
    """The deepagents backend and permissions for one student's notebook.

    Files under /notebook/ are real files in the notebook folder; anything
    else lives only in the agent's short-term state. Permissions let the
    agent read and write this student's file, and write nothing else."""
    from deepagents import FilesystemPermission
    from deepagents.backends import CompositeBackend, FilesystemBackend, StateBackend

    backend = CompositeBackend(
        default=StateBackend(),
        routes={ROUTE: FilesystemBackend(root_dir=notebook.folder, virtual_mode=True)})
    permissions = [
        FilesystemPermission(operations=["read", "write"], paths=[notebook.agent_path]),
        FilesystemPermission(operations=["write"], paths=["/**"], mode="deny"),
        FilesystemPermission(operations=["read"], paths=[f"{ROUTE}**"], mode="deny"),
    ]
    return backend, permissions
