# web/

The one-page screen for the projector: current lesson, live readings,
the rows the lesson uses with their lights, the tutor's hints, the
mini-lesson card and quiz, and the mistake history.

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

The rows tile shows each row the lesson uses with its live voltage,
"empty" when nothing is connected to it, and the color of its light.

## Try different boards

The real board's details are not decided yet, so the server can start
the fake board with any of them:

    python web/server.py --fresh --no-current          # no current sensor
    python web/server.py --fresh --no-power-switch     # cannot switch power
    python web/server.py --fresh --rows T1-T16         # only 16 rows measured
    python web/server.py --fresh --no-screen           # no screen

They can be combined. The top-left corner of the page says what the
board has.

The page fits one 1920x1080 screen without scrolling (checked for every
circuit, first and second try, with and without those options).
Press F11 in the browser for full screen. It also works on a phone.
