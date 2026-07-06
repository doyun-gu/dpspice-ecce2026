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


def _run(args, cwd=None):
    """Run ``dpspice <args>`` and return (returncode, stdout, stderr)."""
    proc = subprocess.run(
        [_dpspice(), *args],
        capture_output=True, text=True, timeout=300, cwd=cwd,
    )
    return proc.returncode, proc.stdout, proc.stderr


def _json_commands():
    """Every command README advertises as accepting --json, with real args."""
    with _rectifier_refs() as (sp, raw), example_path("buck_sync.sp") as buck:
        return [
            (["info", sp, "--json"]),
            (["run", sp, "--json"]),
            (["route", str(buck), "--json"]),
            (["run", str(buck), "--analysis", "hb", "--K", "7", "--json"]),
            (["bench", "--json"]),
            (["reproduce", "--json"]),
            (["reproduce", "--table", "3", "--json"]),
            (["reproduce", "--table", "5", "--json"]),
            (["reproduce", "--figure", "6", "--json"]),
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
    ["route", "--quiet"],
    ["bench", "--quiet"],
    ["reproduce", "--quiet"],
    ["validate", "--quiet"],
    ["suite", "--quick", "--quiet"],
])
def test_quiet_has_no_box_chrome(args):
    # Fill in the netlist/ref placeholders that some commands need.
    with _rectifier_refs() as (sp, raw), example_path("buck_sync.sp") as buck:
        cmd = list(args)
        if cmd[0] in {"info", "run"}:
            cmd.insert(1, sp)
        elif cmd[0] == "route":
            cmd.insert(1, str(buck))
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
    assert {"--table 3", "--table 5", "--figure 6"} <= flags


# ----------------------------------------------------------------------
# Bundled-example resolution + bare invocation (README: examples resolve
# from any working directory; `dpspice <file>` implies `run`).
# ----------------------------------------------------------------------

def test_readme_examples_path_resolves_from_any_cwd(tmp_path):
    """The README quickstart form `dpspice run examples/rlc.sp` must work
    from a directory that contains no examples/ folder."""
    code, stdout, stderr = _run(
        ["run", "examples/rlc.sp", "--quiet", "--no-banner"], cwd=tmp_path)
    assert code == 0, f"stderr={stderr[:400]}"
    assert "solver: idp" in stdout
    assert "using bundled example rlc.sp" in stderr


def test_bare_and_extensionless_example_names_resolve(tmp_path):
    code, stdout, stderr = _run(
        ["route", "buck_sync", "--quiet", "--no-banner"], cwd=tmp_path)
    assert code == 0, f"stderr={stderr[:400]}"
    assert "using bundled example buck_sync.sp" in stderr
    assert "S1" in stdout and "switch-LTP" in stdout


def test_local_file_wins_over_bundled_example(tmp_path):
    """A file on disk named like a bundled example must shadow the bundle."""
    local = tmp_path / "rlc.sp"
    local.write_text("* local shadow: bare RC, not the bundled RLC\n"
                     "V1 in 0 SINE(0 1 1k)\nR1 in out 1k\nC1 out 0 1u\n"
                     ".tran 10u 5m\n.end\n")
    code, stdout, stderr = _run(
        ["run", "rlc.sp", "--quiet", "--no-banner"], cwd=tmp_path)
    assert code == 0, f"stderr={stderr[:400]}"
    assert "using bundled example" not in stderr
    assert "states: 3" in stdout  # the 2-node local RC, not the 5-state RLC


def test_bare_invocation_defaults_to_run(tmp_path):
    """`dpspice <netlist>` (the drag-a-file-into-the-terminal form) runs it."""
    code, stdout, stderr = _run(
        ["buck_sync.sp", "--quiet", "--no-banner"], cwd=tmp_path)
    assert code == 0, f"stderr={stderr[:400]}"
    assert "solver: ltp" in stdout


def test_examples_listing_json_is_pure():
    code, stdout, _ = _run(["examples", "--json"])
    assert code == 0
    rows = json.loads(stdout)
    names = {r["name"] for r in rows}
    assert {"rlc.sp", "buck_sync.sp", "rectifier_halfwave.sp"} <= names
    assert all("title" in r for r in rows)
    assert not (_BOX_CHARS & set(stdout))


def test_examples_show_prints_netlist():
    code, stdout, _ = _run(["examples", "buck_sync"])
    assert code == 0
    assert "SWMOD" in stdout and not (_BOX_CHARS & set(stdout))


def test_examples_copy_refuses_overwrite(tmp_path):
    code, _, stderr = _run(["examples", "buck_sync", "--copy"], cwd=tmp_path)
    assert code == 0, f"stderr={stderr[:400]}"
    assert (tmp_path / "buck_sync.sp").is_file()
    code2, _, stderr2 = _run(["examples", "buck_sync", "--copy"], cwd=tmp_path)
    assert code2 != 0
    assert "Refusing to overwrite" in stderr2


def test_validate_resolves_bundled_ref(tmp_path):
    code, stdout, stderr = _run(
        ["validate", "rectifier_halfwave.sp",
         "--ref", "rectifier_halfwave.raw", "--quiet"], cwd=tmp_path)
    assert code == 0, f"stderr={stderr[:400]}"
    assert "verdict: PASS" in stdout


def test_unknown_example_name_fails_loudly(tmp_path):
    code, _, stderr = _run(
        ["examples", "does_not_exist"], cwd=tmp_path)
    assert code != 0
    assert "No bundled example" in stderr and "buck_sync.sp" in stderr
