from functools import cache
from pathlib import Path
from string import Template


@cache
def _template(name):
    return Template((Path(__file__).parents[1] / "resources/prompts" / name).read_text())


def load_prompt(name, **values):
    return _template(name).substitute(values)
