# euler-toolbox

Unified CLI for dataset-processing tools. Wraps multiple tools behind a single
entry point with machine-readable schema output for pipeline automation and
built-in path-origin tracking for HPC / Slurm workflows.

## Installation

```bash
pip install -e .
# or, if dependencies are already installed separately:
uv pip install -e . --no-deps

uv pip install "euler-toolbox @ git+https://github.com/d-rothen/euler-toolbox.git"
```

## Quick start

```bash
# List available tools
euler-toolbox list

# Show help for a tool
euler-toolbox run sample-dataset --help

# Run a tool
euler-toolbox run sample-dataset \
  --dataset-paths /data/rgb.zip \
  --dataset-paths /data/depth.zip \
  --dataset-paths /data/segmentation.zip

# Get the machine-readable schema
euler-toolbox schema sample-dataset
```

---

## CLI structure

```
euler-toolbox
  list                          List all registered tools
  schema <tool> [options]       Emit JSON schema for a tool
  run <tool> [options]          Execute a tool
```

### `euler-toolbox list`

Prints every registered tool with its one-line description.

### `euler-toolbox schema`

Outputs a JSON object describing a tool's parameters, types, defaults, and
placeholders. Designed to be consumed by pipeline orchestrators over SSH.

```bash
# Single tool
euler-toolbox schema sample-dataset

# All tools at once
euler-toolbox schema --all

# Include a ready-to-fill invocation template
euler-toolbox schema sample-dataset --format template

# Change placeholder syntax
euler-toolbox schema sample-dataset --placeholder-style shell   # ${x}
euler-toolbox schema sample-dataset --placeholder-style plain   # x
euler-toolbox schema sample-dataset --placeholder-style mustache # {{x}} (default)
```

The output is always plain JSON to stdout (no ANSI, no Rich formatting), so it
works cleanly over `ssh host euler-toolbox schema sample-dataset | jq ...`.

### `euler-toolbox run <tool>`

Executes a tool. Every `run` subcommand accepts two global options in addition
to the tool's own parameters:

| Flag | Description |
|---|---|
| `--log-level` | `DEBUG`, `INFO` (default), `WARNING`, `ERROR` |
| `--origin-map` | Path-origin rewrite rules (see below) |

---

## TrackedPath and origin tracking

### The problem

On HPC clusters (Slurm, etc.), datasets are commonly copied from a shared
filesystem to a fast node-local `$TMPDIR` before processing. The tool needs to
operate on the local copy (for I/O performance) but log/record the *original*
location (for reproducibility and metadata).

A single logical dataset therefore has **two paths**:

| | Example | Used for |
|---|---|---|
| **working** | `$TMPDIR/rgb.zip` | Actual data loading, file I/O |
| **origin** | `/scratch/project/rgb.zip` | Logging, metadata, provenance |

### How it works

Every path parameter declared as `TrackedPath` in a tool becomes a pair of
values internally: `.working` and `.origin`. The CLI provides three ways to
set the origin, applied in this priority order:

#### 1. Per-parameter `--<param>-origin` (highest priority)

Every `TrackedPath` parameter automatically gets a companion `--<param>-origin`
flag. This sets the origin for that one parameter explicitly:

```bash
euler-toolbox run sample-dataset \
  --dataset-paths $TMPDIR/rgb.zip \
  --dataset-paths-origin /scratch/project/rgb.zip \
  --dataset-paths $TMPDIR/depth.zip \
  --dataset-paths-origin /scratch/project/depth.zip
```

For list parameters (like `--dataset-paths` above), the origins are matched
**positionally**: the first `--dataset-paths-origin` applies to the first
`--dataset-paths`, the second to the second, etc.

#### 2. Global `--origin-map` prefix rewrite (medium priority)

When all your paths follow the same copy pattern (everything under `$TMPDIR`
was copied from `/scratch/project`), a single `--origin-map` flag rewrites
all of them at once:

```bash
euler-toolbox run sample-dataset \
  --origin-map '$TMPDIR=/scratch/project' \
  --dataset-paths $TMPDIR/rgb.zip \
  --dataset-paths $TMPDIR/depth.zip \
  --dataset-paths $TMPDIR/segmentation.zip
```

The rule `$TMPDIR=/scratch/project` means: for any working path that starts
with the expanded value of `$TMPDIR`, replace that prefix with
`/scratch/project`. Environment variables are expanded on both sides.

Multiple rules can be comma-separated:

```bash
--origin-map '$TMPDIR=/scratch/project,/fast=/archive'
```

First matching prefix wins.

#### 3. Fallback: origin = working (lowest priority)

If neither `--<param>-origin` nor `--origin-map` matches a path, the origin
defaults to the working path itself. This means tools work identically in
non-HPC contexts where there is no copy step -- you just pass paths normally
and don't think about origins at all.

### Priority resolution example

```bash
euler-toolbox run sample-dataset \
  --origin-map '$TMPDIR=/scratch/project' \
  --dataset-paths $TMPDIR/rgb.zip \
  --dataset-paths-origin /archive/special/rgb.zip \
  --dataset-paths $TMPDIR/depth.zip \
  --dataset-paths $TMPDIR/seg.zip
```

| Path | Origin | Why |
|---|---|---|
| `$TMPDIR/rgb.zip` | `/archive/special/rgb.zip` | Explicit `--dataset-paths-origin` (priority 1) |
| `$TMPDIR/depth.zip` | `/scratch/project/depth.zip` | `--origin-map` prefix match (priority 2) |
| `$TMPDIR/seg.zip` | `/scratch/project/seg.zip` | `--origin-map` prefix match (priority 2) |



### What the tool sees

Inside the tool function, every `TrackedPath` parameter is a simple object:

```python
def my_tool(data: TrackedPath = ToolParam(...)):
    data.working   # Path -- the local/fast path, use for I/O
    data.origin    # Path -- the canonical path, use for logging
    str(data)      # returns str(data.working)
    os.fspath(data)  # returns str(data.working)
```

For `list[TrackedPath]` parameters, the tool receives a `list` of these objects.

```sh
euler-toolbox run split-ds \
  --source-paths /tmp/rgb.zip \
  --source-paths /tmp/depth.zip \
  --source-paths /tmp/seg.zip \
  --source-paths-origin /scratch/rgb.zip \
  --source-paths-origin /scratch/depth.zip
```

| Index | `--source-paths` | `--source-paths-origin` | Resolved Origin |
|-------|------------------|-------------------------|-----------------|
| 0 | `/tmp/rgb.zip` | `/scratch/rgb.zip` | `/scratch/rgb.zip` (explicit) |
| 1 | `/tmp/depth.zip` | `/scratch/depth.zip` | `/scratch/depth.zip` (explicit) |
| 2 | `/tmp/seg.zip` | (none) | Falls through to `--origin-map`, then fallback |

---

## Schema output

The `schema` command outputs structured JSON that a pipeline orchestrator can
parse to discover a tool's interface and build invocation commands
programmatically.

### Example: `euler-toolbox schema sample-dataset --format template`

```json
{
  "tool": "sample-dataset",
  "description": "Subsample the first dataset and index-match the rest.",
  "params": [
    {
      "name": "dataset_paths",
      "cli_name": "--dataset-paths",
      "type": "list[tracked_path]",
      "required": true,
      "help": "Dataset archives. The first is subsampled; the rest are index-matched against it.",
      "placeholder": "{{dataset_path}}",
      "origin_placeholder": "{{dataset_origin}}",
      "note": "Repeat --dataset-paths for each path. ..."
    },
    {
      "name": "sample_rate",
      "cli_name": "--sample-rate",
      "type": "int",
      "required": false,
      "default": 3,
      "placeholder": "{{sample_cfg:1}}"
    }
  ],
  "global_options": {
    "origin_map": {
      "placeholder": "{{origin_map:1}}",
      "format": "<local_prefix>=<real_prefix>[,...]"
    },
    "log_level": { "default": "INFO", "choices": ["DEBUG","INFO","WARNING","ERROR"] }
  },
  "template": "euler-toolbox run sample-dataset \\\n  --dataset-paths '{{dataset_path}}' [...]  ..."
}
```

### Placeholder convention

Placeholders use the format `<semantic_group>:<index>`:

- `dataset_path` -- a dataset path (repeat for multiple)
- `dataset_origin` -- auto-derived origin counterpart
- `sample_cfg:1` -- first configuration value in the `sample_cfg` group
- `origin_map:1` -- the origin-map string

An orchestrator parses the schema, fills in placeholders with real values, and
executes the resulting command.

### Pipeline usage over SSH

```bash
# 1. Discover tools
ssh cluster euler-toolbox list

# 2. Fetch the contract
schema=$(ssh cluster euler-toolbox schema sample-dataset --format template)

# 3. Extract template, fill placeholders, execute
cmd=$(echo "$schema" | jq -r '.template' \
  | sed 's|{{dataset_path}}|/data/rgb.zip|' ...)
ssh cluster $cmd
```

---

## Available tools

### `sample-dataset`

Subsample the first dataset (every Nth file) and index-match all subsequent
datasets against it.

```bash
euler-toolbox run sample-dataset \
  --dataset-paths /data/rgb.zip \
  --dataset-paths /data/depth.zip \
  --dataset-paths /data/segmentation.zip \
  --sample-rate 3 \
  --output-suffix _8k
```

| Parameter | Type | Default | Description |
|---|---|---|---|
| `--dataset-paths` | `PATH` (repeatable) | *required* | Archives to process. First is subsampled; rest are index-matched. |
| `--sample-rate` | `INT` | `3` | Take every Nth file from the primary dataset. |
| `--output-suffix` | `TEXT` | `_8k` | Suffix appended to output archive names. |

### `split-ds`

Split datasets into train/val/test archives by common IDs.

```bash
euler-toolbox run split-ds \
  --source-paths /data/rgb_clear \
  --source-paths /data/foggy \
  --source-paths /data/depth \
  --ratios 80 --ratios 10 --ratios 10
```

| Parameter | Type | Default | Description |
|---|---|---|---|
| `--source-paths` | `PATH` (repeatable) | *required* | Dataset directories or archives to split. |
| `--suffixes` | `TEXT` (repeatable) | `train.zip val.zip test.zip` | Output suffixes for each split. |
| `--ratios` | `INT` (repeatable) | `80 10 10` | Split ratios (must sum to 100). |

---

## Adding a new tool

Create a file in `euler_toolbox/tools/` -- it will be auto-discovered:

```python
# euler_toolbox/tools/my_tool.py
from euler_toolbox.registry import ToolParam, tool
from euler_toolbox.types import TrackedPath

@tool(name="my-tool", description="Does something useful.")
def my_tool(
    input_path: TrackedPath = ToolParam(
        help="Input dataset.",
        placeholder="input_path:1",
    ),
    threshold: float = ToolParam(
        default=0.5,
        help="Detection threshold.",
        placeholder="config:1",
    ),
) -> None:
    # input_path.working -> fast local path (use for I/O)
    # input_path.origin  -> canonical path (use for logging)
    ...
```

No other files need editing. The tool immediately appears in `list`, `schema`,
and `run`.

### Parameter types

| Python type | CLI behavior |
|---|---|
| `TrackedPath` | Single `--flag PATH` + auto-generated `--flag-origin PATH` |
| `list[TrackedPath]` | Repeatable `--flag PATH` + repeatable `--flag-origin PATH` (positional match) |
| `str` | `--flag TEXT` |
| `int` | `--flag INTEGER` |
| `float` | `--flag FLOAT` |
| `list[str]` | Repeatable `--flag TEXT` |
| `list[int]` | Repeatable `--flag INTEGER` |

### ToolParam

```python
ToolParam(
    default=...,       # omit or ... for required; any value for optional
    help="...",        # shown in --help and schema output
    placeholder="...", # e.g. "dataset_path:1" -- used in schema placeholders
)
```
