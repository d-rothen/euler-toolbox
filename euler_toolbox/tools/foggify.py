"""Generate foggy versions of datasets using euler-fog.

Wraps ``euler_fog.fog.foggify.Foggify`` — the modality paths are supplied
as CLI arguments (with TrackedPath origin tracking), while the fog
simulation parameters come from a JSON config file whose expected
structure is exposed via ``euler-toolbox schema foggify``.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from euler_toolbox.registry import ToolParam, tool
from euler_toolbox.types import TrackedPath

log = logging.getLogger(__name__)

# Required modalities that euler-fog expects.
_REQUIRED_MODALITIES = {"rgb", "depth", "sky_mask"}

# ---------------------------------------------------------------------------
# Fog-config schema — included verbatim in ``euler-toolbox schema foggify``
# so pipeline tooling knows how to construct the JSON file.
# ---------------------------------------------------------------------------

_FOG_CONFIG_SCHEMA: dict = {
    "description": (
        "Fog simulation parameters.  Passed as a JSON file via --fog-config."
    ),
    "properties": {
        "seed": {
            "type": "integer|null",
            "default": None,
            "help": "RNG seed for reproducibility. null = non-deterministic.",
        },
        "depth_scale": {
            "type": "float",
            "default": 1.0,
            "help": "Multiplier applied to depth values after loading.",
        },
        "resize_depth": {
            "type": "boolean",
            "default": True,
            "help": "Resize depth map to match RGB resolution.",
        },
        "contrast_threshold": {
            "type": "float",
            "default": 0.05,
            "help": "Visibility-to-attenuation threshold C_t: k = -ln(C_t) / V.",
        },
        "device": {
            "type": "string",
            "default": "cpu",
            "help": "Compute device: cpu, cuda, mps.",
        },
        "gpu_batch_size": {
            "type": "integer",
            "default": 4,
            "help": "Batch size for GPU processing.",
        },
        "selection": {
            "type": "object",
            "help": "Model selection strategy.",
            "properties": {
                "mode": {
                    "type": "string",
                    "enum": ["fixed", "weighted"],
                    "help": "fixed = always use one model; weighted = random per image.",
                },
                "model": {
                    "type": "string",
                    "help": "(fixed mode) Name of the model to use.",
                },
                "weights": {
                    "type": "object",
                    "help": "(weighted mode) Model name -> weight mapping.",
                },
            },
        },
        "models": {
            "type": "object",
            "help": "Fog model definitions keyed by model name.",
            "properties": {
                "<model_name>": {
                    "type": "object",
                    "properties": {
                        "visibility_m": {
                            "type": "object",
                            "help": "Visibility distribution.",
                            "properties": {
                                "dist": {
                                    "type": "string",
                                    "enum": [
                                        "constant",
                                        "uniform",
                                        "normal",
                                        "lognormal",
                                        "choice",
                                    ],
                                },
                                "value": {"type": "float", "help": "(constant) fixed value"},
                                "min": {"type": "float", "help": "(uniform/normal) lower bound"},
                                "max": {"type": "float", "help": "(uniform/normal) upper bound"},
                                "mean": {"type": "float", "help": "(normal/lognormal) mean"},
                                "std": {"type": "float", "help": "(normal) std deviation"},
                                "sigma": {"type": "float", "help": "(lognormal) sigma"},
                                "values": {"type": "list[float]", "help": "(choice) candidates"},
                                "weights": {"type": "list[float]", "help": "(choice) weights"},
                            },
                        },
                        "atmospheric_light": {
                            "type": "string|list[float]",
                            "help": "'from_sky' or an [R,G,B] colour value.",
                        },
                        "k_hetero": {
                            "type": "object",
                            "help": "Spatially-varying attenuation (optional).",
                            "properties": {
                                "scales": {"type": "string|list[int]", "default": "auto"},
                                "min_scale": {"type": "integer", "default": 2},
                                "max_scale": {"type": "integer|null", "default": None},
                                "min_factor": {"type": "float", "default": 0.0},
                                "max_factor": {"type": "float", "default": 1.0},
                                "normalize_to_mean": {"type": "boolean", "default": True},
                            },
                        },
                        "ls_hetero": {
                            "type": "object",
                            "help": "Spatially-varying airlight (optional).",
                            "properties": {
                                "scales": {"type": "string|list[int]", "default": "auto"},
                                "min_scale": {"type": "integer", "default": 2},
                                "max_scale": {"type": "integer|null", "default": None},
                                "min_factor": {"type": "float", "default": 0.0},
                                "max_factor": {"type": "float", "default": 1.0},
                                "normalize_to_mean": {"type": "boolean", "default": False},
                            },
                        },
                    },
                },
            },
        },
    },
}


# ---------------------------------------------------------------------------
# Tool
# ---------------------------------------------------------------------------


@tool(
    name="foggify",
    description="Generate foggy dataset versions using physics-based fog simulation.",
    sub_schemas={"fog_config": _FOG_CONFIG_SCHEMA},
)
def foggify(
    fog_config: TrackedPath = ToolParam(
        help="Path to the fog simulation config JSON.",
        placeholder="config.path",
    ),
    output_path: TrackedPath = ToolParam(
        help="Output directory for generated foggy images.",
        placeholder="output.path",
    ),
    modality: list[str] = ToolParam(
        help="Modality mapping as name=path (e.g. rgb=/data/rgb). Required: rgb, depth, sky_mask.",
        placeholder="modality.path[]",
    ),
    hierarchical_modality: list[str] = ToolParam(
        default=[],
        help="Hierarchical modality mapping as name=path (e.g. intrinsics=/data/calib).",
        placeholder="hierarchical_modality.path[]",
    ),
    dataset_name: str = ToolParam(
        default="dataset",
        help="Human-readable dataset name for logging.",
        placeholder="dataset.name",
    ),
) -> None:
    from euler_fog.fog.foggify import Foggify
    from euler_fog.fog.foggify_logging import get_logger, log_dataset_info
    from euler_loading import Modality, MultiModalDataset

    logger = get_logger()

    # --- Parse key=value modality mappings --------------------------------
    modality_paths = _parse_kv_list(modality, "--modality")
    hierarchical_paths = _parse_kv_list(hierarchical_modality, "--hierarchical-modality")

    missing = _REQUIRED_MODALITIES - modality_paths.keys()
    if missing:
        raise SystemExit(
            f"Missing required modalities: {', '.join(sorted(missing))}. "
            f"--modality must include: {', '.join(sorted(_REQUIRED_MODALITIES))}"
        )

    # --- Build dataset ----------------------------------------------------
    modalities = {name: Modality(path) for name, path in modality_paths.items()}
    hierarchical = (
        {name: Modality(path) for name, path in hierarchical_paths.items()}
        if hierarchical_paths
        else None
    )
    dataset = MultiModalDataset(
        modalities=modalities,
        hierarchical_modalities=hierarchical,
    )

    # --- Read fog config to determine device ------------------------------
    fog_config_path = str(fog_config.working)
    with open(fog_config_path, "r", encoding="utf-8") as f:
        fog_cfg = json.load(f)
    device = fog_cfg.get("device", "cpu").lower()
    use_gpu = device not in ("cpu",)

    all_paths = {**modality_paths, **hierarchical_paths}
    log.info("Fog config: %s (origin: %s)", fog_config.working, fog_config.origin)
    log.info("Output: %s (origin: %s)", output_path.working, output_path.origin)
    log_dataset_info(logger, dataset_name, len(dataset), all_paths, use_gpu)

    # --- Run fog generation -----------------------------------------------
    fog = Foggify(
        config_path=fog_config_path,
        out_path=str(output_path.working),
    )
    saved = fog.generate_fog(dataset)
    log.info("Fog generation complete. Generated %d images.", len(saved))


def _parse_kv_list(items: list[str], flag_name: str) -> dict[str, str]:
    """Parse ``["rgb=/data/rgb", "depth=/data/depth"]`` into a dict."""
    result: dict[str, str] = {}
    for item in items:
        if "=" not in item:
            raise SystemExit(
                f"Invalid {flag_name} value: {item!r}. Expected name=path format."
            )
        key, _, value = item.partition("=")
        result[key.strip()] = value.strip()
    return result
