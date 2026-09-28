"""models@run.time for the team: keeps Team causally connected to the
running hand-offs and drives them to a fixpoint (Sec III-D).

Cross-hand-off propagation needs no explicit message-passing data
structure: hand-offs are just re-run in registration order, and a
downstream hand-off's own stamp check (Algorithm 1) automatically detects
when an upstream hand-off changed a value its footprint reads. Iterating
to a fixpoint (capped by `max_passes`, escalating rather than looping
forever, mirroring Proposition 3) is enough to let obligations cascade
along the network (Req->Arch->Code->Test) within one team run.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..engine.executor import HandoffReport, acceptance_holds as _handoff_acceptance_holds, run_handoff
from ..engine.helpers_loader import load_helpers
from ..engine.obligations import Obligation, from_handoff_report
from ..llm.base import LLMBackend
from ..rules.ast import Module
from ..rules.parser import parse_module_file
from .model import Team


@dataclass
class TeamRunReport:
    handoff_reports: dict[str, HandoffReport] = field(default_factory=dict)
    obligations: list[Obligation] = field(default_factory=list)
    passes: int = 0

    @property
    def escalations(self):
        return [e for r in self.handoff_reports.values() for e in r.escalations]

    def summary(self) -> str:
        lines = [f"Team run: {self.passes} pass(es)"]
        for name, r in self.handoff_reports.items():
            lines.append(
                f"  {name}: +{len(r.created)} created, -{len(r.deleted)} deleted, "
                f"{len(r.resampled)} resampled, {len(r.escalations)} escalation(s)"
            )
        if self.obligations:
            lines.append(f"  obligations discharged: {len(self.obligations)}")
            for o in self.obligations:
                lines.append(f"    - {o.handoff}: {o.target_key}.{o.binding} -> {o.agent}")
        if self.escalations:
            lines.append(f"  ESCALATIONS: {len(self.escalations)}")
            for e in self.escalations:
                lines.append(f"    ! {e.rule}: {e.target_key}.{e.binding} ({e.reason})")
        return "\n".join(lines)


class TeamRuntime:
    def __init__(
        self,
        team: Team,
        llm: LLMBackend,
        *,
        max_resamples: int = 3,
        temperature: float = 0.2,
        max_passes: int = 5,
    ) -> None:
        self.team = team
        self.llm = llm
        self.max_resamples = max_resamples
        self.temperature = temperature
        self.max_passes = max_passes
        self._modules: dict[str, Module] = {}
        self._last_reports: dict[str, HandoffReport] = {}

    def module_for(self, handoff_name: str) -> Module:
        if handoff_name not in self._modules:
            spec = self.team.handoffs[handoff_name]
            self._modules[handoff_name] = parse_module_file(spec.rule_path)
        return self._modules[handoff_name]

    def run_handoff_once(self, handoff_name: str) -> HandoffReport:
        team = self.team
        spec = team.handoffs[handoff_name]
        module = self.module_for(handoff_name)
        source_roots = {sm.alias: team.roots[sm.mm_name] for sm in module.sources}
        target_root = team.roots[spec.target_mm]
        target_mm = team.views[spec.target_mm]
        trace = team.traces[handoff_name]
        report = run_handoff(
            module,
            source_roots,
            target_root,
            target_mm,
            trace,
            self.llm,
            base_dir=spec.rule_path.parent,
            max_resamples=self.max_resamples,
            temperature=self.temperature,
        )
        self._last_reports[handoff_name] = report
        return report

    def run_to_fixpoint(self) -> TeamRunReport:
        team_report = TeamRunReport()
        for _pass_num in range(self.max_passes):
            team_report.passes += 1
            any_change = False
            for handoff_name, spec in self.team.handoffs.items():
                report = self.run_handoff_once(handoff_name)
                team_report.handoff_reports[handoff_name] = report
                owner = self.team.owner_of(spec.target_mm)
                team_report.obligations.extend(
                    from_handoff_report(handoff_name, report, owning_agent=owner.name if owner else None)
                )
                if report.created or report.deleted or report.resampled:
                    any_change = True
            if not any_change:
                break
        return team_report

    def acceptance_holds(self) -> bool:
        """phi for the whole team: every hand-off's own phi holds."""
        for handoff_name, spec in self.team.handoffs.items():
            module = self.module_for(handoff_name)
            helpers = load_helpers(module.uses, spec.rule_path.parent)
            source_roots = {sm.alias: self.team.roots[sm.mm_name] for sm in module.sources}
            trace = self.team.traces[handoff_name]
            last = self._last_reports.get(handoff_name, HandoffReport(handoff=handoff_name))
            if not _handoff_acceptance_holds(module, source_roots, trace, last, helpers):
                return False
        return True
