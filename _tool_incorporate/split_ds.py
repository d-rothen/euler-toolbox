
import os

from ds_crawler import split_datasets

#TODO: CLI 
tmpdir = os.environ["TMPDIR"]
dadasets_in_basedir = ["rgb_clear", "radial_foggy_10", "depth_10"]


datasets = [os.path.join(tmpdir, ds) for ds in dadasets_in_basedir]

result = split_datasets(
    source_paths=datasets,
    suffixes=["train.zip", "val.zip", "test.zip"],
    ratios=[80, 10, 10]
)

print(f"Common IDs: {len(result['common_ids'])}")
for src in result["per_source"]:
    print(f"\n{src['source']}:")
    print(f"  total: {src['total_ids']}, excluded: {src['excluded_ids']}")
    for s in src["splits"]:
        print(f"  {s['suffix']}: {s['num_ids']} IDs, {s['copied']} copied")
