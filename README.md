# system-one-decision-bench

An open, reproducible benchmark and confidence-cascade reference for Jev, a **System One decision model**,
against chat LLMs. Maintained by [Dwayo AI](https://dwayo.ai). Apache-2.0.

The question it answers: for a call that only needs a typed decision (route this ticket, is this message unsafe,
how urgent is this), how do a decision model and a chat model compare on accuracy, calibration, latency and cost,
and how much can a **confidence cascade** save by escalating only the uncertain calls?

## Status, stated plainly

- Jev is a **hosted, early-access** model (text in, typed values and probabilities out). Results are a **snapshot on the run date**, not a lasting property of the model.
- **Real runs:** Jev (`jev-1.13.0`) and the `chat-small` baseline plus Jev > chat-small cascades on banking77, banking77-10 and guardrail. The chat baseline is `claude-haiku-5-5, thinking disabled, max_tokens=128` (not Haiku's default behaviour; see below). All figures are in [results/REPORT.md](results/REPORT.md), including negative results. The mock backends remain for tests and the quickstart only.
- Vendor numbers and our measured numbers are shown side by side in [results/REPORT.md](results/REPORT.md). Jev's list price is $0.042 per million input tokens (output free). Third-party write-ups quote 70-500 ms latency; Jev's own docs give no figure. We report what we measure.

## Quickstart

```bash
make setup                 # venv + deps (Python >= 3.11)
cp .env.example .env       # add TYPESAFE_API_KEY (and a chat provider key for baselines)
make test lint

# no key needed: synthetic backends, results flagged MOCK
.venv/bin/jev-bench run --task lead-qualification --backend mock-jev,mock-chat --limit 300

# real runs
.venv/bin/jev-bench run --task banking77 --backend jev --limit 500
.venv/bin/jev-bench run --task banking77 --backend chat-small --limit 500
.venv/bin/jev-bench cascade --task banking77 --primary jev --fallback chat-small --sweep 0.5:0.99:0.05
.venv/bin/jev-bench report   # regenerates results/REPORT.md, summary.{json,csv}, charts/
```

`jev-bench list` shows backends. Model versions are pinned in [config/models.yaml](config/models.yaml), list prices (with retrieval date) in [config/prices.yaml](config/prices.yaml).

## What is in it

- **`Decider` interface** (`src/jev_bench/core.py`): `decide(question, input) -> Decision` for `YesNo`, `Choice` and `Score` questions. A `Decision` carries value, label, probabilities, latency, cost, resolved model id, raw output and error. Backends: Jev over HTTP (direct, or through a LiteLLM proxy), Anthropic and OpenAI chat models returning JSON, a deterministic `MockDecider`, and `CascadeDecider`.
- **Tasks**: Banking77 intent routing (77 and 10 classes), review star rating (ordered rubric), Aegis 2.0 safety guardrail (yes/no), synthetic lead qualification. Results and each dataset's source and license are published for the Banking77 and Aegis 2.0 tasks only, in [results/REPORT.md](results/REPORT.md) and the attribution section below; review star rating and synthetic lead qualification are in the code but have no published results here.
- **Metrics**: accuracy with bootstrap 95% CI, macro-F1, expected calibration error and reliability bins, p50/p95/p99 client-side latency, cost per 1,000 decisions, invalid-output rate and API-error rate.
- **Cascade**: escalate to the fallback when the primary's top probability is below tau. `jev-bench cascade` replays the sweep offline from the two cached runs, so trying other thresholds costs no API calls. Output: `results/cascade/*.json|csv` and a chart.
- Failures (timeouts after retries, HTTP errors, unparseable or out-of-schema output) are **counted as wrong and reported**, never dropped.

## Methodology

Fixed seed and sample, identical inputs to every backend, 3 warm-up calls excluded, 4 concurrent calls by default (`--workers`), latency measured client-side with network included (successful attempt only; retries are counted separately). Decisions are cached in `.cache/` keyed by backend, model id, task + question hash and example id, so reruns are cheap. Run records in `results/runs/` hold ids, gold labels and decisions, not dataset text.

**Chat baseline configuration:** `claude-haiku-5-5, thinking disabled, max_tokens=128`. This is **not Haiku 5.5's default behaviour** (adaptive thinking is on by default). In an initial smoke test with thinking on and the same 128-token budget, a substantial share of calls spent all their tokens on thinking (or were cut off) and returned no usable answer, so thinking was turned off for this classification baseline. Numbers for the chat model apply to that configuration only.

## Limitations

- Early-access hosted model; one snapshot; one network location; 500-example samples (see the confidence intervals).
- Jev's answers were not bit-for-bit identical between two of our own runs of the same inputs, so treat the last digit of any accuracy as noise.
- Chat-model probabilities are **verbalised confidences**, not token probabilities. Calibration comparisons are therefore not like-for-like.
- Jev bills instruction and criteria text as input tokens, so the cost of a question depends on its wording and on the number of options (77-way Banking77 costs about 3x the 10-way task per call).
- The Anthropic chat backend has been run live (chat-small only). The OpenAI chat backend and the LiteLLM gateway backend are covered by fake-client/transport tests but have **not been run live**.
- `lead-qualification` is synthetic and checks rubric-following only.
- Prices are list prices times token counts; no batch, cache or volume discounts.

## Not tested

- **Frontier models were not benchmarked.** The only chat model is a small one, so a cascade result here says nothing about a frontier fallback.
- **Cascades with a stronger fallback were not tested.** The only fallback, chat-small, is no more accurate than Jev on any task (lower on banking77, within noise on banking77-10 and guardrail in the run-date paired comparison), so the cascade results say nothing about a stronger one.
- **Gateways other than direct HTTP were not tested live** (LiteLLM, Vercel AI Gateway, Portkey, TrueFoundry, Requesty/OpenRouter, Arize).
- **Results are a snapshot on the run date with pinned model IDs** ([config/models.yaml](config/models.yaml)); a different model version or day can give different numbers.

## Data and attribution

- **Banking77**: created by PolyAI, licensed [CC-BY-4.0](https://creativecommons.org/licenses/by/4.0/). Source: <https://huggingface.co/datasets/PolyAI/banking77>. We load the parquet mirror <https://huggingface.co/datasets/legacy-datasets/banking77> (revision logged in each run record). Cite: Casanueva et al., "Efficient Intent Detection with Dual Sentence Encoders", NLP for ConvAI Workshop at ACL 2020, <https://arxiv.org/abs/2003.04807>.
- **NVIDIA Aegis 2.0** (the card now calls it "Nemotron Content Safety Dataset V2"): curated by the NeMo Guardrails team at NVIDIA, licensed CC-BY-4.0. Source: <https://huggingface.co/datasets/nvidia/Aegis-AI-Content-Safety-Dataset-2.0>. Cite: Ghosh et al., "AEGIS2.0: A Diverse AI Safety Dataset and Risks Taxonomy for Alignment of LLM Guardrails", NAACL 2025, <https://doi.org/10.18653/v1/2025.naacl-long.306>. The card says the data are intended for research that makes models less harmful and not for training dialogue agents; we use it only to evaluate classifiers. It contains offensive text.
- **Changes made:** we draw a fixed-seed sample of up to 500 test examples per task (banking77-10 restricts to 10 hand-picked classes; guardrail drops redacted prompts). Run records keep example ids, gold labels, decisions and, for Aegis, the violated-category labels, not the example text.
- **One exception:** `results/runs/guardrail__chat-small.json` quotes a short fragment of one Aegis prompt (id 00922) inside a model reply that was not valid JSON.

## Layout

```
src/jev_bench/   core types, deciders/, tasks/, metrics, runner, analysis, report, cli
config/          models.yaml (pinned versions), prices.yaml
results/         runs/, cascade/, charts/, REPORT.md, summary.*
tests/           no network
```
