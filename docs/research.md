# The research behind Twin Me Up

Twin Me Up started as a question: can an AI system learn how a particular person works well
enough to brief, check and constrain the agents acting for them? Before writing any code we
reviewed the primary research on "digital twins" of people, expertise transfer and AI
assistance, plus regulator guidance on workplace AI. This page summarises what that evidence
does and does not support, and how each finding shaped the design.

Sources were checked in October 2026. Numbers are quoted from the original papers; follow the
links to read them in context. Where a result comes from a single study or setting, we say so.

## The short version

- **Simulating someone's survey answers is promising but limited.** The best published agents
  reach about 83% of a person's own consistency, which is not 83% accuracy.
- **Predicting a specific person's new choices is much harder.** In one validation study,
  interview-informed agents matched individual answers 30% of the time against 45% for the
  same people answering twice.
- **Experts leave out much of what they know unless they are asked the right questions.**
  Structured probing recovers substantially more.
- **AI assistance can raise productivity, and it can also harm learning** when the assistance
  is removed. Measure what people can do without the tool, not only with it.
- **Regulators draw hard lines** around workplace monitoring, consent and emotion inference.

So Twin Me Up does not claim to reproduce a person. It records the guidance a person has
confirmed, tests whether that guidance helps on decisions it has not seen, and keeps authority
as something the person grants explicitly rather than something a model predicts.

## 1. The paper that prompted the project

**Mindful Human Digital Twins: Integrating Theory of Mind with Multi-Agent Reinforcement
Learning** (Zhinin-Vera and colleagues) proposes combining Theory-Theory and Simulation-Theory
so that agents can model other agents' intentions and adapt in multi-agent environments.[^1]

Its experiments use synthetic agents in a simulated environment. The authors chose simulation
deliberately, because testing the scenarios on real people (children, in their case) would
expose them to harm.[^1] The paper does not train a twin of an identified person from their
working history, and it does not show that such a twin can safely make decisions for anyone.

**What we took from it:** the idea that an assistant benefits from an explicit model of the
person it works with. **What we did not take:** any claim that a system can read a person's
mental state. "Theory of mind" in this project means written, inspectable guidance, nothing
more.

## 2. Four different capabilities, four different levels of evidence

"Digital twin of a person" covers several capabilities with very different evidence behind
them. Separating them was the most useful outcome of the review.

### A. Replicating survey answers: promising, limited

Park and colleagues built agents for 1,052 people from two-hour interviews, structured surveys
or both.[^2] On held-out General Social Survey items, interview-only agents reached **65.67%
raw accuracy**, against **79.53% test-retest consistency** for the people themselves.[^2] The
headline figure of 83% is the ratio between the two, a normalised score, not raw accuracy.[^2]
Agents built from interviews plus surveys reached 86% of the human consistency benchmark.[^2]

The revised version of the paper removed 27 survey items that overlapped with the training
material, which is why we cite it rather than the earlier "85% accurate" headline.[^2] Its
experimental replications were explicitly underpowered for comparing effect sizes.[^2] None of
this tests workplace judgement.

### B. Predicting a particular person's new choices: much harder

A 2026 product-discovery study built interview-informed agents for 51 people.[^3] On
categorical questions the agents achieved **0.300 exact item accuracy**; the same people
answering again achieved **0.446**.[^3] The authors conclude the agents were "imprecise on an
individual level" even with the full interview transcript.[^3]

This is one study in one setting, not a universal ceiling. It is still the clearest warning in
the literature: a twin that sounds right in conversation can be wrong about the decision.

### C. Transferring expertise: valuable adjacent evidence

- A field study of customer-support work found AI assistance raised productivity by **15% on
  average**, with large differences between workers.[^4] That supports AI-assisted knowledge
  work in general, not faithful replicas of individuals.
- Cognitive task analysis research shows experts leave steps out when describing their own
  work. In a study of experts teaching a surgical procedure, they described **44%** of the
  required steps unprompted and **66%** when prompted.[^5] The lesson is methodological: observe real decisions, probe what
  was omitted, verify the result.

### D. Coaching and learning: assistance is not retention

- A randomised trial in a large undergraduate physics course (N = 194) found advantages for a
  carefully designed AI tutor over the tested active-learning setting.[^6]
- A high-school maths field experiment found an unrestricted GPT-based assistant improved
  assisted practice but **harmed performance once access was removed**: unassisted results
  were 17% below control. A tutor built with learning guardrails did not show that harm.[^7]

The implication for any system that claims to transfer expertise: measure what the person can
do after the assistance is withdrawn.

### What this adds up to

| Capability | Evidence | How Twin Me Up treats it |
|---|---|---|
| Source-grounded professional memory | Practical, well understood | Core feature |
| Structured capture of expertise | Credible, needs per-workflow testing | Core feature, with probes |
| Predicting a person's choices | Uncertain outside the tested domain | Measured on held-out cases, never assumed |
| Autonomous replacement of a person | Not demonstrated | Out of scope by design |

## 3. How the findings shaped the design

| Finding | Design decision |
|---|---|
| Individual-level prediction is imprecise (0.300 against 0.446)[^3] | Authority is never inferred. Agents act only on explicit, scoped, expiring grants. Everything else returns `ask`. |
| Headline "twin accuracy" figures are easy to misread[^2] | Evaluations report agreement as counts and rates with sample-size warnings, and compare against two baselines rather than reporting a single score. |
| Experts omit steps unless probed[^5] | Onboarding probes ask for exceptions, reversals and red lines. `twin_next_questions` asks the most valuable follow-up about each recorded decision. |
| A model's explanation is not the person's reasoning | Machine-inferred guidance stays a candidate until the person confirms it. Every lesson keeps its origin, sources and review history. |
| Unrestricted assistance can harm unassisted performance[^7] | The pilot guide measures what a colleague or agent can do with the twin's handoff, and recommends testing without it too. |
| Assistance helps on average but varies widely by person[^4] | Every workflow carries its own track record. Nothing is promoted on the strength of another workflow's results. |
| Pre-deployment testing, provenance and incident handling are core governance practices[^8] | Held-out evaluation sets that freeze, provenance on every record and a tamper-evident audit chain. |
| Consent is rarely a sound basis for workplace monitoring[^9] | No covert collection. The person owns the twin, runs it locally and decides what it records. |
| Workplace emotion inference is prohibited under the EU AI Act, subject to narrow exceptions[^10] | No emotion, personality or sentiment modelling of anyone. |

## 4. The landscape

Conversational likenesses, knowledge-capture tools for offboarding, AI coaching products and
permission-aware enterprise assistants all exist commercially. Personal memory and
permission-aware retrieval are therefore not novel on their own.

What we did not find in one place was the combination Twin Me Up aims for: learning that the
person ratifies, competence measured on held-out decisions against simple baselines, and
authority that is enforced outside the model and portable across every agent the person uses.
That is a design choice, not a claim that nobody else could build it.

## 5. Ethics and law: the lines we drew

These follow from the guidance above. They are not legal advice, and a workplace deployment
needs its own assessment of lawful basis, notices, minimisation and whether an impact
assessment is required.

- **The person owns their twin.** It runs on their machine, under their control. There is no
  telemetry and no hosted service.
- **No covert recording, no monitoring of others.** The twin learns from what its owner
  chooses to record.
- **No emotion, personality or sentiment inference.** Ever.
- **Plain disclosure.** Briefs and handoffs are clearly the twin's output, not a fresh
  statement from the person.
- **Human-ratified learning.** Inferred guidance has no authority until confirmed, and can be
  revised, retired or erased with everything derived from it.
- **No self-expanding authority.** Agents cannot grant themselves actions, and actions under
  the reserved `twin.` namespace can never be granted to anyone.
- **Separate the person from the organisation.** If you deploy this at work, decide in advance
  who can access, export, amend and revoke a twin, and what happens when someone leaves. An
  employer should not acquire a right to impersonate an employee by default.

Technical checks are not governance sign-off or legal clearance.

## Sources

[^1]: Zhinin-Vera et al., *Mindful Human Digital Twins: Integrating Theory of Mind with
    Multi-Agent Reinforcement Learning*, university-hosted manuscript.
    https://ruidera.uclm.es/server/api/core/bitstreams/aad8fedf-3f28-4b9e-b57a-9c15eea98135/content

[^2]: Park et al., *LLM Agents Grounded in Self-Reports Enable General-Purpose Simulation of
    Individuals*, arXiv:2411.10109v3. "calculated from a raw accuracy of 65.67% (std = 6.51)
    divided by participants' self-consistency of 79.53% (std = 8.65)."
    https://arxiv.org/pdf/2411.10109v3

[^3]: *Interview-Informed Generative Agents for Product Discovery: A Validation Study*,
    arXiv:2603.29890v1. "These results suggest that the agents are imprecise on an individual
    level..." https://arxiv.org/html/2603.29890v1

[^4]: Brynjolfsson, Li and Raymond, *Generative AI at Work*, arXiv:2304.11771v2. "Access to AI
    assistance increases worker productivity, as measured by issues resolved per hour, by 15%
    on average, with substantial heterogeneity across workers." https://arxiv.org/pdf/2304.11771v2

[^5]: *The use of cognitive task analysis to reveal the instructional limitations of experts
    in the teaching of procedural skills*, PubMed 24667500. "The
    average number of steps that were described increased from 44% (20/46) when unprompted to
    66% (31/46) when prompted." https://pubmed.ncbi.nlm.nih.gov/24667500

[^6]: *AI tutoring outperforms in-class active learning: an RCT introducing a novel
    research-based design in an authentic educational setting*, Scientific Reports (2025). "We
    conducted a randomized controlled experiment in a large undergraduate physics course
    (N = 194)." https://www.nature.com/articles/s41598-025-97652-6

[^7]: Bastani et al., *Generative AI without guardrails can harm learning*, PNAS. "this
    negative effect is essentially eradicated in the GPT Tutor arm, though we still do not
    observe a positive effect." https://pmc.ncbi.nlm.nih.gov/articles/PMC12232635

[^8]: NIST AI 600-1, *Artificial Intelligence Risk Management Framework: Generative AI
    Profile*. "Governance, Content Provenance, Pre-deployment Testing, and Incident
    Disclosure." https://nvlpubs.nist.gov/nistpubs/ai/NIST.AI.600-1.pdf

[^9]: UK Information Commissioner's Office, *Data protection and monitoring workers*. "This
    means that consent is not usually appropriate in the employment context, due to the
    imbalance of power between you and your workers."
    https://ico.org.uk/for-organisations/uk-gdpr-guidance-and-resources/employment/monitoring-workers/data-protection-and-monitoring-workers

[^10]: Regulation (EU) 2024/1689 (the AI Act), consolidated text of 27 July 2026, Article 5:
    prohibits "the use of AI systems to infer emotions of a natural person in the areas of
    workplace and education institutions, except where the use of the AI system is intended
    to be put in place or into the market for medical or safety reasons".
    https://eur-lex.europa.eu/legal-content/EN/TXT/HTML/?uri=CELEX:02024R1689-20260727
