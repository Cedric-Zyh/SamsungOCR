"""Stable review tokens shared by production and sample projections."""
import hashlib
import json


def review_revision(result):
    evidence = {key: value for key, value in result.items()
                if key not in {"review_revision", "ground_truth_saved"}}
    return hashlib.sha256(json.dumps(evidence, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def with_review_revision(result):
    return {**result, "review_revision": review_revision(result)}
