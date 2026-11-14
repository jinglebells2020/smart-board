"""Chat with the tutor in a terminal.

    python -m agent                 # uses the fake board (sim/fake_board.py)
    python -m agent --rows T1-T16 --no-current --no-power-switch
    python -m agent --serial COM3          # a real board (or --serial auto)

Type what a student would say ("check my circuit", "I fixed it").
Commands for testing with the fake board:
    /build <circuit>    pretend the student built this circuit
    /list               show the circuits for the current lesson
    /press <1-4>        press a board button (1 check, 2 hint,
                        3 next step, 4 power off/on)
    /board              show the board's lights and screen
    /quit               stop
"""

import argparse
from pathlib import Path

from agent.build import ask, build_agent, make_model
from agent.buttons import handle_button
from agent.serial_board import BoardError, SerialBoard, choose_port
from agent.session import Session
from protocol import BUTTON_LABELS, DEFAULT_BAUD
from sim.fake_board import SCENARIOS, FakeBoard

DEFAULT_HISTORY = Path(__file__).resolve().parent / "student_history.json"


def show_board(session):
    link = session.board
    lights = ", ".join(f"{r} {c}" for r, c in sorted(link.lights.items())) or "all off"
    print(f"  lights: {lights}")
    if link.has_screen:
        for line in link.screen or ["(blank)"]:
            print(f"  screen | {line}")


def main(argv=None):
    p = argparse.ArgumentParser(description="Smart breadboard tutor")
    p.add_argument("--lesson", type=int, default=1)
    p.add_argument("--scenario", default="correct",
                   help="circuit the fake board starts with")
    p.add_argument("--history", default=str(DEFAULT_HISTORY),
                   help="file that keeps the student's mistake history")
    p.add_argument("--fresh", action="store_true", help="forget the old history")
    p.add_argument("--rows", default="all", help='rows the fake board measures, e.g. "T1-T16"')
    p.add_argument("--no-current", action="store_true", help="no current sensor")
    p.add_argument("--no-power-switch", action="store_true", help="cannot switch power")
    p.add_argument("--no-screen", action="store_true", help="no screen")
    p.add_argument("--serial", metavar="PORT",
                   help='use a real board on this port (COM3, /dev/ttyUSB0) or "auto"')
    p.add_argument("--baud", type=int, default=DEFAULT_BAUD,
                   help=f"serial speed for --serial (default {DEFAULT_BAUD})")
    args = p.parse_args(argv)

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
        board = FakeBoard(args.lesson, args.scenario, rows=args.rows,
                          has_current=not args.no_current,
                          has_power_switch=not args.no_power_switch,
                          has_screen=not args.no_screen)
    fake = isinstance(board, FakeBoard)
    session = Session(board, lesson=args.lesson, history_path=args.history)
    model, is_real = make_model()
    agent = build_agent(session, model)
    caps = session.board.capabilities()

    print("Smart breadboard tutor (" + ("fake board" if fake else f"real board on {board.port}") + ")")
    print(f"Board: {caps['rows']} rows measured, current sensor "
          f"{'yes' if caps['has_current'] else 'no'}, power switch "
          f"{'yes' if caps['has_power_switch'] else 'no'}, screen "
          f"{'yes' if caps['has_screen'] else 'no'}")
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

    def tutor(text):
        lesson_before = session.lesson_id
        try:
            reply, _ = ask(agent, history, text)
        except BoardError as e:
            print(f"board> {e}")
            return str(e)
        print(f"tutor> {reply}")
        if session.lesson_id != lesson_before and fake:
            # New lesson: the pretend student starts it with a correct build.
            board.send(f"SCENARIO {session.lesson_id} correct")
            print(f"(fake board now shows lesson {session.lesson_id}, circuit "
                  f"'correct'. Use /build to try a mistake.)")
        return reply

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
        if text == "/help":
            print(__doc__)
            continue
        if text == "/list":
            print(", ".join(SCENARIOS[session.lesson_id]))
            continue
        if text == "/board":
            show_board(session)
            continue
        if (text.startswith("/build") or text.startswith("/press")) and not fake:
            print("board> /build and /press are for the fake board. Use the real "
                  "board's buttons.")
            continue
        if text.startswith("/build"):
            parts = text.split()
            if len(parts) != 2:
                print("usage: /build <circuit>   (see /list)")
                continue
            print("board>", board.send(f"SCENARIO {session.lesson_id} {parts[1]}"))
            continue
        if text.startswith("/press"):
            parts = text.split()
            answer = board.send(f"PRESS {parts[1] if len(parts) == 2 else ''}".strip())
            if answer.get("type") == "error":
                print("board>", answer["msg"])
                continue
            for ev in session.board.events():
                print(f"(button {ev['id']}: {BUTTON_LABELS.get(ev['id'], '?')})")
                result = handle_button(ev["id"], session, tutor)
                if result.get("action") == "next_step" and not result.get("done"):
                    legs = ", ".join(f"{l['leg']} in {l['row']}" for l in result["legs"])
                    print(f"step> {result['step']} of {result['of']}: {result['part']} ({legs})")
                elif "say" in result:
                    print(f"board> {result['say']}")
            show_board(session)
            print()
            continue

        tutor(text)
        show_board(session)
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
