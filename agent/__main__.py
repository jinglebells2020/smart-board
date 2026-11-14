"""Chat with the tutor in a terminal.

    python -m agent                 # uses the fake board (sim/fake_board.py)

Type what a student would say ("check my circuit", "I fixed it").
Commands for testing with the fake board:
    /build <scenario>   pretend the student built this circuit
    /list               show the scenarios for the current lesson
    /quit               stop
"""

import argparse
from pathlib import Path

from agent.build import ask, build_agent, make_model
from agent.session import Session
from sim.fake_board import SCENARIOS, FakeBoard

DEFAULT_HISTORY = Path(__file__).resolve().parent / "student_history.json"


def main(argv=None):
    p = argparse.ArgumentParser(description="Smart breadboard tutor")
    p.add_argument("--lesson", type=int, default=1)
    p.add_argument("--scenario", default="correct",
                   help="circuit the fake board starts with")
    p.add_argument("--history", default=str(DEFAULT_HISTORY),
                   help="file that keeps the student's mistake history")
    p.add_argument("--fresh", action="store_true", help="forget the old history")
    args = p.parse_args(argv)

    if args.fresh and Path(args.history).exists():
        Path(args.history).unlink()

    board = FakeBoard(args.lesson, args.scenario)
    session = Session(board, lesson=args.lesson, history_path=args.history)
    model, is_real = make_model()
    agent = build_agent(session, model)

    print("Smart breadboard tutor (fake board)")
    if is_real:
        print(f"Model: {model}")
    else:
        print("No ANTHROPIC_API_KEY set: using the offline practice tutor.")
    print(f"Lesson {session.lesson_id}: {session.lesson['title']} "
          f"({session.lesson['circuit']})")
    print(f"Fake board circuit: {args.scenario}. Type /help for commands.\n")

    history = []
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
        if text.startswith("/build"):
            parts = text.split()
            if len(parts) != 2:
                print("usage: /build <scenario>   (see /list)")
                continue
            print("board>", board.send(f"SCENARIO {session.lesson_id} {parts[1]}"))
            continue

        lesson_before = session.lesson_id
        reply, _ = ask(agent, history, text)
        print(f"tutor> {reply}\n")
        if session.lesson_id != lesson_before:
            # New lesson: the pretend student starts it with a correct build.
            board.send(f"SCENARIO {session.lesson_id} correct")
            print(f"(fake board now shows lesson {session.lesson_id}, circuit "
                  f"'correct'. Use /build to try a mistake.)\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
