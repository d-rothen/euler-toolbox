"""Split datasets into train/val/test splits by common IDs."""

from __future__ import annotations

import logging

from euler_toolbox.registry import ToolParam, tool
from euler_toolbox.types import TrackedPath

log = logging.getLogger(__name__)


@tool(
    name="split-ds",
    description="Split datasets into train/val/test by common IDs.",
)
def split_ds(
    source_paths: list[TrackedPath] = ToolParam(
        help="Paths to dataset directories or archives to split.",
        placeholder="source_path",
    ),
    suffixes: list[str] = ToolParam(
        default=["train.zip", "val.zip", "test.zip"],
        help="Output suffixes for each split.",
    ),
    ratios: list[int] = ToolParam(
        default=[80, 10, 10],
        help="Split ratios (must sum to 100).",
    ),
) -> None:
    from ds_crawler import split_datasets

    working_paths = [str(sp.working) for sp in source_paths]
    log.info("Splitting %d datasets with ratios %s", len(working_paths), ratios)
    log.info("Origins: %s", [str(sp.origin) for sp in source_paths])

    result = split_datasets(
        source_paths=working_paths,
        suffixes=list(suffixes),
        ratios=list(ratios),
    )

    log.info("Common IDs: %d", len(result["common_ids"]))
    for src in result["per_source"]:
        log.info(
            "%s: total=%d, excluded=%d",
            src["source"],
            src["total_ids"],
            src["excluded_ids"],
        )
        for s in src["splits"]:
            log.info(
                "  %s: %d IDs, %d copied", s["suffix"], s["num_ids"], s["copied"]
            )
