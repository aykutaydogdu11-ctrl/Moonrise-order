from flask import Flask, request, render_template_string
from openai import OpenAI
import os
import base64
import json
import re
from datetime import datetime

app = Flask(__name__)
client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

RULES_FILE = "learned_codes.json"
CORRECTIONS_FILE = "corrections.json"


# ============================================================
# DEFAULT MOONRISE CODES
# ============================================================

DEFAULT_CODES = {
    # DRINKS
    "C": "White Coffee",
    "BC": "Black Coffee",
    "L": "Latte",
    "Can": "Can Drink",
    "Bottle": "Bottle Drink",

    # FOOD
    "E": "Egg",
    "PE": "Poached Egg",
    "SE": "Scrambled Egg",
    "B": "Bacon",
    "S": "Sausage",
    "BB": "Baked Beans",
    "Bubble": "Bubble",

    # Exact combined shorthand
    "S.E.PE": "Sausage, Egg, Poached Egg"
}


# ============================================================
# NORMAL FOOD WORDS
# ============================================================

NORMAL_WORDS = {
    "chips",
    "cheese",
    "toast",
    "brown",
    "white",
    "beans",
    "mushroom",
    "mushrooms",
    "tomato",
    "tomatoes",
    "bread",
    "butter",
    "salad",
    "milk",
    "tea",
    "coffee",
    "water",
    "juice",
    "egg",
    "eggs",
    "bacon",
    "sausage",
    "latte"
}


# ============================================================
# LEARNED CODES
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

            if not
