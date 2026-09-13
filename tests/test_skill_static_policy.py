"""AST-based static policy checks for candidate skill source, per skill_contract.md.

These checks only parse source text. They never import or execute it.
"""

from __future__ import annotations

import pytest

from noob_agent.skills import check_static_policy

VALID_SOURCE = '''"""Locate the lantern and confirm it is lit."""

from __future__ import annotations


async def run(context, inputs):
    await context.observe()
    result = await context.call("use_object", object_id=inputs["object_id"])
    return {
        "status": "succeeded" if result.state_changed else "failed",
        "summary": "Used the object.",
        "evidence": [],
        "outputs": {},
        "primitive_actions_used": 1,
    }
'''


def test_accepts_a_policy_compliant_source() -> None:
    assert check_static_policy(VALID_SOURCE) == []


@pytest.mark.parametrize(
    "module",
    ["os", "subprocess", "socket", "threading", "importlib", "ctypes", "pickle", "shutil"],
)
def test_rejects_a_forbidden_import(module: str) -> None:
    issues = check_static_policy(f"import {module}\n")

    assert len(issues) == 1
    assert issues[0].check == "static_policy"
    assert issues[0].code == "forbidden_import"


def test_rejects_a_forbidden_import_from_form() -> None:
    issues = check_static_policy("from os import environ\n")

    assert len(issues) == 1
    assert issues[0].code == "forbidden_import"


def test_rejects_private_member_import_from_allowed_module() -> None:
    issues = check_static_policy("from dataclasses import _create_fn\n")

    assert len(issues) == 1
    assert issues[0].code == "forbidden_import"


def test_rejects_file_access_via_the_open_builtin() -> None:
    source = 'async def run(context, inputs):\n    handle = open("secret.txt")\n'

    issues = check_static_policy(source)

    assert any(issue.code == "forbidden_call" for issue in issues)


def test_rejects_aliased_forbidden_builtins() -> None:
    source = (
        "async def run(context, inputs):\n"
        "    opener = open\n"
        "    opener('secret.txt')\n"
        "    imp = __import__\n"
        "    imp('os')\n"
    )

    issues = check_static_policy(source)

    assert sum(issue.code == "forbidden_call" for issue in issues) == 2


@pytest.mark.parametrize("call", ["eval('1')", "exec('pass')", "compile('1', '<s>', 'eval')"])
def test_rejects_dynamic_execution(call: str) -> None:
    issues = check_static_policy(f"async def run(context, inputs):\n    {call}\n")

    assert any(issue.code == "forbidden_call" for issue in issues)


def test_rejects_reflection_via_dunder_attribute_access() -> None:
    source = "async def run(context, inputs):\n    return ().__class__\n"

    issues = check_static_policy(source)

    assert any(issue.code == "forbidden_reflection" for issue in issues)


def test_rejects_source_that_does_not_parse() -> None:
    issues = check_static_policy("def run(:\n")

    assert len(issues) == 1
    assert issues[0].code == "syntax_error"
