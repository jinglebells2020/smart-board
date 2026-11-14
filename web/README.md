# web/

The one-page screen for the projector: current lesson, live readings,
the tutor's hints, and the mistake history.

- `index.html` : the page (one file, no internet needed).
- `server.py`  : a small local server the page talks to. It runs the
  fake board and the agent. Only Python's built-in libraries.

Start it from the main folder:

    python web/server.py --fresh

then open http://localhost:8000. Step-by-step Windows instructions:
RUN.md, Part E.

The page is laid out to fit one 1920x1080 screen without scrolling.
Press F11 in the browser for full screen. It also works on a phone.
