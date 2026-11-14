# web/

The one-page screen for the projector, made for an 11-year-old: Zip
(our robot spark guide, 4 moods) says the tutor's words, a drawing of
the lesson's breadboard shows each part and the live row lights and
voltages, a lesson path shows stars, the mini-lesson card has its quiz,
and a correct circuit gets a short celebration. Practice mode, board
status, the tool trace and the mistake history are in the **Teacher** drawer
(closed when the page opens).

What the server adds for the page (worked out, no new rules): the
lesson layout, `path` (stars per lesson) and `mood` (happy, thinking,
curious or careful) in `/api/state`, and `mood` and `celebrate` on each
tutor message.

- `index.html` : the page (one file, no internet needed).
- `server.py`  : a small local server the page talks to. It runs the
  fake board and the agent. Only Python's built-in libraries.

Start it from the main folder:

    python web/server.py --fresh

then open http://localhost:8000. Step-by-step Windows instructions:
RUN.md, Part E.

## Pretend board

With the fake board, the page shows a **pretend board**: the board's
small screen with its current text, and its 4 buttons
(1 check my circuit, 2 I need a hint, 3 next step, 4 power off/on).
Clicking a button presses it on the fake board, so it goes exactly the
way a real button press will. Button 3 builds the lesson step by step:
the next part's rows light blue and the screen names the part.

The drawing ("The plan: where each part goes") shows each row the lesson uses with its
live voltage, "empty" when nothing is connected to it, and its light in
its real colour. During step-by-step building the current part is
outlined in blue and later parts are faint.

## Try different boards

The real board's details are not decided yet, so the server can start
the fake board with any of them:

    python web/server.py --fresh --no-current          # no current sensor
    python web/server.py --fresh --no-power-switch     # cannot switch power
    python web/server.py --fresh --rows T1-T16         # only 16 rows measured
    python web/server.py --fresh --no-screen           # no screen

They can be combined. The Teacher drawer says what the board has.

## A real board

    python web/server.py --fresh --serial COM3     # or --serial auto

With a real board the practice-mode buttons and the pretend board's
buttons are hidden (the student uses the real buttons), and a line in
the Teacher drawer says **Board connected on COM3 (115200 baud)**, or in red
**Board not answering on COM3** with what to do. While it is not
answering, the numbers show "?" instead of old or made-up values. The
page reconnects by itself when the cable is plugged back in. Windows
steps: RUN.md, Part F.

The page fits one 1920x1080 screen without scrolling (checked for every
circuit, first and second try, with and without those options).
Press F11 in the browser for full screen. It also works on a phone.
