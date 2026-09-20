from flask import Flask, request, render_template_string
from openai import OpenAI
import os
import base64
import json
from datetime import datetime

app = Flask(__name__)
client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

RULES_FILE = "learned_codes.json"
CORRECTIONS_FILE = "corrections.json"


# ============================================================
# DEFAULT CODES
# ============================================================

DEFAULT_CODES = {
    "C": "White Coffee",
    "BC": "Black Coffee",
    "L": "Latte",
    "Can": "Can Drink",
    "Bottle": "Bottle Drink",

    "E": "Egg",
    "PE": "Poached Egg",
    "SE": "Scrambled Egg",
    "B": "Bacon",
    "S": "Sausage",
    "BB": "Baked Beans",
    "Bubble": "Bubble"
}


# ============================================================
# LOAD / SAVE CODES
# ============================================================

def load_codes():

    codes = DEFAULT_CODES.copy()

    try:
        with open(RULES_FILE, "r") as f:
            learned = json.load(f)

            if isinstance(learned, dict):
                codes.update(learned)

    except (FileNotFoundError, json.JSONDecodeError):
        pass

    return codes


def save_code(code, meaning):

    learned = {}

    try:
        with open(RULES_FILE, "r") as f:
            learned = json.load(f)

            if not isinstance(learned, dict):
                learned = {}

    except (FileNotFoundError, json.JSONDecodeError):
        pass

    learned[code] = meaning

    with open(RULES_FILE, "w") as f:
        json.dump(
            learned,
            f,
            indent=2,
            ensure_ascii=False
        )


# ============================================================
# CORRECTIONS
# ============================================================

def load_corrections():

    try:
        with open(CORRECTIONS_FILE, "r") as f:
            data = json.load(f)

            if isinstance(data, list):
                return data

    except (FileNotFoundError, json.JSONDecodeError):
        pass

    return []


def save_correction(original, corrected):

    corrections = load_corrections()

    original = original.strip()
    corrected = corrected.strip()

    for correction in corrections:

        old_original = correction.get(
            "original",
            ""
        ).strip()

        old_corrected = correction.get(
            "corrected",
            ""
        ).strip()

        if (
            old_original == original
            and old_corrected == corrected
        ):

            correction["times_seen"] = (
                correction.get("times_seen", 1) + 1
            )

            correction["last_seen"] = (
                datetime.now().isoformat(
                    timespec="seconds"
                )
            )

            with open(CORRECTIONS_FILE, "w") as f:
                json.dump(
                    corrections,
                    f,
                    indent=2,
                    ensure_ascii=False
                )

            return

    corrections.append({
        "original": original,
        "corrected": corrected,
        "times_seen": 1,
        "last_seen": datetime.now().isoformat(
            timespec="seconds"
        )
    })

    corrections = corrections[-100:]

    with open(CORRECTIONS_FILE, "w") as f:
        json.dump(
            corrections,
            f,
            indent=2,
            ensure_ascii=False
        )


def corrections_for_prompt():

    corrections = load_corrections()

    if not corrections:
        return "No previous corrections."

    recent = corrections[-20:]

    blocks = []

    for number, correction in enumerate(
        recent,
        start=1
    ):

        block = (
            "\nCORRECTION EXAMPLE "
            + str(number)
            + "\n\nAI READ:\n"
            + correction.get("original", "")
            + "\n\nHUMAN CORRECTED TO:\n"
            + correction.get("corrected", "")
            + "\n"
        )

        blocks.append(block)

    return "\n".join(blocks)


# ============================================================
# UNKNOWN CODES
# ============================================================

def get_unknowns(result):

    codes = load_codes()

    unknowns = []

    lines = result.splitlines()

    inside_unknown = False

    for line in lines:

        stripped = line.strip()

        if stripped.upper() == "UNKNOWN:":
            inside_unknown = True
            continue

        if (
            inside_unknown
            and stripped.endswith(":")
        ):
            inside_unknown = False
            continue

        if not inside_unknown:
            continue

        if not stripped:
            continue

        if stripped.lower() in [
            "none",
            "n/a",
            "unknown"
        ]:
            continue

        code = stripped.lstrip("-• ").strip()

        if not code:
            continue

        if code in codes:
            continue

        if code not in unknowns:
            unknowns.append(code)

    return unknowns


# ============================================================
# UPDATE CURRENT ORDER AFTER LEARNING
# ============================================================

def update_order_after_learning(
    current_order,
    code,
    meaning
):

    if not current_order:
        return ""

    lines = current_order.splitlines()

    new_lines = []

    inside_unknown = False

    for line in lines:

        stripped = line.strip()

        if stripped.upper() == "UNKNOWN:":

            inside_unknown = True
            new_lines.append(line)
            continue

        if (
            inside_unknown
            and stripped.endswith(":")
        ):

            inside_unknown = False

        if inside_unknown:

            clean = stripped.lstrip("-• ").strip()

            if clean.lower() == code.lower():
                continue

            new_lines.append(line)
            continue

        # Replace known code in normal order text.

        words = line.split()

        changed_words = []

        for word in words:

            prefix = ""
            suffix = ""
            core = word

            # Keep numbering such as 2-
            if core.endswith("-"):
                changed_words.append(core)
                continue

            if core == code:
                core = meaning

            changed_words.append(core)

        rebuilt = " ".join(changed_words)

        # Handle modification:
        # No E -> B
        # No
