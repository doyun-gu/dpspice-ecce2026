"""``dpspice`` command-line interface (Typer + Rich).

Netlist in, result out. The interactive experience is polished (banner,
spinners, boxed result panels), but every flourish auto-disables when stdout
is not a TTY, and ``--quiet`` / ``--no-banner`` give scripted and academic
users clean, parseable output. ``--out result.json`` writes data only.
"""
from __future__ import annotations

import json
import sys
from typing import Optional

try:
    import typer
    from rich import box
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text
except ModuleNotFoundError as exc:  # CLI extras not installed
    raise SystemExit(
        f"The `dpspice` command needs the CLI extras (missing: {exc.name}). "
        f"Install them with:  pip install 'dpspice[cli]'\n"
        f"(The Python API `import dpspice` works without them.)"
    )

from . import __version__
from . import api
from .dispatch import DpspiceError

app = typer.Typer(
    add_completion=False,
    help="DPSpice — topology-independent dynamic-phasor circuit simulation (ECCE 2026).",
    no_args_is_help=True,
)

# stdout console for results; stderr for chrome so piped stdout stays clean.
out = Console()
err = Console(stderr=True)


def _version_callback(value: bool) -> None:
    if value:
        # Bare version on stdout, nothing else — safe to capture in scripts.
        print(__version__)
        raise typer.Exit()


@app.callback()
def _root(
    version: bool = typer.Option(
        False,
        "--version",
        "-V",
        help="Show the DPSpice version and exit.",
        callback=_version_callback,
        is_eager=True,
    ),
) -> None:
    """DPSpice — topology-independent dynamic-phasor circuit simulation (ECCE 2026)."""

SUBTITLE = "Dynamic Phasor Circuit Simulation"
# Muted teal-green wordmark — professional, not a bright "hacker" cyan.
BANNER_STYLE = "#3fa796"


def _interactive(quiet: bool) -> bool:
    """True only when we should render banners/spinners."""
    return out.is_terminal and not quiet


def banner(quiet: bool, no_banner: bool) -> None:
    if quiet or no_banner or not out.is_terminal:
        return
    try:
        from pyfiglet import figlet_format
        art = figlet_format("DPSpice", font="ansi_shadow").rstrip("\n")
        err.print(Text(art, style=BANNER_STYLE))
    except Exception:
        err.print(Text("DPSpice", style=f"bold {BANNER_STYLE}"))
    err.print(Text(f"  {SUBTITLE}  ·  v{__version__}  ·  ECCE 2026\n", style="dim"))


def _fail(exc: Exception) -> None:
    """Print an actionable error (never a traceback) and exit non-zero."""
    err.print(Text(f"error: {exc}", style="bold red"))
    raise typer.Exit(code=1)


# ----------------------------------------------------------------------
# Plain (undecorated) output for --quiet / scripted callers
#
# --quiet must be genuinely non-decorative: no banner, no spinners, and no Rich
# box/panel/table chrome — just plain machine-friendly lines. Structured output
# still goes through --json / --out. These helpers write bare text directly to
# the underlying streams (bypassing Rich) so nothing styles or boxes them.
# ----------------------------------------------------------------------

def _line(msg: str = "") -> None:
    print(msg, file=sys.stdout)


def _eline(msg: str = "") -> None:
    print(msg, file=sys.stderr)


def _plain_warnings(warnings) -> None:
    for w in warnings:
        _eline(f"warning: {w}")


def _decisions_table(decisions) -> Table:
    t = Table(title="Auto-decisions (Tier 2)", title_style="bold",
              show_header=True, header_style="bold")
    t.add_column("field"); t.add_column("value"); t.add_column("source"); t.add_column("reason")
    for d in decisions:
        style = "yellow" if d.source == "auto" else ("cyan" if d.source == "override" else "green")
        t.add_row(d.field, str(d.value), Text(d.source, style=style), d.reason)
    return t


# ----------------------------------------------------------------------
# info
# ----------------------------------------------------------------------

@app.command()
def info(
    netlist: str = typer.Argument(..., help="Path to a .sp/.cir/.net file (or netlist text)."),
    mode: str = typer.Option("auto", help="auto|td|idp|hb (preview only; no solve)."),
    omega: Optional[str] = typer.Option(None, help="Carrier frequency in Hz (SPICE suffixes ok)."),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Essential output only."),
    no_banner: bool = typer.Option(False, "--no-banner", help="Drop the startup banner."),
    json_out: bool = typer.Option(False, "--json", help="Emit JSON to stdout (implies quiet chrome)."),
):
    """Parse only: report MNA states, detected mode, omega, devices. No solve."""
    banner(quiet or json_out, no_banner)
    try:
        result = api.load(netlist).info(mode=mode, omega=omega)
    except DpspiceError as exc:
        _fail(exc)

    if json_out:
        out.print_json(json.dumps(result.to_dict()))
        return

    if quiet:
        _line(f"title: {result.netlist_title}")
        _line(f"states: {result.n_states}")
        _line(f"nodes: {result.n_nodes}")
        _line(f"mode: {result.mode_selected}")
        _line(f"reason: {result.reason}")
        _line(f"omega_hz: {result.omega_hz if result.omega_hz else ''}")
        _line(f"nonlinear: {'yes' if result.has_nonlinear else 'no'}")
        _line(f"devices: {', '.join(result.devices)}")
        if result.tran:
            _line(f"tran: {result.tran}")
        for d in result.decisions:
            _line(f"decision: {d.field}={d.value} [{d.source}] {d.reason}")
        _plain_warnings(result.warnings)
        return

    panel = Table.grid(padding=(0, 2))
    panel.add_column(style="bold"); panel.add_column()
    panel.add_row("Title", result.netlist_title)
    panel.add_row("MNA states", str(result.n_states))
    panel.add_row("Nodes", str(result.n_nodes))
    panel.add_row("Mode", Text(result.mode_selected.upper(), style="bold magenta"))
    panel.add_row("Reason", result.reason)
    panel.add_row("Carrier", f"{result.omega_hz:g} Hz" if result.omega_hz else "—")
    panel.add_row("Nonlinear", "yes" if result.has_nonlinear else "no")
    panel.add_row("Devices", ", ".join(result.devices))
    if result.tran:
        panel.add_row(".tran", str(result.tran))
    out.print(Panel(panel, title="dpspice info", border_style="cyan",
                    box=box.SQUARE, expand=False))
    out.print(_decisions_table(result.decisions))
    for w in result.warnings:
        err.print(Text(f"warning: {w}", style="yellow"))


# ----------------------------------------------------------------------
# run
# ----------------------------------------------------------------------

@app.command()
def run(
    netlist: str = typer.Argument(..., help="Path to a .sp/.cir/.net file (or netlist text)."),
    mode: str = typer.Option("auto", help="auto|td|idp|hb."),
    harmonics: Optional[int] = typer.Option(None, "--harmonics", "-K", help="HB harmonic count K."),
    omega: Optional[str] = typer.Option(None, help="Carrier frequency in Hz (SPICE suffixes ok)."),
    tol: Optional[float] = typer.Option(None, help="Solver tolerance."),
    out_file: Optional[str] = typer.Option(None, "--out", help="Write full result JSON here (data only)."),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Essential output only."),
    no_banner: bool = typer.Option(False, "--no-banner", help="Drop the startup banner."),
    json_out: bool = typer.Option(False, "--json", help="Emit result JSON to stdout."),
):
    """Parse, auto-decide, simulate; print a summary and (optionally) save waveforms."""
    chrome = _interactive(quiet or json_out)
    banner(quiet or json_out, no_banner)
    try:
        ckt = api.load(netlist)
        if chrome:
            with err.status("[cyan]Parsing + stamping MNA…", spinner="dots"):
                preview = ckt.info(mode=mode, omega=omega)
            err.print(Text(f"  ✓ Stamped MNA system ({preview.n_states} states)", style="green"))
            err.print(Text(f"  ✓ {preview.reason}", style="green"))
            with err.status(f"[cyan]Solving ({preview.mode_selected.upper()})…", spinner="dots"):
                result = ckt.run(mode=mode, harmonics=harmonics, omega=omega, tol=tol)
            err.print(Text(f"  ✓ Solved in {result.solve_time*1000:.1f} ms", style="green"))
        else:
            result = ckt.run(mode=mode, harmonics=harmonics, omega=omega, tol=tol)
    except DpspiceError as exc:
        _fail(exc)

    if out_file:
        with open(out_file, "w", encoding="utf-8") as fh:
            json.dump(result.to_dict(include_waveforms=True), fh, indent=2)
        if not (quiet or json_out):
            err.print(Text(f"  ✓ Wrote {out_file}", style="green"))

    if json_out:
        out.print_json(json.dumps(result.to_dict(include_waveforms=False)))
        return

    if quiet:
        _plain_run(result)
        return

    _render_run(result, quiet)


def _plain_run(result) -> None:
    _line(f"solver: {result.solver}")
    _line(f"reason: {result.reason}")
    if result.omega_hz:
        _line(f"omega_hz: {result.omega_hz:g}")
    _line(f"states: {result.states}")
    if result.K is not None:
        _line(f"harmonics_K: {result.K}")
        _line(f"converged: {result.converged} iters={result.iters} "
              f"residual={result.residual:.3e}")
    _line(f"solve_ms: {result.solve_time * 1000:.3f}")
    if "conduction_angle_deg" in result.summary:
        _line(f"conduction_angle_deg: {result.summary['conduction_angle_deg']:.3f}")
    nodes = result.summary.get("nodes", {})
    for name, vals in nodes.items():
        metrics = " ".join(f"{k}={v:.6g}" for k, v in vals.items())
        _line(f"node {name}: {metrics}")
    _plain_warnings(result.warnings)


def _render_run(result, quiet: bool) -> None:
    head = Table.grid(padding=(0, 2))
    head.add_column(style="bold"); head.add_column()
    head.add_row("Solver", Text(result.solver.upper(), style="bold magenta"))
    head.add_row("Reason", result.reason)
    if result.omega_hz:
        head.add_row("Carrier", f"{result.omega_hz:g} Hz")
    head.add_row("MNA states", str(result.states))
    if result.K is not None:
        head.add_row("Harmonics K", str(result.K))
        head.add_row("Converged", f"{result.converged} ({result.iters} iters, res {result.residual:.1e})")
    head.add_row("Solve time", f"{result.solve_time*1000:.1f} ms")
    if "conduction_angle_deg" in result.summary:
        head.add_row("Conduction angle", f"{result.summary['conduction_angle_deg']:.1f}°")
    out.print(Panel(head, title="dpspice run", border_style="green",
                    box=box.SQUARE, expand=False))

    nodes = result.summary.get("nodes", {})
    if nodes:
        t = Table(show_header=True, header_style="bold")
        cols = ["node"] + list(next(iter(nodes.values())).keys())
        for c in cols:
            t.add_column(c)
        for name, vals in nodes.items():
            t.add_row(name, *[f"{v:.4g}" for v in vals.values()])
        out.print(t)
    if not quiet:
        for w in result.warnings:
            err.print(Text(f"warning: {w}", style="yellow"))


# ----------------------------------------------------------------------
# validate
# ----------------------------------------------------------------------

@app.command()
def validate(
    netlist: str = typer.Argument(..., help="The netlist to simulate: a .sp/.cir/.net file (or netlist text). Not a .raw."),
    ref: Optional[str] = typer.Option(None, "--ref", help="LTspice/ngspice .raw OUTPUT to validate against (a simulation result, not a netlist). Omit to auto-run ngspice."),
    keep_raw: bool = typer.Option(False, "--keep-raw", help="Keep the auto-generated ngspice .raw."),
    mode: str = typer.Option("auto", help="auto|td|idp|hb."),
    harmonics: Optional[int] = typer.Option(None, "--harmonics", "-K", help="HB harmonic count K."),
    omega: Optional[str] = typer.Option(None, help="Carrier frequency in Hz."),
    json_out: bool = typer.Option(False, "--json", help="Emit JSON to stdout."),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Essential output only."),
):
    """Run + cross-validate. With no --ref, ngspice is driven automatically."""
    chrome = _interactive(quiet or json_out)
    banner(quiet or json_out, no_banner=False)
    try:
        if chrome and ref is None:
            with err.status("[cyan]Running ngspice reference + DPSpice…", spinner="dots"):
                report = api.load(netlist).validate(ref=ref, mode=mode, harmonics=harmonics,
                                                    omega=omega, keep_raw=keep_raw).to_dict()
        else:
            report = api.load(netlist).validate(ref=ref, mode=mode, harmonics=harmonics,
                                                omega=omega, keep_raw=keep_raw).to_dict()
    except DpspiceError as exc:
        _fail(exc)

    if json_out:
        out.print_json(json.dumps(report))
        return

    if quiet:
        _line(f"reference: {report['reference']} [{report['reference_engine']}]")
        _line(f"solver: {report['solver']}")
        for p in report["per_node"]:
            _line(f"node {p['node']}: nrmse={p['nrmse']:.6g} r2={p['r2']:.6g} "
                  f"max_abs_error={p['max_abs_error']:.6g}")
        verdict = "PASS" if report["worst_nrmse"] < 0.05 else "REVIEW"
        _line(f"verdict: {verdict} worst_nrmse={report['worst_nrmse']:.6g}")
        return

    ref_label = report["reference"]
    t = Table(title=f"Validation vs {ref_label} [{report['reference_engine']}]",
              title_style="bold", header_style="bold")
    t.add_column("node"); t.add_column("NRMSE", justify="right")
    t.add_column("R²", justify="right"); t.add_column("max|err|", justify="right")
    for p in report["per_node"]:
        ok = p["nrmse"] < 0.05
        t.add_row(p["node"], Text(f"{p['nrmse']:.4%}", style="green" if ok else "red"),
                  f"{p['r2']:.6f}", f"{p['max_abs_error']:.3g}")
    out.print(t)
    verdict = "PASS" if report["worst_nrmse"] < 0.05 else "REVIEW"
    style = "bold green" if verdict == "PASS" else "bold yellow"
    out.print(Text(f"{verdict}  (worst NRMSE {report['worst_nrmse']:.4%}, "
                   f"solver {report['solver'].upper()})", style=style))


# ----------------------------------------------------------------------
# bench
# ----------------------------------------------------------------------

@app.command()
def bench(
    json_out: bool = typer.Option(False, "--json", help="Emit JSON to stdout."),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Plain, undecorated output."),
):
    """Regenerate the computational benchmark over the bundled example netlists."""
    from . import reproduce as _rep
    rows = _rep.bench()
    if json_out:
        out.print_json(json.dumps(rows))
        return
    if quiet:
        for r in rows:
            _line(f"{r['case']}: solver={r['solver']} states={r['states']} "
                  f"K={r.get('K')} solve_ms={r['solve_ms']:.3f}")
        return
    t = Table(title="DPSpice computational benchmark", title_style="bold", header_style="bold")
    for c in ["case", "solver", "states", "K", "solve_ms"]:
        t.add_column(c, justify="right" if c in ("states", "K", "solve_ms") else "left")
    for r in rows:
        t.add_row(r["case"], r["solver"], str(r["states"]),
                  str(r.get("K", "—")), f"{r['solve_ms']:.1f}")
    out.print(t)


# ----------------------------------------------------------------------
# reproduce
# ----------------------------------------------------------------------

@app.command()
def reproduce(
    figure: Optional[int] = typer.Option(None, "--figure", help="Paper figure number."),
    table: Optional[int] = typer.Option(None, "--table", help="Paper table number."),
    json_out: bool = typer.Option(False, "--json", help="Emit JSON to stdout."),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Plain, undecorated output."),
):
    """Regenerate a specific paper figure/table from the real engine.

    With no ``--table``/``--figure`` this lists the available artifacts. The
    ``--json`` flag always emits JSON — including the listing — and never falls
    back to a decorated table.
    """
    from . import reproduce as _rep

    # No artifact selected: list what is reproducible. --json must stay JSON.
    if figure is None and table is None:
        cat = _rep.catalogue()
        if json_out:
            out.print_json(json.dumps(cat))
            return
        if quiet:
            for a in cat["available"]:
                _line(f"{a['flag']}: {a['label']} [{a['status']}]")
            for e in cat["external"]:
                _line(f"{e['flag']}: {e['label']} [{e['status']}]")
            _line(cat["hint"])
            return
        out.print(_rep.catalogue_table())
        return

    try:
        result = _rep.reproduce(figure=figure, table=table)
    except DpspiceError as exc:
        _fail(exc)
    if json_out:
        out.print_json(json.dumps(result))
        return
    if quiet:
        _line(json.dumps(result))
        return
    out.print(Panel(json.dumps(result, indent=2), title=result.get("label", "reproduce"),
                    border_style="cyan", box=box.SQUARE, expand=False))


# ----------------------------------------------------------------------
# suite (automated validation)
# ----------------------------------------------------------------------

@app.command()
def suite(
    family: Optional[list[str]] = typer.Option(
        None, "--family", "-f",
        help="Restrict to these families (repeat or comma-separate). Default: all non-experimental."),
    full: bool = typer.Option(False, "--full", help="Full parameter sweeps (default is quick)."),
    quick: bool = typer.Option(False, "--quick", help="Force the reduced sweep (default)."),
    self_check_only: bool = typer.Option(
        False, "--self-check",
        help="Only check the two oracles agree on series RLC (validates the harness)."),
    json_out: bool = typer.Option(False, "--json", help="Emit the full report as JSON to stdout."),
    csv_out: Optional[str] = typer.Option(None, "--csv", help="Write per-case results to this CSV."),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Plain, undecorated output."),
):
    """Auto-generate circuit families, solve each, and score against an independent oracle.

    Closed-form analytic references for RC/RL/RLC; ngspice for ladder, coupled,
    and rectifier circuits. Every reported error is a real DPSpice run vs a real
    oracle, classified against a per-family tolerance band. Exits non-zero on any
    FAIL (SKIP, e.g. ngspice missing, does not fail the run).
    """
    from .validation import run_suite, self_check
    is_quick = not full  # quick is the default; --full opts into the sweep

    banner(json_out or quiet, no_banner=False)

    if self_check_only:
        rows = self_check(quick=is_quick)
        if json_out:
            out.print_json(json.dumps(rows))
            return
        if quiet:
            for r in rows:
                nr = "" if r["nrmse"] is None else f"{r['nrmse']:.3e}"
                _line(f"{r['name']}: status={r['status']} nrmse={nr}")
            if any(r["status"] == "disagree" for r in rows):
                raise typer.Exit(code=1)
            return
        t = Table(title="Oracle self-check (closed-form vs ngspice)",
                  title_style="bold", header_style="bold")
        t.add_column("case"); t.add_column("status"); t.add_column("NRMSE", justify="right")
        for r in rows:
            style = {"agree": "green", "disagree": "red"}.get(r["status"], "yellow")
            nr = "—" if r["nrmse"] is None else f"{r['nrmse']:.3e}"
            t.add_row(r["name"], Text(r["status"], style=style), nr)
        out.print(t)
        if any(r["status"] == "disagree" for r in rows):
            raise typer.Exit(code=1)
        return

    only = _split_families(family)
    chrome = _interactive(json_out or quiet)
    if chrome:
        with err.status("[cyan]Generating circuits, solving, scoring vs oracles…", spinner="dots"):
            report = run_suite(quick=is_quick, only=only)
    else:
        report = run_suite(quick=is_quick, only=only)

    if csv_out:
        _write_suite_csv(csv_out, report)
        if not (json_out or quiet):
            err.print(Text(f"  ✓ Wrote {csv_out}", style="green"))

    if json_out:
        out.print_json(json.dumps(report))
        raise typer.Exit(code=1 if report["totals"]["failed"] else 0)

    if quiet:
        _plain_suite(report)
        raise typer.Exit(code=1 if report["totals"]["failed"] else 0)

    _render_suite(report)
    raise typer.Exit(code=1 if report["totals"]["failed"] else 0)


def _plain_suite(report: dict) -> None:
    for key, s in report["per_family"].items():
        def fmt(x):
            return "" if x is None else f"{x:.2e}"
        _line(f"{key}: pass={s['passed']} fail={s['failed']} skip={s['skipped']} "
              f"nrmse_min={fmt(s['nrmse_min'])} nrmse_median={fmt(s['nrmse_median'])} "
              f"nrmse_max={fmt(s['nrmse_max'])}")
    for c in report["cases"]:
        if c["status"] == "fail":
            _line(f"FAIL {c['family']}/{c['name']}: {c['reason']}")
    tot = report["totals"]
    verdict = "FAIL" if tot["failed"] else "PASS"
    _line(f"verdict: {verdict} passed={tot['passed']} failed={tot['failed']} "
          f"skipped={tot['skipped']} borderline={tot['borderline']}")
    for w in report["warnings"]:
        _eline(f"note: {w}")


def _split_families(family: Optional[list[str]]) -> Optional[list[str]]:
    if not family:
        return None
    keys: list[str] = []
    for item in family:
        keys.extend(k.strip() for k in item.split(",") if k.strip())
    return keys or None


def _write_suite_csv(path: str, report: dict) -> None:
    import csv
    fields = ["family", "name", "oracle", "status", "nrmse", "r2",
              "max_abs_error", "band", "borderline", "solve_ms", "states", "K", "reason"]
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for c in report["cases"]:
            w.writerow(c)


def _render_suite(report: dict) -> None:
    # Per-family summary.
    fam = Table(title="Validation suite — per family", title_style="bold", header_style="bold")
    fam.add_column("family"); fam.add_column("pass", justify="right")
    fam.add_column("fail", justify="right"); fam.add_column("skip", justify="right")
    fam.add_column("NRMSE min/median/max", justify="right")
    for key, s in report["per_family"].items():
        def fmt(x):
            return "—" if x is None else f"{x:.2e}"
        band = f"{fmt(s['nrmse_min'])} / {fmt(s['nrmse_median'])} / {fmt(s['nrmse_max'])}"
        fail_style = "red" if s["failed"] else "dim"
        fam.add_row(key, str(s["passed"]),
                    Text(str(s["failed"]), style=fail_style), str(s["skipped"]), band)
    out.print(fam)

    # Failures + borderline detail (with the netlist for reproducibility).
    fails = [c for c in report["cases"] if c["status"] == "fail"]
    border = [c for c in report["cases"] if c.get("borderline")]
    for c in fails:
        body = Text()
        body.append(f"reason: {c['reason']}\n", style="red")
        body.append("netlist:\n", style="bold")
        body.append(c["netlist"])
        out.print(Panel(body, title=f"FAIL · {c['family']}/{c['name']}", border_style="red",
                         box=box.SQUARE, expand=False))
    for c in border:
        out.print(Text(f"borderline · {c['family']}/{c['name']}: "
                       f"NRMSE {c['nrmse']:.3e} vs band {c['band']:.1e}", style="yellow"))

    tot = report["totals"]
    verdict = "FAIL" if tot["failed"] else "PASS"
    style = "bold red" if tot["failed"] else "bold green"
    out.print(Text(f"{verdict}  {tot['passed']} passed, {tot['failed']} failed, "
                   f"{tot['skipped']} skipped, {tot['borderline']} borderline", style=style))
    for w in report["warnings"]:
        err.print(Text(f"note: {w}", style="yellow"))


# ----------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------

def main() -> None:  # entry point
    app()


if __name__ == "__main__":
    main()
