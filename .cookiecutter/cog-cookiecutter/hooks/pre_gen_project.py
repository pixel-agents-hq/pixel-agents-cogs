"""Validate the cog name before any files are rendered."""

import re
import sys

COG_NAME = "{{ cookiecutter.cog_name }}"

if not re.fullmatch(r"[a-z][a-z0-9_]*", COG_NAME):
    print(
        f"ERROR: cog_name '{COG_NAME}' must be a lowercase snake_case Python "
        "identifier (e.g. 'my_cog')."
    )
    sys.exit(1)
