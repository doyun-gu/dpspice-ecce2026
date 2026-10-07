# DPSpice Quickstart

DPSpice is an Apache-2.0 Python package (Python >= 3.10) with a `dpspice` command-line tool. This guide takes you from installation to a first result file in a few commands.

## 1. Install

The commands below target v1.1.1. Its tag and downloadable assets must be
published before these commands can be used; check the [release page](https://github.com/doyun-gu/dpspice-ecce2026/releases/tag/v1.1.1).

### Option A: pipx (recommended)

You need Python 3.10 or newer, pipx, Git, and an internet connection.

```
pipx install "dpspice[cli] @ git+https://github.com/doyun-gu/dpspice-ecce2026.git@v1.1.1"
```

pipx installs DPSpice in its own isolated environment, so your other Python installations stay unchanged.

### Option B: Install from source into a virtual environment

On POSIX systems (macOS or Linux shell):

```
git clone --branch v1.1.1 https://github.com/doyun-gu/dpspice-ecce2026.git
cd dpspice-ecce2026
python3 -m venv .venv
source .venv/bin/activate
python -m pip install ".[cli]"
```

On Windows (PowerShell):

```
git clone --branch v1.1.1 https://github.com/doyun-gu/dpspice-ecce2026.git
cd dpspice-ecce2026
py -m venv .venv
& .\.venv\Scripts\Activate.ps1
python -m pip install ".[cli]"
```

The release preparation checks use fresh macOS Python 3.13 environments. Check the release commit’s GitHub Actions results for the other platforms; the presence of a workflow is not evidence that it passed.

### Option C: Downloaded wheel (no Git required)

Download `dpspice-1.1.1-py3-none-any.whl` and `SHA256SUMS.txt` from the [GitHub release](https://github.com/doyun-gu/dpspice-ecce2026/releases/tag/v1.1.1). Verify the SHA-256 digest against the release list. From the download directory, in an isolated environment:

```sh
python -m pip install "./dpspice-1.1.1-py3-none-any.whl[cli]"
```

The wheel does not bundle NumPy or SciPy; dependency installation still needs internet unless you separately provide compatible wheels. The versioned tag and assets become available only when the release is published.

## 2. Check the installation

```
dpspice --version
dpspice examples
```

The first command reports the installed version. The second lists the bundled example netlists. You can pass bare example names such as `rlc.sp` from any directory, so you do not need a repository checkout.

## 3. First result: linear RLC transient

```
dpspice info rlc.sp
dpspice run rlc.sp --out rlc-result.json
```

The `info` command describes the netlist. The `run` command writes the simulated waveforms as JSON to `rlc-result.json` in your current directory.

## 4. Reproduce paper Table V

```
dpspice reproduce --table 5 --json
```

This command compares the diode-rectifier harmonic-balance results against LTspice reference waveforms that ship with the package. You do not need to install LTspice. The expected NRMSE values are approximately:

- 0.409% for the resistive load
- 0.134% for the mild-RC load
- 0.137% for the strong-RC load

## 5. Switched-linear buck converter (harmonic balance)

```
dpspice route buck_sync.sp
dpspice run buck_sync.sp --analysis hb --K 7 --out buck-result.json
```

The `route` command shows how DPSpice classifies the netlist for analysis. The `run` command writes the waveforms as JSON to `buck-result.json`. The `--K` option sets the harmonic truncation, so the results are a finite-harmonic approximation.

## Next steps

- See [supported scope](SUPPORTED_SCOPE.md) for what is supported, what is experimental, and what is out of scope.
- The browser version at [dpspice.com](https://dpspice.com) runs a separately pinned public v1.1.0 engine through an adapter. Its accepted inputs and features may differ from this CLI.
