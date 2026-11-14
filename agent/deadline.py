"""Give up on a reply that takes too long.

run_with_timeout() runs one reply in a helper thread and waits at most
`timeout` seconds. If the reply is not done by then, it is marked as
given up and ReplyTimeout is raised, so the offline tutor can answer.

The given-up reply may still be running in its thread (a network call
cannot be stopped from outside). check_run() is called before every
model call and tool call (agent/trace.py), so that run stops at its
next step and never touches the board or the history again.
"""

import contextvars
import threading

RUN = contextvars.ContextVar("smart_board_run", default=None)


class ReplyTimeout(Exception):
    """Claude did not answer in time."""


class RunAbandoned(Exception):
    """This reply was given up on; it must do nothing more."""


def check_run():
    run = RUN.get()
    if run is not None and run["abandoned"]:
        raise RunAbandoned("this reply was given up on")


def run_with_timeout(fn, timeout):
    """fn() with a time limit in seconds (None: no limit)."""
    if timeout is None:
        return fn()
    run = {"abandoned": False}
    box = {}

    def work():
        RUN.set(run)
        try:
            box["value"] = fn()
        except BaseException as e:  # handed back to the caller below
            box["error"] = e

    thread = threading.Thread(target=contextvars.copy_context().run, args=(work,),
                              daemon=True, name="claude-reply")
    thread.start()
    thread.join(timeout)
    if thread.is_alive():
        run["abandoned"] = True
        raise ReplyTimeout(f"no answer within {timeout:g} seconds")
    if "error" in box:
        raise box["error"]
    return box["value"]
