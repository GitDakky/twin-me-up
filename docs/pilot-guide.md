# Run a six-week pilot

A twin is worth keeping only if it makes real work better than a strong, simple alternative.
This guide tests that for one workflow over six weeks. It follows the design in
[the research review](research.md): bounded scope, held-out evaluation, two baselines and
authority that you grant explicitly.

## Choose the workflow

Pick one recurring piece of work where your judgement matters and the cost of a wrong call is
contained, for example reviewing supplier quotes, triaging inbound requests or preparing a
weekly priority list.

Keep these out of scope at first: committing money, legal advice, decisions about people, and
anything sent outside your organisation. Start with agents that draft and read, not act.

## Weeks 1 and 2: capture

```bash
twinmeup probes onboarding
```

- Answer the onboarding questions and record your boundaries and escalation rules as confirmed
  lessons (`--kind boundary` or `--kind escalation`, `--origin written_rule`, `--confirm`).
- Record ten to twenty real decisions, with the options you rejected and why. Let agents call
  `twin_record_decision`, then use `twinmeup episode gaps` and `twin_next_questions` to fill in
  what the record does not explain.
- Before tuning anything, set aside held-out decisions and freeze them:

```bash
twinmeup eval create --name pilot-holdout --workflow <workflow>
twinmeup eval from-episode <set-id> <episode-id>      # repeat for each held-out decision
twinmeup eval freeze <set-id>
```

Aim for at least 30 held-out cases by the end of the pilot. Fewer than that, and the reports
will warn you that the results cannot distinguish the arms.

## Weeks 3 and 4: shadow

- Agents work as normal, briefed by the twin, with no new authority.
- Every time you edit, override or reject their work, they record a correction. Review the
  lesson queue twice a week and confirm, revise or reject.
- Run the evaluation arms against the frozen set. The model never sees your held-out answers.

```bash
for arm in generic rules twin; do
  twinmeup --actor agent:eval eval run <set-id> --arm $arm --responder openrouter:<model>
done
```

## Weeks 5 and 6: hand off

- Use `twinmeup handoff` to brief a colleague or a second agent on new cases.
- Measure whether the receiving person or agent produces better work with the handoff than
  without it. Test some cases without the twin as well: assistance that helps in the moment can
  leave people worse off once it is removed.

## What to measure

| Measure | Question |
|---|---|
| Knowledge | Are the facts and sources in briefs correct? |
| Judgement | Does the twin agree with your held-out decisions, and were those decisions good? Agreement with you is not automatically the best decision. |
| Calibration | Does it ask or escalate when it should? |
| Delegation | How many drafts did you accept, and how long did corrections take? |
| Transfer | How well does a colleague perform without the twin present? |
| Safety | Any unauthorised action, disclosure, stale rule or successful instruction injection? |
| Value | Time saved after onboarding, reviewing, correcting and maintaining the twin. |

Agree the acceptance criteria before you look at the results.

## The decision at the end

Keep going, and consider granting more authority, only if all of these hold:

- the `twin` arm beats the `rules` arm on the held-out set;
- you trust the learned guidance and can correct it easily;
- the time saved exceeds the time spent maintaining it.

Stop or simplify if only the tone feels better, if maintenance eats the savings, or if the
workflow keeps needing invisible rescue. `twin_track_record` summarises the evidence for each
workflow; a stage of `supervised` means the evidence supports asking you about more authority,
never that the twin has it.
