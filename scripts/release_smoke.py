"""Check an installed release from a fresh directory, without a source import.

Run with the target environment's Python after installing the wheel or sdist.
The output directory must not exist. No external simulator or network is used.
"""
from __future__ import annotations

import argparse
import importlib.metadata
import json
import math
import os
from pathlib import Path
import platform
import subprocess
import sys
import sysconfig


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--expected-version", required=True)
    args = parser.parse_args()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    cli = Path(sysconfig.get_path("scripts")) / (
        "dpspice.exe" if os.name == "nt" else "dpspice"
    )
    if not cli.is_file():
        raise RuntimeError("The target environment has no dpspice entry point")
    version = importlib.metadata.version("dpspice")
    if version != args.expected_version:
        raise RuntimeError(f"Expected {args.expected_version}, installed {version}")
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    for name in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS",
                 "MKL_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"):
        env[name] = "1"
    checks = []

    def run(name: str, *arguments: str, refusal: bool = False):
        process = subprocess.run(
            [str(cli), *arguments], cwd=out, env=env,
            capture_output=True, text=True, timeout=120,
        )
        (out / f"{name}.stdout.txt").write_text(process.stdout, encoding="utf-8")
        (out / f"{name}.stderr.txt").write_text(process.stderr, encoding="utf-8")
        if refusal:
            if process.returncode == 0 or "Traceback" in process.stderr:
                raise RuntimeError("Unsupported device was not cleanly refused")
            if "Q1" not in process.stderr:
                raise RuntimeError("Refusal did not identify the unsupported device")
        elif process.returncode:
            raise RuntimeError(f"{name} failed: {process.stderr[-2000:]}")
        checks.append({"name": name, "returncode": process.returncode})
        return process.stdout

    def waveform_file(name: str):
        result = json.loads((out / name).read_text(encoding="utf-8"))
        waves = result.get("waveforms", [])
        if not waves or result.get("converged") is False:
            raise RuntimeError(f"Missing waveforms or failed convergence: {name}")
        for wave in waves:
            times, values = wave["t"], wave["v"]
            if len(times) != len(values) or len(times) < 2:
                raise RuntimeError(f"Incomplete waveform: {name}")
            if not all(math.isfinite(float(v)) for v in times + values):
                raise RuntimeError(f"Non-finite waveform: {name}")
        return result

    run("version", "--version")
    json.loads(run("examples", "examples", "--json"))
    json.loads(run("rlc-info", "info", "rlc.sp", "--json"))
    run("rlc", "run", "rlc.sp", "--out", "rlc-result.json", "--json")
    waveform_file("rlc-result.json")
    run("buck-route", "route", "buck_sync.sp", "--json")
    run("buck", "run", "buck_sync.sp", "--analysis", "hb", "--K", "7",
        "--out", "buck-result.json", "--json")
    waveform_file("buck-result.json")
    table = json.loads(run("table5", "reproduce", "--table", "5", "--json"))
    golden_path = Path(__file__).resolve().parents[1] / "tests/golden_reference.json"
    golden = json.loads(golden_path.read_text(encoding="utf-8"))["entries"]
    entries = (
        "rectifier_table5_resistive_nrmse_K15",
        "rectifier_table5_rcmild_nrmse_K30",
        "rectifier_table5_rcstrong_nrmse_K40",
    )
    if len(table["rows"]) != len(entries):
        raise RuntimeError("Table V did not reproduce all three loads")
    for row, key in zip(table["rows"], entries):
        ref = golden[key]
        error = row["nrmse_vs_ltspice"]
        if not math.isfinite(error) or abs(error - ref["value"]) > (
            ref["atol"] + ref["rtol"] * abs(ref["value"])
        ):
            raise RuntimeError(f"Table V reference gate failed: {key}")
    (out / "unsupported.sp").write_text(
        "* unsupported transistor\nV1 in 0 SINE(0 1 50)\n"
        "Q1 a b c npn\nR1 in 0 1k\n.tran 0 1m\n.end\n", encoding="utf-8",
    )
    run("unsupported", "run", "unsupported.sp", refusal=True)
    receipt = {
        "status": "passed", "version": version,
        "python": platform.python_version(), "platform": platform.system(),
        "checks": checks,
        "table5_nrmse": [row["nrmse_vs_ltspice"] for row in table["rows"]],
        "dependencies": {name: importlib.metadata.version(name)
                         for name in ("numpy", "scipy", "simpleeval", "typer", "rich")},
        "scope": "Installed CLI smoke and stored-reference accuracy, not arbitrary-circuit validation",
    }
    (out / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(receipt, indent=2))


if __name__ == "__main__":
    main()
