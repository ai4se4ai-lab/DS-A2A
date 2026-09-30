"""The Security Reviewer's view metamodel, added at runtime by the RQ3 HOT
(evaluation/harness/hot_reviewer_rq3.py) -- identical in shape to
examples/03_security_reviewer_hot/metamodels.py's build_sec_mm, duplicated
here for the same reason as devbench_metamodels.py.
"""
from __future__ import annotations

from agentm2m.metamodel import MetamodelBuilder


def build_sec_mm(arch_mm: MetamodelBuilder) -> MetamodelBuilder:
    b = MetamodelBuilder("Sec", "http://agentm2m/eval/devbench/sec")
    review = b.eclass("SecurityReview")
    b.attribute(review, "notes")
    b.attribute(review, "risk")
    b.reference(review, "operation", arch_mm.get("Operation"), many=False, containment=False)

    root = b.eclass("SecModel")
    b.add_root_slot(root, "reviews", "SecurityReview")
    return b
