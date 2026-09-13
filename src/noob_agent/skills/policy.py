"""AST-based static policy checks for candidate skill source.

This only parses source text with `ast.parse`; it never imports, compiles to
bytecode, or executes candidate code. See skill_contract.md's "Permissions"
section for the rules this enforces.
"""

from __future__ import annotations

import ast

from noob_agent.skills.errors import SkillValidationIssue

# A small allowlist, not a blocklist: anything not named here is refused,
# which is how skill_contract.md describes permitted imports ("a small
# allowlist of Python built-ins and pure standard library helpers").
ALLOWED_IMPORT_MODULES = frozenset(
    {
        "__future__",
        "math",
        "statistics",
        "dataclasses",
        "typing",
        "collections",
        "itertools",
        "functools",
        "enum",
    }
)

FORBIDDEN_CALL_NAMES = frozenset(
    {
        "eval",
        "exec",
        "compile",
        "__import__",
        "open",
        "globals",
        "locals",
        "vars",
        "getattr",
        "setattr",
        "delattr",
        "input",
    }
)


def check_static_policy(source: str) -> list[SkillValidationIssue]:
    """Return every forbidden-construct violation found in `source`."""
    try:
        tree = ast.parse(source)
    except SyntaxError as error:
        return [
            SkillValidationIssue(
                check="static_policy",
                code="syntax_error",
                message=str(error.msg),
                line=error.lineno,
                column=error.offset,
            )
        ]

    forbidden_aliases = _collect_forbidden_aliases(tree)
    issues: list[SkillValidationIssue] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root not in ALLOWED_IMPORT_MODULES:
                    issues.append(_forbidden_import(alias.name, node))
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            if node.level or root not in ALLOWED_IMPORT_MODULES:
                issues.append(_forbidden_import(node.module or "", node))
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in FORBIDDEN_CALL_NAMES or node.func.id in forbidden_aliases:
                issues.append(
                    SkillValidationIssue(
                        check="static_policy",
                        code="forbidden_call",
                        message=f"Call to {node.func.id!r} is not permitted.",
                        line=node.lineno,
                        column=node.col_offset,
                    )
                )
        elif (
            isinstance(node, ast.Attribute)
            and node.attr.startswith("__")
            and node.attr.endswith("__")
        ):
            issues.append(
                SkillValidationIssue(
                    check="static_policy",
                    code="forbidden_reflection",
                    message=f"Access to dunder attribute {node.attr!r} is not permitted.",
                    line=node.lineno,
                    column=node.col_offset,
                )
            )

    return issues


def _forbidden_import(name: str, node: ast.Import | ast.ImportFrom) -> SkillValidationIssue:
    return SkillValidationIssue(
        check="static_policy",
        code="forbidden_import",
        message=f"Import of {name!r} is not permitted.",
        line=node.lineno,
        column=node.col_offset,
    )


def _collect_forbidden_aliases(tree: ast.AST) -> set[str]:
    forbidden_refs = set(FORBIDDEN_CALL_NAMES)
    aliases: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Name):
            continue
        if node.value.id not in forbidden_refs:
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                aliases.add(target.id)
                forbidden_refs.add(target.id)
    return aliases
