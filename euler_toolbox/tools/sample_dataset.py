"""Subsample datasets across modalities.

The first dataset is subsampled (every Nth file); all subsequent datasets
are index-matched against it and copied in the same order.
"""

from __future__ import annotations

import logging

from euler_toolbox.registry import ToolParam, tool
from euler_toolbox.types import TrackedPath

log = logging.getLogger(__name__)


def _output_path(zip_path: str, suffix: str) -> str:
    assert zip_path.endswith(".zip"), f"Expected a .zip path, got: {zip_path}"
    return zip_path[: -len(".zip")] + suffix + ".zip"


@tool(
    name="sample-dataset",
    description="Subsample the first dataset and index-match the rest.",
)
def sample_dataset(
    dataset_paths: list[TrackedPath] = ToolParam(
        help="Dataset archives. The first is subsampled; the rest are index-matched against it.",
        placeholder="dataset_path",
    ),
    sample_rate: int = ToolParam(
        default=3,
        help="Take every Nth file from the primary (first) dataset.",
        placeholder="sample_cfg:1",
    ),
    output_suffix: str = ToolParam(
        default="_8k",
        help="Suffix appended to output archive names.",
        placeholder="sample_cfg:2",
    ),
) -> None:
    from ds_crawler.parser import copy_dataset, get_files, index_dataset_from_path

    if not dataset_paths:
        raise SystemExit("At least one --dataset-path is required.")

    primary = dataset_paths[0]

    # 1) Subsample the primary dataset.
    log.info("Indexing primary dataset: %s (origin: %s)", primary.working, primary.origin)
    primary_index = index_dataset_from_path(
        str(primary.working),
        strict=False,
        sample=sample_rate,
        save_index=False,
    )
    log.info("Primary index contains %d files.", len(get_files(primary_index)))

    primary_out = _output_path(str(primary.working), output_suffix)
    log.info("Copying primary subset -> %s", primary_out)
    copy_dataset(str(primary.working), primary_out, index=primary_index)

    # 2) Index-match and copy every subsequent dataset.
    for i, tp in enumerate(dataset_paths[1:], start=2):
        log.info("Indexing dataset %d: %s (origin: %s)", i, tp.working, tp.origin)
        idx = index_dataset_from_path(
            str(tp.working),
            strict=False,
            save_index=False,
            match_index=primary_index,
        )
        log.info("Dataset %d index contains %d files.", i, len(get_files(idx)))

        out = _output_path(str(tp.working), output_suffix)
        log.info("Copying dataset %d subset -> %s", i, out)
        copy_dataset(str(tp.working), out, index=idx)

    log.info("All done — processed %d dataset(s).", len(dataset_paths))
