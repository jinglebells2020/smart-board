"""Chat with the tutor in a terminal.

    python -m agent                 # uses the fake board (sim/fake_board.py)
    python -m agent --rows T1-T16 --no-current --no-power-switch
    python -m agent --serial COM3          # a real board (or --serial auto)
    python -m agent --supply 5             # fake board on another supply (default 3.3 V)

Type what a student would say ("check my circuit", "I fixed it").
With --serial, the real board's buttons work too, even while the terminal
waits for you to type (a background thread asks the board for button
presses about 4 times a second).
Commands for testing with the fake board:
    /build <circuit>    pretend the student built this circuit
    /list               show the circuits for the current lesson
    /press <1-4>        press a board button (1 yellow: check, 2 white:
                        hint, 3 blue: next step, 4 red: power off/on)
    /board              show the board's lights and screen
    /notebook           show the student's notebook
    /answer <1-3>       answer lesson 0's question (which holes are joined)
    /quit               stop
"""

import argparse
import threading
from pathlib import Path

from agent.build import build_agent, make_model
from agent.fallback import OFFLINE_NOTE, SafeReplies
from agent.buttons import handle_button
from agent.notebook import Notebook, safe_name
from agent.serial_board import BoardError, SerialBoard, choose_port
from agent.session import UNMAPPED_SAY, Session
from protocol import BUTTON_LABELS, DEFAULT_BAUD, SUPPLY_V
from sim.fake_board import SCENARIOS, FakeBoard

DEFAULT_HISTORY = Path(__file__).resolve().parent / "student_history.json"
NOTEBOOKS = Path(__file__).resolve().parent.parent / "notebooks"


class ButtonPoller:
    """The real board's buttons, while the terminal waits for typing.

    A background thread asks session.board.events() about every `every`
    seconds and hands each button press to on_button (the same handler as
    /press). `lock` is held while a press is handled, and the main loop
    holds it while it handles a typed line, so two tutor turns never run at
    once (a press during a reply waits, then runs)."""

    def __init__(self, session, on_button, lock, every=0.25):
        self.session = session
        self.on_button = on_button
        self.lock = lock
        self.every = every
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self._thread.start()
        return self

    def stop(self):
        self._stop.set()
        self._thread.join(timeout=2)

    def _run(self):
        while not self._stop.wait(self.every):
            self.poll_once()

    def poll_once(self):
        """Handle the presses waiting now. Returns their button ids."""
        with self.lock:
            try:
                events = self.session.board.events()  # never raises
            except BoardError:
                return []
            ids = [ev["id"] for ev in events if ev.get("type", "button") == "button"]
            for button_id in ids:
                self.on_button(button_id)
            return ids


def show_board(session):
    link = session.board
    lights = ", ".join(f"{r} {c}" for r, c in sorted(link.lights.items())) or "all off"
    print(f"  lights: {lights}")
    if link.has_screen:
        for line in link.screen or ["(blank)"]:
            print(f"  screen | {line}")


def main(argv=None):
    p = argparse.ArgumentParser(description="Genesis tutor")
    p.add_argument("--lesson", type=int, default=None,
                   help="lesson to start at (default: lesson 0 for a new student)")
    p.add_argument("--scenario", default="correct",
                   help="circuit the fake board starts with")
    p.add_argument("--history", default=None,
                   help="file that keeps the student's mistake history")
    p.add_argument("--student", default="student",
                   help="the student's name: their notebook is notebooks/<name>.md")
    p.add_argument("--notebooks", default=str(NOTEBOOKS), help="folder for the notebooks")
    p.add_argument("--fresh", action="store_true", help="forget the old history")
    p.add_argument("--rows", default="all", help='rows the fake board measures, e.g. "T1-T16"')
    p.add_argument("--no-current", action="store_true", help="no current sensor")
    p.add_argument("--no-power-switch", action="store_true", help="cannot switch power")
    p.add_argument("--no-screen", action="store_true", help="no screen")
    p.add_argument("--supply", type=float, default=SUPPLY_V,
                   help=f"fake board supply voltage (default {SUPPLY_V:g} V, like the station)")
    p.add_argument("--serial", metavar="PORT",
                   help='use a real board on this port (COM3, /dev/ttyUSB0) or "auto"')
    p.add_argument("--baud", type=int, default=DEFAULT_BAUD,
                   help=f"serial speed for --serial (default {DEFAULT_BAUD})")
    args = p.parse_args(argv)
    if args.history is None:
        name = safe_name(args.student)
        args.history = str(DEFAULT_HISTORY if name == "student"
                           else DEFAULT_HISTORY.with_name(f"history_{name}.json"))

    if args.fresh and Path(args.history).exists():
        Path(args.history).unlink()

    if args.serial:
        try:
            port = choose_port(args.serial)
            print(f"Connecting to the board on {port} at {args.baud} baud...")
            board = SerialBoard(port, args.baud)
        except BoardError as e:
            print(f"Could not use the real board. {e}")
            return 2
    else:
        try:
            board = FakeBoard(args.lesson if args.lesson is not None else 1, args.scenario,
                              rows=args.rows, has_current=not args.no_current,
                              has_power_switch=not args.no_power_switch,
                              has_screen=not args.no_screen, supply_v=args.supply)
        except ValueError as e:
            print(f"Could not start the fake board: {e}")
            return 2
    fake = isinstance(board, FakeBoard)
    notebook = Notebook(args.notebooks, args.student)
    session = Session(board, lesson="start" if args.lesson is None else args.lesson,
                      history_path=args.history, notebook=notebook)
    if fake and session.lesson_id != board.lesson:
        board.send(f"SCENARIO {session.lesson_id} correct")  # lesson 0 for a new student
    model, is_real = make_model()
    agent = build_agent(session, model, ask_before_power=True)
    # Claude's reply, or the offline tutor's for that message when Claude
    # fails, is slow or there is no internet. Prints each Claude reply's time.
    replies = SafeReplies(session, ask_before_power=True, log=print)
    caps = session.board.capabilities()

    print("Genesis tutor (" + ("fake board" if fake else f"real board on {board.port}") + ")")
    print(f"Board: {caps['rows']} rows measured, current sensor "
          f"{'yes' if caps['has_current'] else 'no'}, power switch "
          f"{'yes' if caps['has_power_switch'] else 'no'}, screen "
          f"{'yes' if caps['has_screen'] else 'no'}, supply {caps['supply_v']:g} V")
    if session.board.unmapped:
        print(UNMAPPED_SAY)
    print(f"Notebook for {args.student}: {notebook.path}")
    if is_real:
        print(f"Model: {model}")
    else:
        print("No ANTHROPIC_API_KEY set: using the offline practice tutor.")
    print(f"Lesson {session.lesson_id}: {session.lesson['title']} "
          f"({session.lesson['circuit']})")
    if fake:
        print(f"Fake board circuit: {args.scenario}. Type /help for commands.\n")
    else:
        print("Type what a student would say. /board shows lights and screen, /quit stops.\n")

    history = []

    def show_question():
        """Lesson 0 has no circuit: its question is answered here (on the
        web page it is the Learn card)."""
        if session.is_explore() and not session.intro_answered():
            ml = session.lesson["intro_lesson"]
            print(f"learn> {ml['title']} {ml['idea']}")
            print(f"learn> {ml['question']}")
            for i, option in enumerate(ml["options"], 1):
                print(f"learn>   {i}. {option}")
            print("learn> Type /answer 1, 2 or 3.\n")

    show_question()
    # One tutor turn at a time: typed lines and button presses take turns.
    turn = threading.RLock()

    def answer_pause(text):
        """Zip is paused (asks before power on, or asks for an experiment):
        `text` is the student's answer. Returns Zip's next reply."""
        pending = replies.pending(agent)
        if pending.get("kind") == "experiment":
            exp = pending["experiment"]
            done = text.strip().lower().startswith(exp["done_label"][:1].lower() or "d")
            return replies.answer_experiment(agent, history, done)[0]
        return replies.answer_power(agent, history, text.strip().lower().startswith("y"))[0]

    def pause_choices():
        """What Zip is waiting for: "yes/no", or the experiment's two labels."""
        pending = replies.pending(agent)
        if pending.get("kind") == "experiment":
            exp = pending["experiment"]
            return f"{exp['done_label']} / {exp['cant_label']}"
        return "yes/no"

    def tutor(text, from_button=False, answer=None):
        """One tutor turn. While Zip is paused for an answer, it is read
        here (typing), or, for a turn a board button started (the main
        loop is waiting at you>), left for the next typed line."""
        lesson_before = session.lesson_id
        try:
            reply = answer_pause(answer) if answer is not None else \
                replies.ask(agent, history, text)[0]
            while replies.paused(agent) is not None:
                print(f"tutor> {reply}")
                if from_button:
                    print(f"(type your answer at you>: {pause_choices()})")
                    return reply
                try:
                    typed = input(f"you ({pause_choices()})> ")
                except (EOFError, KeyboardInterrupt):
                    typed = ""
                reply = answer_pause(typed)
        except BoardError as e:
            print(f"board> {e}")
            return str(e)
        print(f"tutor> {reply}")
        if replies.offline:
            print(f"({OFFLINE_NOTE}.)")
        if session.lesson_id != lesson_before and fake:
            # New lesson: the pretend student starts it with a correct build.
            board.send(f"SCENARIO {session.lesson_id} correct")
            print(f"(fake board now shows lesson {session.lesson_id}, circuit "
                  f"'correct'. Use /build to try a mistake.)")
        show_question()
        return reply

    def on_button(button_id, prompt=False):
        """One button press, from /press or from the real board: the same
        handling either way."""
        if prompt:
            print()  # off the "you> " line
        print(f"(button {button_id}: {BUTTON_LABELS.get(button_id, '?')})")
        try:
            result = handle_button(button_id, session,
                                   lambda text: tutor(text, from_button=prompt))
        except BoardError as e:
            print(f"board> {e}")
            result = {}
        if result.get("action") == "next_step" and not result.get("done"):
            legs = ", ".join(f"{l['leg']} in {l['row']}" for l in result["legs"])
            print(f"step> {result['step']} of {result['of']}: {result['part']} ({legs})")
        elif "say" in result:
            print(f"board> {result['say']}")
        show_board(session)
        print()
        if prompt:
            print("you> ", end="", flush=True)  # still waiting for typing
        return result

    def paused_answer(text):
        """A typed line while Zip waits for an answer (a board button's
        turn paused): it is that answer. Returns True when it was used."""
        if replies.paused(agent) is None:
            return False
        tutor(None, answer=text)
        show_board(session)
        print()
        return True

    poller = None
    if not fake:
        # The real board's buttons: handled while the terminal waits for typing.
        poller = ButtonPoller(session, lambda b: on_button(b, prompt=True), turn).start()
    try:
        return _loop(session, board, fake, notebook, turn,
                     lambda text: paused_answer(text) or
                     _command(text, session, board, fake, notebook, tutor, on_button))
    finally:
        if poller is not None:
            poller.stop()


def _loop(session, board, fake, notebook, turn, handle):
    """Read typed lines until /quit. Each line is handled holding `turn`."""
    while True:
        try:
            text = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not text:
            continue
        if text in ("/quit", "/exit"):
            break
        with turn:
            handle(text)
    return 0


def _command(text, session, board, fake, notebook, tutor, on_button):
    """One typed line: a /command, or what the student says to the tutor."""
    if text == "/help":
        print(__doc__)
        return
    if text == "/list":
        print(", ".join(SCENARIOS[session.lesson_id]))
        return
    if text == "/notebook":
        print(notebook.text())
        return
    if text == "/board":
        show_board(session)
        return
    if text.startswith("/answer"):
        parts = text.split()
        if not session.is_explore():
            print("learn> /answer is for lesson 0's question. Quiz questions after "
                  "a mistake are on the web page.")
            return
        if len(parts) != 2 or not parts[1].isdigit():
            print("usage: /answer <1-3>")
            return
        out = session.record_quiz(session.lesson_id, "intro", int(parts[1]) - 1, "intro")
        if "error" in out:
            print("learn> Type /answer 1, 2 or 3.")
            return
        print(f"learn> {'Right!' if out['correct'] else 'Not quite.'} {out['why']}")
        print("learn> Now type: check my circuit\n")
        return
    if (text.startswith("/build") or text.startswith("/press")) and not fake:
        print("board> /build and /press are for the fake board. On the real board, "
              "press its buttons: they work while this terminal waits for you.")
        return
    if text.startswith("/build"):
        parts = text.split()
        if len(parts) != 2:
            print("usage: /build <circuit>   (see /list)")
            return
        print("board>", board.send(f"SCENARIO {session.lesson_id} {parts[1]}"))
        return
    if text.startswith("/press"):
        parts = text.split()
        answer = board.send(f"PRESS {parts[1] if len(parts) == 2 else ''}".strip())
        if answer.get("type") == "error":
            print("board>", answer["msg"])
            return
        presses = session.board.events()
        for ev in presses:
            on_button(ev["id"])
        if not presses:
            show_board(session)
            print()
        return

    tutor(text)
    show_board(session)
    print()


if __name__ == "__main__":
    raise SystemExit(main())
