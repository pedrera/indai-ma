# indAI MA agent guide

Before changing code, read the relevant [project context](docs/PROJECT_CONTEXT.md),
[architecture](docs/ARCHITECTURE.md), [decisions](docs/DECISIONS.md), and
[roadmap](docs/ROADMAP.md). Treat the repository and tests as authoritative.

Keep the deterministic-first design: RAG retrieves grounded facts, deterministic
tools calculate, optional LLMs interpret, specialists execute their own goals, and
Supervisor owns routing and cross-specialist synthesis. Specialists must not call
each other. Preserve provenance, distinguish retrieved from calculated values, and
keep document text as data rather than executable instructions.

Do not expose private chain-of-thought. Expose evidence, actions, tool results and
trace metadata. Preserve compatible behavior unless an explicit architectural
decision changes it. Do not add functionality without a reliability, security,
operability, evaluation, integration, or customer-value reason.

Validation commands:

```powershell
python -m pytest
python -m evals --mode fast
```

The fast evaluation is deterministic/offline and must not invoke a real generation
LLM. Do not commit, tag, or push unless the user explicitly requests it.
