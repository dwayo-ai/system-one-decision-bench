# Contributing

- `make setup`, then `make test` and `make lint` must pass. Tests never touch the network.
- Backends implement the `Decider` protocol in `src/jev_bench/core.py`. Add one under `src/jev_bench/deciders/`,
  register it in `config/models.yaml`, and add a contract test against a fake transport or client.
- Tasks live in `src/jev_bench/tasks/`. Record the dataset source and license in the task definition (`src/jev_bench/tasks/registry.py`).
- Never invent a number. Every figure in a doc comes from a committed run in `results/` or is attributed to the vendor.
- Changing a chat prompt template? Rerun with `--refresh`: the cache key covers the question text, not the prompt wrapper.
- Never commit keys. They come from the environment or a git-ignored `.env`.
