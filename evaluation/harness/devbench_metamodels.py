"""The DevTeam Req/Arch/Code/Test view metamodels (examples/01_devteam),
duplicated here rather than imported -- same convention as
evaluation/harness/agentm2m_config.py, which keeps evaluation/ independent
of examples/'s per-directory `sys.path` / same-module-name workaround.

Shapes are identical to examples/01_devteam/metamodels.py; only the seed
model differs (seeded from a real DevBenchTask instead of the toy
payments/notifications backlog).
"""
from __future__ import annotations

from agentm2m.metamodel import MetamodelBuilder

from .devbench_loader import DevBenchTask, criterion_text_for


def build_req_mm() -> MetamodelBuilder:
    b = MetamodelBuilder("Req", "http://agentm2m/eval/devbench/req")
    epic = b.eclass("Epic")
    b.attribute(epic, "name")

    criterion = b.eclass("Criterion")
    b.attribute(criterion, "id")
    b.attribute(criterion, "text")

    story = b.eclass("UserStory")
    b.attribute(story, "id")
    b.attribute(story, "status")
    b.reference(story, "epic", "Epic", many=False, containment=False)
    b.reference(story, "criteria", "Criterion", many=True, containment=True)

    root = b.eclass("ReqModel")
    b.add_root_slot(root, "epics", "Epic")
    b.add_root_slot(root, "stories", "UserStory")
    return b


def build_arch_mm() -> MetamodelBuilder:
    b = MetamodelBuilder("Arch", "http://agentm2m/eval/devbench/arch")
    component = b.eclass("Component")
    b.attribute(component, "name")

    operation = b.eclass("Operation")
    # `id` (qualified "Component::realName", propagated structurally from
    # the source UserStory.id) is the trace/element_key identity; `name`
    # is the *real* symbol name DevBench's tests import, which is only
    # unique within a component (e.g. multiple classes each define their
    # own `__init__`) -- keeping them separate is what lets element_key
    # (engine/trace.py, prefers `id` over `name`) stay collision-free while
    # `name` still matches the real function/method symbol.
    b.attribute(operation, "id")
    b.attribute(operation, "name")
    b.attribute(operation, "signature")
    b.reference(operation, "component", "Component", many=False, containment=False)

    root = b.eclass("ArchModel")
    b.add_root_slot(root, "components", "Component")
    b.add_root_slot(root, "operations", "Operation")
    return b


def build_code_mm(arch_mm: MetamodelBuilder) -> MetamodelBuilder:
    b = MetamodelBuilder("Code", "http://agentm2m/eval/devbench/code")
    edit = b.eclass("CodeEdit")
    b.attribute(edit, "name")
    b.attribute(edit, "body")
    b.reference(edit, "operation", arch_mm.get("Operation"), many=False, containment=False)

    root = b.eclass("CodeModel")
    b.add_root_slot(root, "edits", "CodeEdit")
    return b


def build_test_mm(req_mm: MetamodelBuilder) -> MetamodelBuilder:
    b = MetamodelBuilder("Test", "http://agentm2m/eval/devbench/test")
    case = b.eclass("TestCase")
    b.attribute(case, "name")
    b.attribute(case, "oracle")
    b.reference(case, "criterion", req_mm.get("Criterion"), many=False, containment=False)

    root = b.eclass("TestModel")
    b.add_root_slot(root, "cases", "TestCase")
    return b


def build_seed_req_model(req_mm: MetamodelBuilder, task: DevBenchTask, *, canary_stories=None):
    """One Epic per repo, one accepted UserStory per real UML operation, one
    Criterion per story embedding the ground-truth symbol name/parameters
    (see devbench_loader.criterion_text_for). `canary_stories` is an optional
    list of `(OperationSpec, criterion_text)` pairs for synthesized canary
    operations (evaluation/harness/canary.py) -- these carry their own
    bespoke criterion text rather than the module's real acceptance
    criteria, since a canary is deliberately unrelated to the real spec."""
    root = req_mm.get("ReqModel")()

    # One Epic per real UML component (not one per repo): Req2Arch's
    # Epic2Component rule maps `Epic -> Component` 1:1 and `component <-
    # s.epic` resolves through *that* trace link, so a single repo-level
    # Epic would collapse every operation into one bogus Arch!Component
    # regardless of which real class (Global_functions, Result, ...) it
    # actually belongs to -- breaking devbench_common.assemble_module's
    # per-component grouping. Epics are keyed by component name so every
    # operation lands under the Epic matching its real component.
    epics: dict[str, object] = {}

    def _epic_for(component_name: str):
        if component_name not in epics:
            e = req_mm.new("Epic", name=component_name)
            epics[component_name] = e
            root.epics.append(e)
        return epics[component_name]

    for op in task.operations:
        qid = f"{op.component}::{op.name}"
        story = req_mm.new("UserStory", id=qid, status="accepted", epic=_epic_for(op.component))
        story.criteria.append(
            req_mm.new("Criterion", id=f"{qid}.C1", text=criterion_text_for(task, op))
        )
        root.stories.append(story)

    for op, text in canary_stories or []:
        qid = f"{op.component}::{op.name}"
        story = req_mm.new("UserStory", id=qid, status="accepted", epic=_epic_for(op.component))
        story.criteria.append(req_mm.new("Criterion", id=f"{qid}.C1", text=text))
        root.stories.append(story)
    return root
