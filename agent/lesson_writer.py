"""The lesson writer: a deepagents subagent that writes a new mini-lesson,
checked by a grader (deepagents RubricMiddleware) and then by plain Python.

When the same mistake happens a second time, or the student answers its
quiz wrong, the tutor calls it through the task tool. It writes a new
mini-lesson in the same JSON shape as lessons/*.json, with a different
everyday comparison and a new question.

Two checks, in order:
1. RubricMiddleware: after the writer finishes, a grader agent checks the
   lesson against RUBRIC. If it needs revision, the writer gets the
   grader's feedback and tries again (at most twice).
2. Plain Python (check_lesson): the same rules, plus the JSON shape and a
   comparison not used before. Only a lesson that passes is shown, with
   the tag "Written by Zip for you". Otherwise the built-in one stays.
"""

import json
import re

from deepagents.middleware.rubric import RubricMiddleware
from langchain.agents import create_agent
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import RunnableLambda

from agent.trace import TraceMiddleware

RUBRIC = """\
1. The idea has at most 3 short sentences (at most 20 words each).
2. Only words a 6th grader knows: none of probe, anode, cathode, node, polarity, simulator, rail, agent, scenario.
3. The idea never says what to change on the board: none of flip, move, swap, put, check, turn, replace, change, use, plug, remove, add, try, connect it.
4. Exactly 3 options with exactly 1 right answer (answer is 0, 1 or 2).
"""
CRITERIA = ["The idea has at most 3 short sentences",
            "Only words a 6th grader knows",
            "The idea never says what to change on the board",
            "Exactly 3 options with exactly 1 right answer"]
HARD_WORDS = ["probe", "anode", "cathode", "node", "polarity", "simulator", "rail", "agent",
              "scenario"]
FIX_WORDS = ["flip", "move", "swap", "put", "check", "turn", "replace", "change", "use", "plug",
             "remove", "add", "try", "connect it"]
FIELDS = ["title", "idea", "question", "options", "answer", "why", "comparison"]

WRITER_PROMPT = """\
You write one short mini-lesson for a middle school student (age 11 to 14)
who made the same circuit mistake again or answered its quiz wrong.
1. Call get_lesson_facts: the mistake, the old mini-lesson, and the
   everyday comparisons already used.
2. Write a NEW mini-lesson with a DIFFERENT everyday comparison and a new
   question. Answer with only this JSON, nothing else:
   {"title": "...", "idea": "...", "question": "...",
    "options": ["...", "...", "..."], "answer": 0, "why": "...",
    "comparison": "the everyday thing you compared it to"}
Rules: the idea has at most 3 short sentences in 6th grade words, explains
the idea only and never says what to change on the board. Exactly 3
options, exactly 1 right ("answer" is its number: 0, 1 or 2).
"""

WRITER_DESCRIPTION = (
    "Lesson writer. Use when log_mistake says new_lesson_wanted (the same mistake a second "
    "time) or when the student answered the quiz wrong and wants it explained another way. "
    "It writes a new mini-lesson with a different everyday comparison; a grader and plain "
    "Python check it. Describe which mistake in one sentence.")


def _sentences(text):
    return [s for s in re.split(r"(?<=[.!?])\s+", str(text).strip()) if s]


def _has_word(text, word):
    return re.search(rf"\b{re.escape(word)}\b", str(text), re.IGNORECASE) is not None


def check_lesson(lesson):
    """The rubric, checked in plain Python: [(criterion, passed, gap)]."""
    if not isinstance(lesson, dict):
        return [(c, False, "No lesson JSON was found.") for c in CRITERIA]
    idea = str(lesson.get("idea", ""))
    sents = _sentences(idea)
    long_ones = [s for s in sents if len(s.split()) > 20]
    out = [(CRITERIA[0], 1 <= len(sents) <= 3 and not long_ones,
            f"The idea has {len(sents)} sentences" + (" and one is too long." if long_ones else "."))]
    texts = [lesson.get(k, "") for k in ("title", "idea", "question", "why")] + list(
        lesson.get("options") or [])
    hard = [w for w in HARD_WORDS if any(_has_word(t, w) for t in texts)]
    longest = max((w for t in texts for w in re.findall(r"[A-Za-z]+", str(t))), key=len, default="")
    out.append((CRITERIA[1], not hard and len(longest) <= 14,
                f"Hard words: {', '.join(hard) or longest}."))
    fixes = [w for w in FIX_WORDS if _has_word(idea, w)]
    out.append((CRITERIA[2], not fixes, f"The idea says what to do: {', '.join(fixes)}."))
    options = lesson.get("options")
    ok = (isinstance(options, list) and len(options) == 3
          and all(isinstance(o, str) and o.strip() for o in options)
          and len({o.strip().lower() for o in options}) == 3
          and isinstance(lesson.get("answer"), int) and not isinstance(lesson.get("answer"), bool)
          and 0 <= lesson["answer"] <= 2)
    out.append((CRITERIA[3], ok, "It needs exactly 3 different options and an answer of 0, 1 or 2."))
    return out


def validate_lesson(lesson, used_comparisons=()):
    """Plain Python's final word: (ok, problems). The rubric, the JSON shape
    and a comparison that was not used before."""
    problems = [gap for _, passed, gap in check_lesson(lesson) if not passed]
    if isinstance(lesson, dict):
        missing = [k for k in FIELDS if k not in lesson or lesson[k] in ("", None)]
        if missing:
            problems.append(f"Missing: {', '.join(missing)}.")
        comparison = str(lesson.get("comparison", "")).strip().lower()
        if comparison and comparison in {str(c).strip().lower() for c in used_comparisons}:
            problems.append(f"The comparison {comparison!r} was used before.")
    return not problems, problems


def parse_lesson(text):
    """The lesson JSON in the writer's answer, or None."""
    match = re.search(r"\{.*\}", str(text), re.S)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _text(msg):
    content = msg.content
    if isinstance(content, list):
        return "".join(b.get("text", "") for b in content if isinstance(b, dict))
    return str(content)


def lesson_writer_spec(session, writer_model, grader_model):
    """The subagent for create_deep_agent(subagents=[...]): a CompiledSubAgent
    whose runnable runs the writer agent with the rubric set, then checks
    the result in plain Python and saves it only if it passes."""

    def get_lesson_facts() -> str:
        """The mistake, its old mini-lesson, why a new one is wanted, and the
        everyday comparisons already used (do not use them again)."""
        return json.dumps(session.lesson_writer_facts())

    def on_evaluation(evaluation):
        session.trace.append({"who": "grader", "tool": "grade",
                              "args": {"result": evaluation["result"]}})
        grades.append(evaluation["result"])

    grades = []
    writer = create_agent(
        writer_model, tools=[get_lesson_facts], system_prompt=WRITER_PROMPT,
        middleware=[TraceMiddleware(session, "lesson writer"),
                    RubricMiddleware(model=grader_model, max_iterations=2,
                                     on_evaluation=on_evaluation)])

    def run(state, config=None):
        grades.clear()
        result = writer.invoke({"messages": state["messages"], "rubric": RUBRIC}, config)
        final = next((_text(m) for m in reversed(result["messages"])
                      if isinstance(m, AIMessage) and _text(m).strip()), "")
        lesson = parse_lesson(final)
        ok, problems = validate_lesson(lesson, session.comparisons_used())
        graded_ok = bool(grades) and grades[-1] == "satisfied"
        if ok and graded_ok:
            session.accept_ai_lesson(lesson)
            summary = {"accepted": True, "title": lesson["title"],
                       "comparison": lesson["comparison"]}
        else:
            reasons = problems or ["the grader did not accept it"]
            summary = {"accepted": False, "kept": "the built-in mini-lesson",
                       "reason": " ".join(reasons)}
        return {"messages": [AIMessage(content=json.dumps(summary))]}

    return {"name": "lesson_writer", "description": WRITER_DESCRIPTION,
            "runnable": RunnableLambda(run)}


# --- Offline stand-ins for Claude ------------------------------------------------

# What the scripted writer writes: a different everyday comparison for every
# mistake in the lessons (the built-in ones use other comparisons).
ALT_LESSONS = {
    "led_backwards": {
        "title": "LEDs work like a bike tire valve",
        "idea": "An LED is like the valve on a bike tire. Air goes in, but it cannot come "
                "back out. Current through an LED also goes only one way.",
        "question": "Which way can current go through an LED?",
        "options": ["Only one way", "Both ways the same", "No way at all"], "answer": 0,
        "why": "An LED lets current pass in one direction only, like a bike tire valve.",
        "comparison": "a bike tire valve"},
    "no_resistor": {
        "title": "A resistor is a faucet opened a little",
        "idea": "A resistor is like a faucet that is open only a little. It lets a small, safe "
                "stream through. With no resistor, current gushes like a fire hose.",
        "question": "What happens to an LED with no resistor?",
        "options": ["Too much current can burn it out", "It glows a soft color",
                    "Nothing is different"], "answer": 0,
        "why": "Without a resistor nothing holds the current back, so far too much flows.",
        "comparison": "a faucet"},
    "open_circuit": {
        "title": "A circle of hands passes the squeeze",
        "idea": "Current is like a ring of kids holding hands and passing a squeeze along. If "
                "one pair lets go, the squeeze stops for everyone. Every link of the loop counts.",
        "question": "A loop has one small gap. What does the current do?",
        "options": ["It jumps across the gap", "It stops all around the loop",
                    "It flows half the way"], "answer": 1,
        "why": "Current needs the whole loop, like the squeeze needs every hand.",
        "comparison": "a ring of kids holding hands"},
    "resistor_too_big": {
        "title": "A thin straw lets less through",
        "idea": "A resistor is like a drinking straw. A thin straw lets less juice through each "
                "second than a wide one. A bigger resistor is a thinner straw.",
        "question": "A circuit gets a much bigger resistor. What happens to the current?",
        "options": ["It goes down", "It goes up", "It stays the same"], "answer": 0,
        "why": "A bigger resistor holds back more, so less current flows.",
        "comparison": "a drinking straw"},
    "led_bypassed": {
        "title": "Current loves a shortcut",
        "idea": "People walking home love a shortcut across the park. Current is the same: it "
                "picks the easiest way. A wire next to the LED is that shortcut.",
        "question": "A plain wire runs from one LED leg to the other. What happens?",
        "options": ["The LED gets brighter", "Most current skips the LED, so it stays dark",
                    "The wire gets cold"], "answer": 1,
        "why": "The plain wire is the easy path, so the current goes around the LED.",
        "comparison": "a shortcut across a park"},
    "bottom_1k": {
        "title": "Two resistors share like two kids",
        "idea": "Two resistors in a row share the 3.3 volts like two kids sharing marbles. The "
                "bigger resistor gets the bigger handful. Their shares always make 3.3 volts.",
        "question": "One resistor is 10k and the other is 1k. Which one gets more volts?",
        "options": ["The 10k one", "The 1k one", "Both the same"], "answer": 0,
        "why": "The bigger resistor always takes the bigger share of the voltage.",
        "comparison": "sharing marbles"},
    "middle_floating": {
        "title": "A row touching nothing only hears noise",
        "idea": "A measuring row that touches nothing is like a walkie-talkie between channels. "
                "It hears only crackle, not a real voice. The jumping number is that crackle.",
        "question": "A voltage number keeps jumping around. What is the likely reason?",
        "options": ["The measuring row touches nothing", "The resistor is too big",
                    "The LED is too bright"], "answer": 0,
        "why": "A row that touches nothing picks up noise, so its number jumps.",
        "comparison": "a walkie-talkie between channels"},
    "top_1k": {
        "title": "The taller hill takes more of the climb",
        "idea": "Going from 3.3 volts to 0 is like walking over two hills. The taller hill takes "
                "more of the climb. A bigger resistor is the taller hill.",
        "question": "The top resistor is much bigger than the bottom one. What is the middle voltage?",
        "options": ["Lower than half", "Higher than half", "Exactly half"], "answer": 0,
        "why": "The big top resistor takes most of the 3.3 volts, so the middle is low.",
        "comparison": "two hills"},
    "bottom_missing": {
        "title": "A toll booth with no cars takes nothing",
        "idea": "A resistor is like a toll booth on a road. It takes money only from cars that "
                "drive through. With no cars, it takes nothing.",
        "question": "No current flows through a resistor. How much voltage does it take?",
        "options": ["None", "All of it", "Half"], "answer": 0,
        "why": "A resistor lowers the voltage only while current flows through it.",
        "comparison": "a toll booth"},
    "top_missing": {
        "title": "No path to power, no voltage",
        "idea": "A row gets voltage only through a path to the power supply. It is like a lamp "
                "with its cord cut. No path means no power.",
        "question": "A row has a path to ground but none to the power supply. What does it read?",
        "options": ["0 volts", "3.3 volts", "1.65 volts"], "answer": 0,
        "why": "With no path from the power supply, the row sits at ground: 0 volts.",
        "comparison": "a lamp with a cut cord"},
    "loose_led_leg": {
        "title": "A wiggly leg is like a wiggly charger",
        "idea": "A loose leg is like a phone charger that only charges when the cable is held just "
                "right. It touches, lets go, and touches again. So the circuit blinks on and off.",
        "question": "Your circuit works, then stops, then works again. What is the likely reason?",
        "options": ["A leg is loose", "The power is too strong", "The LED is new"], "answer": 0,
        "why": "A leg that wobbles keeps losing touch with the clip inside the hole.",
        "comparison": "a wiggly phone charger"},
    "loose_resistor_leg": {
        "title": "A wiggly leg is like a wiggly charger",
        "idea": "A loose leg is like a phone charger that only charges when the cable is held just "
                "right. It touches, lets go, and touches again. So the circuit blinks on and off.",
        "question": "A number on the board keeps jumping between two values. What is the likely reason?",
        "options": ["A leg is loose", "The resistor is cold", "The board is new"], "answer": 0,
        "why": "A leg that wobbles keeps losing touch with the clip inside the hole.",
        "comparison": "a wiggly phone charger"},
    "button_always_on": {
        "title": "A button is like a door that is stuck open",
        "idea": "A button is like a door that should close when you let go. If the door is stuck "
                "open, anyone can walk through at any time. Current does the same when the button "
                "sides stay joined.",
        "question": "Why does the LED stay on after you let go of the button?",
        "options": ["The sides of the button stay joined", "The LED likes the button",
                    "The wires are too short"], "answer": 0,
        "why": "When the two sides stay joined, the path is never broken, so current keeps flowing.",
        "comparison": "a door stuck open"},
    "button_not_connecting": {
        "title": "A button is like a light switch with no wire behind it",
        "idea": "A light switch only works if it joins two wires behind the wall. If it joins "
                "nothing, you can click it all day and the lamp stays dark. A button that does not "
                "join its sides is the same.",
        "question": "You hold the button down, but no current flows. What is the likely reason?",
        "options": ["The button does not join its two sides", "The LED is too new",
                    "The power is too strong"], "answer": 0,
        "why": "If pressing does not join the two sides, the path still has a gap.",
        "comparison": "a light switch with no wire"},
}

# What the scripted writer writes in "bad" mode: it breaks two rules.
BAD_LESSON = {
    "title": "Fix it now", "idea": "Flip the LED around and try again. You must use a resistor.",
    "question": "What do you do?", "options": ["Flip it", "Swap it", "Turn it", "Move it"],
    "answer": 5, "why": "Because.", "comparison": "nothing"}


def _last_human_start(messages):
    return max(i for i, m in enumerate(messages) if isinstance(m, HumanMessage)
               and not m.additional_kwargs.get("lc_source"))


class ScriptedLessonWriterModel(BaseChatModel):
    """Offline writer: reads the facts, then writes the lesson for that
    mistake from ALT_LESSONS ("good"), or a lesson that breaks the rules
    ("bad"). It writes the same again if the grader asks for a revision."""

    mode: str = "good"

    @property
    def _llm_type(self):
        return "scripted-lesson-writer"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        start = _last_human_start(messages)
        facts = next((json.loads(m.content) for m in messages[start:]
                      if isinstance(m, ToolMessage) and m.name == "get_lesson_facts"), None)
        if facts is None:
            msg = AIMessage(content="", tool_calls=[{"name": "get_lesson_facts", "args": {},
                                                     "id": f"lw-{len(messages)}"}])
        else:
            lesson = ALT_LESSONS.get(facts.get("mistake")) if self.mode == "good" else BAD_LESSON
            msg = AIMessage(content=json.dumps(lesson or BAD_LESSON))
        msg.id = f"lesson-writer-{len(messages)}"
        return ChatResult(generations=[ChatGeneration(message=msg)])


class ScriptedGraderModel(BaseChatModel):
    """Offline grader for RubricMiddleware: finds the lesson JSON in the
    transcript it is given and grades each rubric line with check_lesson."""

    @property
    def _llm_type(self):
        return "scripted-grader"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        payload = "\n".join(_text(m) for m in messages if isinstance(m, HumanMessage))
        lessons = [parse_lesson(chunk) for chunk in re.findall(r"\{[^{}]*\"idea\"[^{}]*\}", payload)]
        lesson = next((x for x in reversed(lessons) if x), None)
        checks = check_lesson(lesson)
        failing = [c for c in checks if not c[1]]
        criteria = [{"name": name, "passed": True} if passed else
                    {"name": name, "passed": False, "gap": gap} for name, passed, gap in checks]
        args = {"result": "needs_revision" if failing else "satisfied",
                "explanation": " ".join(gap for _, _, gap in failing) or "Every rule passes.",
                "criteria": criteria}
        msg = AIMessage(content="", tool_calls=[{"name": "GraderResponse", "args": args,
                                                 "id": f"grade-{len(messages)}"}])
        msg.id = f"grader-{len(messages)}"
        return ChatResult(generations=[ChatGeneration(message=msg)])
