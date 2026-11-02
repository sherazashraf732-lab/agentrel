# agentrel

**Measure how reliable an AI agent is, not just how accurate.**

An agent can score 85% on a benchmark and still fail in production: it answers
differently each time you ask, breaks when the question is rephrased, and is
confidently wrong. `agentrel` measures those properties with the metrics from
[*Towards a Science of AI Agent Reliability*](https://arxiv.org/abs/2602.16666)
(Rabanser, Kapoor, Kirgis, Liu, Utpala, Narayanan; Princeton, ICML 2026), as a
small Python library and CLI you can run on your own agent and in CI.

```
Agent Reliability Report: mock-agent
--------------------------------------------
Tasks: 3   Runs: 15
Accuracy                     60%

Consistency                 0.42
  outcome                   0.15
  resource                  0.70
Predictability (Brier)      0.72
  calibration               0.81
  discrimination (AUROC)    0.50
Robustness                  1.00
  fault                     1.00
  prompt                    1.00
--------------------------------------------
Overall reliability         0.71
```

## Quick start

```bash
git clone https://github.com/sherazashraf732-lab/agentrel
cd agentrel
uv sync
uv run agentrel run examples/mock.yaml        # simulated agent, runs instantly
```

With a real local model (free, via [Ollama](https://ollama.com)):

```bash
ollama pull qwen3:4b
uv run agentrel run examples/ollama-qwen3.yaml
```

Any model [LiteLLM](https://docs.litellm.ai) supports works by changing `model:`
(e.g. `groq/...`, `gemini/...`, `openai/...`; set the provider's API key).

Use it as a CI gate: `agentrel run my.yaml --min-overall 0.7` exits with code 1
when overall reliability drops below the threshold.

## What it measures

All scores are in [0, 1]; higher is more reliable.

| Dimension | Metric | Formula (from the paper) | Question it answers |
|---|---|---|---|
| Consistency | Outcome `C_out` | mean over tasks of (2·p̂ − 1)² | Same task, same result every run? |
| | Resource `C_res` | exp(−mean CV of run time) | Is cost/latency predictable? |
| Predictability | Brier `P_brier` | 1 − mean (c − y)² | Does confidence match reality? (paper's `R_Pred`) |
| | Calibration `P_cal` | 1 − ECE | Does "80% sure" mean 80% right? |
| | Discrimination `P_AUROC` | share of (success, failure) pairs ranked correctly | Is it more confident when right than when wrong? |
| Robustness | Fault `R_fault` | min(Acc_fault / Acc_0, 1) | Survives timeouts and errors in its model/tool calls? |
| | Prompt `R_prompt` | min(Acc_paraphrase / Acc_0, 1) | Survives rephrased instructions? |

Overall = mean of the consistency, predictability and robustness aggregates
(paper Section 3.5). Safety is reported separately in the paper and is not yet
implemented here.

Note that **an agent that is always wrong is perfectly consistent**: consistency
is independent of accuracy, which is the paper's central point.

## How a run works

1. **Baseline**: every task runs `n_runs` times (paper: 5, temperature 0).
2. **Confidence**: after answering, the model rates its own confidence 0-100
   (the paper's post-hoc self-assessment protocol).
3. **Fault runs**: model calls fail at random with `fault_probability`
   (paper: 0.2). Set `max_retries` to measure how much retrying helps.
4. **Prompt runs**: each task's `paraphrases` are run once.
5. Runs are saved as JSONL and the report as JSON in `output_dir`.

The model only ever receives the prompt, never the expected answer.

## Config

```yaml
name: qwen3-4b-local
model: ollama/qwen3:4b      # or "mock:0.8" for a simulated agent
n_runs: 5
fault_probability: 0.2      # optional
max_retries: 0              # optional
output_dir: agentrel-results/qwen3-4b
tasks:
  - id: capital-france
    prompt: What is the capital of France?
    answer: Paris
    paraphrases: ["Which city is France's capital?"]
```

Answers are matched ignoring case, surrounding whitespace and a trailing `.`/`!`.

## Design decisions where the paper is not explicit

Documented so results can be compared and challenged:

- **Resource CV** is computed per task and averaged, using population std; run
  duration is the only resource tracked so far.
- **Predictability** treats each run as a sample; runs without a confidence
  (e.g. crashes) are excluded. Calibration uses 10 equal-width bins.
- **AUROC** follows the paper's strict `>`, so ties score 0 (standard AUROC
  gives 0.5). It is undefined (`n/a`) if all runs succeed or all fail.
- **Robustness** is undefined if baseline accuracy is 0.
- **Crashed runs** count as failures for accuracy and consistency.

## Not yet implemented

- Trajectory consistency (needs tool-call logs from multi-step agents)
- Environment robustness (needs perturbable tool interfaces)
- Safety (compliance and harm severity via an LLM judge)
- HTML report with charts

## Development

```bash
uv sync
uv run pytest        # 100+ tests, no network needed
uv run ruff check .
uv run mypy src
```

Built test-first: every metric is checked against hand-computed values, and a
simulated agent (`MockLLM`) with known behaviour verifies the full pipeline.

## Reference

Rabanser, S., Kapoor, S., Kirgis, P., Liu, K., Utpala, S., & Narayanan, A. (2026).
*Towards a Science of AI Agent Reliability*. ICML 2026.
https://arxiv.org/abs/2602.16666

`agentrel` is an independent implementation and is not affiliated with the authors.

## License

MIT, see [LICENSE](LICENSE).
