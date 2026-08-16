"""Fill in a freshly rolled Red Config identifier.

Runs with the generated project directory as cwd. The identifier is never
prompted for -- inventing a random-looking int by hand is error-prone and
unnecessary busywork.
"""

import random
from pathlib import Path

target = Path("infrastructure") / "settings_repository.py"
identifier = random.SystemRandom().randint(1_000_000_000, 9_999_999_999)
text = target.read_text(encoding="utf-8")
target.write_text(text.replace("__CONFIG_IDENTIFIER__", str(identifier)), encoding="utf-8")
