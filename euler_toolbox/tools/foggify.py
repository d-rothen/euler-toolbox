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

_HETERO_BLOCK_SCHEMA = {
    "type": "object",
    "help": "Perlin FBM spatial-variation parameters.",
    "properties": {
        "scales": {"type": "string|list[int]", "help": "'auto' or explicit list of power-of-2 scales."},
        "min_scale": {"type": "integer", "help": "Minimum Perlin noise scale."},
        "max_scale": {"type": "integer|null", "help": "Maximum scale; null = max(H, W)."},
        "min_factor": {"type": "float", "help": "Lower bound of the multiplicative factor."},
        "max_factor": {"type": "float", "help": "Upper bound of the multiplicative factor."},
        "normalize_to_mean": {"type": "boolean", "help": "Rescale factor field so spatial mean = 1."},
    },
}

_FOG_CONFIG_SCHEMA: dict = {
    "description": (
        "Fog simulation parameters.  Passed as a JSON file via --fog-config."
    ),
    "default": {
        "seed": 1337,
        "depth_scale": 1.0,
        "resize_depth": True,
        "contrast_threshold": 0.05,
        "device": "cpu",
        "gpu_batch_size": 4,
        "selection": {
            "mode": "weighted",
            "weights": {
                "uniform": 1.0,
                "heterogeneous_k": 0.0,
                "heterogeneous_ls": 0.0,
                "heterogeneous_k_ls": 0.0,
            },
        },
        "models": {
            "uniform": {
                "visibility_m": {
                    "dist": "normal",
                    "mean": 300.0,
                    "std": 100.0,
                    "min": 100.0,
                },
                "atmospheric_light": "from_sky",
            },
            "heterogeneous_k": {
                "visibility_m": {"dist": "constant", "value": 80.0},
                "atmospheric_light": "from_sky",
                "k_hetero": {
                    "scales": "auto",
                    "min_scale": 2,
                    "max_scale": None,
                    "min_factor": 0.0,
                    "max_factor": 1.0,
                    "normalize_to_mean": True,
                },
            },
            "heterogeneous_ls": {
                "visibility_m": {"dist": "constant", "value": 80.0},
                "atmospheric_light": "from_sky",
                "ls_hetero": {
                    "scales": "auto",
                    "min_scale": 2,
                    "max_scale": None,
                    "min_factor": 0.0,
                    "max_factor": 1.0,
                    "normalize_to_mean": False,
                },
            },
            "heterogeneous_k_ls": {
                "visibility_m": {"dist": "constant", "value": 80.0},
                "atmospheric_light": "from_sky",
                "k_hetero": {
                    "scales": "auto",
                    "min_scale": 2,
                    "max_scale": None,
                    "min_factor": 0.0,
                    "max_factor": 1.0,
                    "normalize_to_mean": True,
                },
                "ls_hetero": {
                    "scales": "auto",
                    "min_scale": 2,
                    "max_scale": None,
                    "min_factor": 0.0,
                    "max_factor": 1.0,
                    "normalize_to_mean": False,
                },
            },
        },
    },
    "properties": {
        "seed": {
            "type": "integer|null",
            "help": "RNG seed for reproducibility. null = non-deterministic.",
        },
        "depth_scale": {
            "type": "float",
            "help": "Multiplier applied to depth values after loading.",
        },
        "resize_depth": {
            "type": "boolean",
            "help": "Resize depth map to match RGB resolution.",
        },
        "contrast_threshold": {
            "type": "float",
            "help": "Visibility-to-attenuation threshold C_t: k = -ln(C_t) / V.",
        },
        "device": {
            "type": "string",
            "help": "Compute device: cpu, cuda, mps.",
        },
        "gpu_batch_size": {
            "type": "integer",
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
                        "k_hetero": {**_HETERO_BLOCK_SCHEMA, "help": "Spatially-varying attenuation (optional)."},
                        "ls_hetero": {**_HETERO_BLOCK_SCHEMA, "help": "Spatially-varying airlight (optional)."},
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
