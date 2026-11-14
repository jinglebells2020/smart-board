"""Keep the demo going when Claude does not answer.

If a call to Claude fails, takes longer than CLAUDE_TIMEOUT seconds, or
there is no internet, that one message is answered by the offline tutor
(the same tools, with ScriptedTutorModel), and the page shows "Zip is in
offline mode for this answer". The next message tries Claude again.

Safety never waits for Claude: cutting power on a dangerous circuit,
turning power on only when the student asks, measuring at once after
power comes on, and the hint button are all plain Python (agent/session.py,
agent/buttons.py). The offline tutor uses the same rules.

A failed Claude reply may have done part of its work (measured, logged a
mistake). Before the offline tutor answers, the teaching state is put
back as it was before the message, so nothing is counted twice. The
board's power is never put back: if the failed reply cut power, it stays
off.

Every Claude reply writes one line in the server window with how long it
took, so we can see the timing.
"""

import copy
import time

from agent.build import CLAUDE_TIMEOUT, answer_power, ask, build_agent
from agent.mock_model import ScriptedTutorModel
from agent.serial_board import BoardError

OFFLINE_NOTE = "Zip is in offline mode for this answer"

# The teaching state a failed Claude reply might have changed.
STATE = ("history", "lesson_id", "passed_current", "step", "open_mistake", "last_logged",
         "last_check", "lesson_request", "new_ai_lesson", "pending_entry", "placement")


def why_failed(error, timeout=CLAUDE_TIMEOUT):
    """The reason, in plain words, for the server window."""
    name = type(error).__name__
    if "Timeout" in name:
        return f"no answer within {timeout:g} seconds"
    if "Connection" in name or isinstance(error, OSError):
        return "could not reach Claude: is the internet connected?"
    if "Authentication" in name or "Permission" in name:
        return "Claude did not accept the API key"
    text = str(error).splitlines()[0][:120] if str(error) else ""
    return f"{name}: {text}" if text else name


class SafeReplies:
    """Claude's reply, or the offline tutor's when Claude fails."""

    def __init__(self, session, ask_before_power=False, timeout=CLAUDE_TIMEOUT, log=None):
        self.session = session
        self.ask_before_power = ask_before_power
        self.timeout = timeout
        self.log = log or (lambda line: None)
        self._offline = None         # the offline tutor, made when first needed
        self._offline_direct = None  # the same, without asking before power on
        self.offline = False         # was the latest answer the offline tutor's?

    # --- The offline tutor -----------------------------------------------------
    def offline_agent(self, direct=False):
        if direct:
            if self._offline_direct is None:
                self._offline_direct = build_agent(self.session, ScriptedTutorModel())
            return self._offline_direct
        if self._offline is None:
            self._offline = build_agent(self.session, ScriptedTutorModel(),
                                        ask_before_power=self.ask_before_power)
        return self._offline

    def paused(self, agent):
        """The agent whose reply waits for "Turn the power back on?", or None."""
        for a in (agent, self._offline):
            if a is not None and getattr(a, "smart_board_pending", None) is not None:
                return a
        return None

    def forget_pause(self, agent):
        for a in (agent, self._offline):
            if a is not None:
                a.smart_board_pending = None

    # --- Replies ---------------------------------------------------------------
    def ask(self, agent, history, text):
        """One student message. Returns (reply, new messages)."""
        self.offline = False
        if getattr(agent, "smart_board_offline", True):
            return ask(agent, history, text)  # no Claude: nothing to fall back from
        saved = self._save()
        start = time.monotonic()
        try:
            reply, new = ask(agent, history, text, timeout=self.timeout)
        except BoardError:
            raise  # the board is the problem, not Claude
        except Exception as e:
            self._failed(agent, saved, start, e)
            return ask(self.offline_agent(), history, text)
        self._took(start, "asks about the power" if agent.smart_board_pending else "")
        return reply, new

    def answer_power(self, agent, history, yes):
        """The student's Yes or No to "Turn the power back on?"."""
        waiting = self.paused(agent)
        if waiting is None:
            raise ValueError("nothing is waiting for an answer")
        if waiting is not agent or getattr(agent, "smart_board_offline", True):
            # The offline tutor asked: it finishes its own reply.
            self.offline = waiting is not agent
            return answer_power(waiting, history, yes)
        self.offline = False
        saved = self._save()
        start = time.monotonic()
        try:
            reply, new = answer_power(agent, history, yes, timeout=self.timeout)
        except BoardError:
            raise
        except Exception as e:
            self._failed(agent, saved, start, e)
            if not yes:
                return "You said no: the power stays off.", []
            # Yes is the student asking for power: the offline tutor turns it
            # on, measures at once and cuts again if it is still not safe.
            return ask(self.offline_agent(direct=True), history, "I fixed it, turn the power on")
        self._took(start, "after your answer")
        return reply, new

    # --- Helpers ---------------------------------------------------------------
    def _save(self):
        return {k: copy.deepcopy(getattr(self.session, k)) for k in STATE}

    def _failed(self, agent, saved, start, error):
        """Claude failed: put the teaching state back and switch to offline."""
        seconds = time.monotonic() - start
        agent.smart_board_pending = None
        for k, v in saved.items():
            setattr(self.session, k, v)
        self.session._save()
        self.offline = True
        self.log(f"[{time.strftime('%H:%M:%S')}] Claude failed after {seconds:.1f} s "
                 f"({why_failed(error, self.timeout)}). The offline tutor answered this "
                 f"message; the next one tries Claude again.")

    def _took(self, start, what=""):
        seconds = time.monotonic() - start
        extra = f" ({what})" if what else ""
        self.log(f"[{time.strftime('%H:%M:%S')}] Claude reply took {seconds:.1f} s{extra}")
