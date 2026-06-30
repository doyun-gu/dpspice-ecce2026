"""CLI output-contract tests: --json is pure JSON on stdout, --quiet is calm.

These run the real ``dpspice`` console entry point as a subprocess so they
exercise exactly what a shell pipeline or an agent sees: structured output on
stdout, decoration (banners, spinners, Rich boxes) only ever on stderr. A
machine that does ``dpspice ... --json | jq`` must never get a box-drawing
character or a log line mixed into its JSON.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from contextlib import contextmanager

import pytest

from dpspice.examples import example_path

# Unicode box-drawing / panel glyphs Rich uses for tables and panels. None of
# these may appear on stdout under --json or --quiet.
_BOX_CHARS = set("─━│┃┌┐└┘├┤┬┴┼╭╮╰╯═║╔╗╚╝▏▕")


def _dpspice():
    exe = shutil.which("dpspice")
    if exe is None:
        pytest.skip("dpspice console script not installed on PATH")
    return exe


@contextmanager
def _rectifier_refs():
    """Yield (netlist_path, raw_path) for the bundled rectifier case."""
    with example_path("rectifier_halfwave.sp") as sp, \
            example_path("rectifier_halfwave.raw") as raw:
        yield sp, raw


def _run(args):
    """Run ``dpspice <args>`` and return (returncode, stdout, stderr)."""
    proc = subprocess.run(
        [_dpspice(), *args],
        capture_output=True, text=True, timeout=300,
    )
    return proc.returncode, proc.stdout, proc.stderr


def _json_commands():
    """Every command README advertises as accepting --json, with real args."""
    with _rectifier_refs() as (sp, raw):
        return [
            (["info", sp, "--json"]),
            (["run", sp, "--json"]),
            (["bench", "--json"]),
            (["reproduce", "--json"]),
            (["reproduce", "--table", "3", "--json"]),
            (["reproduce", "--table", "4", "--json"]),
            (["reproduce", "--figure", "5", "--json"]),
            (["validate", sp, "--ref", raw, "--json"]),
            (["suite", "--quick", "--json"]),
            (["suite", "--self-check", "--json"]),
        ]


@pytest.mark.parametrize("args", _json_commands())
def test_json_is_pure_json_on_stdout(args):
    code, stdout, stderr = _run(args)
    assert code == 0, f"{args} exited {code}; stderr={stderr[:400]}"
    # stdout must parse as JSON, whole and entire — no banner, no log line.
    try:
        json.loads(stdout)
    except json.JSONDecodeError as exc:  # pragma: no cover - failure path
        pytest.fail(f"{args} stdout not valid JSON: {exc}\n--- stdout ---\n{stdout[:600]}")
    # and no decoration leaked onto stdout
    assert not (_BOX_CHARS & set(stdout)), f"{args} leaked box chars onto stdout"


@pytest.mark.parametrize("args", [
    ["info", "--quiet"],
    ["run", "--quiet"],
    ["bench", "--quiet"],
    ["reproduce", "--quiet"],
    ["validate", "--quiet"],
    ["suite", "--quick", "--quiet"],
])
def test_quiet_has_no_box_chrome(args):
    # Fill in the netlist/ref placeholders that some commands need.
    with _rectifier_refs() as (sp, raw):
        cmd = list(args)
        if cmd[0] in {"info", "run"}:
            cmd.insert(1, sp)
        elif cmd[0] == "validate":
            cmd[1:1] = [sp, "--ref", raw]
        code, stdout, stderr = _run(cmd)
    assert code == 0, f"{cmd} exited {code}; stderr={stderr[:400]}"
    assert not (_BOX_CHARS & set(stdout)), f"{cmd} emitted Rich box chrome under --quiet"


def test_reproduce_no_args_json_lists_catalogue():
    """`reproduce --json` with no table/figure emits the catalogue, not a table."""
    code, stdout, _ = _run(["reproduce", "--json"])
    assert code == 0
    cat = json.loads(stdout)
    assert "available" in cat and "external" in cat
    flags = {row["flag"] for row in cat["available"]}
    assert {"--table 3", "--table 4", "--figure 5"} <= flags
