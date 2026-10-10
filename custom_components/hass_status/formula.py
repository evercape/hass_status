"""Boolean formulas over condition ids.

The language is identifiers plus ``and``, ``or``, ``not``, and parentheses.
It is intentionally not Python ``eval``.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from functools import lru_cache
import re
from typing import Protocol


class FormulaError(Exception):
    """A formula could not be parsed or evaluated."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(detail or code)


class _Node(Protocol):
    def eval(self, env: Mapping[str, bool]) -> bool:
        """Evaluate this node."""

    def names(self) -> set[str]:
        """Return referenced condition ids."""


class _Name:
    def __init__(self, name: str) -> None:
        self.name = name

    def eval(self, env: Mapping[str, bool]) -> bool:
        try:
            return bool(env[self.name])
        except KeyError as err:
            raise FormulaError("unknown_name", self.name) from err

    def names(self) -> set[str]:
        return {self.name}


class _Not:
    def __init__(self, child: _Node) -> None:
        self.child = child

    def eval(self, env: Mapping[str, bool]) -> bool:
        return not self.child.eval(env)

    def names(self) -> set[str]:
        return self.child.names()


class _And:
    def __init__(self, left: _Node, right: _Node) -> None:
        self.left = left
        self.right = right

    def eval(self, env: Mapping[str, bool]) -> bool:
        return self.left.eval(env) and self.right.eval(env)

    def names(self) -> set[str]:
        return self.left.names() | self.right.names()


class _Or:
    def __init__(self, left: _Node, right: _Node) -> None:
        self.left = left
        self.right = right

    def eval(self, env: Mapping[str, bool]) -> bool:
        return self.left.eval(env) or self.right.eval(env)

    def names(self) -> set[str]:
        return self.left.names() | self.right.names()


_TOKEN = re.compile(r"\s*(?:(and|or|not)\b|([A-Za-z_][A-Za-z0-9_]*)|([()]))", re.IGNORECASE)


def _tokenize(source: str) -> list[tuple[str, str]]:
    tokens: list[tuple[str, str]] = []
    position = 0
    while position < len(source):
        if source[position].isspace():
            position += 1
            continue
        match = _TOKEN.match(source, position)
        if match is None:
            raise FormulaError("invalid_formula", source[position:])
        position = match.end()
        keyword, name, paren = match.groups()
        if keyword:
            tokens.append(("op", keyword.lower()))
        elif name:
            tokens.append(("name", name.lower()))
        else:
            tokens.append(("paren", paren))
    return tokens


class _Parser:
    def __init__(self, tokens: list[tuple[str, str]]) -> None:
        self._tokens = tokens
        self._index = 0

    def parse(self) -> _Node:
        if not self._tokens:
            raise FormulaError("invalid_formula", "empty")
        node = self._parse_or()
        if self._index != len(self._tokens):
            raise FormulaError("invalid_formula", self._tokens[self._index][1])
        return node

    def _peek(self) -> tuple[str, str] | None:
        if self._index >= len(self._tokens):
            return None
        return self._tokens[self._index]

    def _parse_or(self) -> _Node:
        node = self._parse_and()
        while (token := self._peek()) is not None and token == ("op", "or"):
            self._index += 1
            node = _Or(node, self._parse_and())
        return node

    def _parse_and(self) -> _Node:
        node = self._parse_not()
        while (token := self._peek()) is not None and token == ("op", "and"):
            self._index += 1
            node = _And(node, self._parse_not())
        return node

    def _parse_not(self) -> _Node:
        token = self._peek()
        if token == ("op", "not"):
            self._index += 1
            return _Not(self._parse_not())
        return self._parse_primary()

    def _parse_primary(self) -> _Node:
        token = self._peek()
        if token is None:
            raise FormulaError("invalid_formula", "truncated")
        kind, value = token
        self._index += 1
        if kind == "name":
            return _Name(value)
        if kind == "paren" and value == "(":
            node = self._parse_or()
            closing = self._peek()
            if closing != ("paren", ")"):
                raise FormulaError("invalid_formula", "missing )")
            self._index += 1
            return node
        raise FormulaError("invalid_formula", value)


@lru_cache(maxsize=128)
def compile_formula(source: str) -> tuple[Callable[[Mapping[str, bool]], bool], frozenset[str]]:
    """Compile a formula into a function and the set of names it uses."""
    node = _Parser(_tokenize(source.strip())).parse()
    names = frozenset(node.names())

    def _run(env: Mapping[str, bool]) -> bool:
        return node.eval(env)

    return _run, names
