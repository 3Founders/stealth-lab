from huggingface_hub import hf_hub_download
path = hf_hub_download("DavydenkoGr/AFTER", filename="tasks/de/debug-parquet-partitioning/instruction.md", repo_type="dataset")
print(open(path, encoding="utf-8").read()[:1500])