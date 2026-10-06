# 07: Shared context + runtime team evolution

A **Security Reviewer** joins the DevTeam at runtime (HOT, `team_evolve`) and
publishes findings into a versioned shared context, `security-review`. The
Developer's code bodies and the Tester's oracles declare
`@llm(..., context=['security-review'])`, so they see the findings in their
prompts, each pinned to a version.

```
Analyst --Req2Arch--> Architect --Arch2Code--> Developer
   '-----------------Req2Test--------------> Tester
Architect --Arch2Sec (HOT)--> SecurityReviewer
SecurityReviewer --context_update--> security-review vN --> Developer, Tester
```

| Mechanism | What to look for |
|---|---|
| Transformation vs. collaboration | Hand-offs still create every element deterministically; the context only adds knowledge to the prompts of bindings that declare it. |
| Context in the version stamp | Step 4: publishing v2 obliges exactly the 5 consuming bindings. Signatures, notes and risk ratings stay fresh. |
| Same obligation machinery | Step 7: `impact` lists the new version's obligations like any source change. |
| Traceable influence | Step 6: `influence_query` lists every accepted value that pinned a finding, and at which version. |
| Observable information flow | Step 9: `context.read` events record who read which version (digests only, never content). |

```
python examples/07_shared_context/run.py --llm mock
```

Template pieces: `src/agentm2m/templates/devteam/rules/extra/{Arch2Code,Req2Test}.ctx.agentm2m`
and `shared-context.yaml`.
