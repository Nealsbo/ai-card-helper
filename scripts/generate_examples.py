"""Run real inference for the bundled fictional products; never fabricate responses."""

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import Settings  # noqa: E402
from app.llm import LLMError, LLMClient  # noqa: E402
from app.models import ProductInput  # noqa: E402
from app.service import generate  # noqa: E402


async def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--id", choices=["pressure", "temperature"], help="Generate just one example"
    )
    parser.add_argument(
        "--debug", action="store_true", help="Save model JSON (no reasoning) in output/"
    )
    parser.add_argument("--model", help="Optional model ID; overrides LLM_MODEL for this run")
    args = parser.parse_args()
    examples = json.loads((ROOT / "examples/products.json").read_text(encoding="utf-8"))
    output = ROOT / "examples/results"
    output.mkdir(exist_ok=True)
    failures = 0

    class ExampleLLM(LLMClient):
        async def request(self, method, path, **kwargs):
            data = await super().request(method, path, **kwargs)
            if args.debug and method == "POST":
                debug_dir = ROOT / "output/llm"
                debug_dir.mkdir(parents=True, exist_ok=True)
                choice = data.get("choices", [{}])[0]
                (debug_dir / f"{example['id']}-{self.calls}.json").write_text(
                    json.dumps(
                        {
                            "content": choice.get("message", {}).get("content"),
                            "finish_reason": choice.get("finish_reason"),
                            "usage": data.get("usage"),
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                    encoding="utf-8",
                )
            return data

    for example in examples:
        if args.id and example["id"] != args.id:
            continue
        print(f"Generating {example['id']}...", flush=True)
        try:
            result = await generate(
                ProductInput.model_validate(example["product"]),
                ExampleLLM(Settings(), model=args.model),
            )
        except LLMError as exc:
            failures += 1
            print(f"FAILED: {exc}. Detail: {exc.__cause__}", flush=True)
            continue
        saved = {
            "demo_notice": example["notice"],
            "input": example["product"],
            **result.model_dump(),
        }
        (output / f"{example['id']}.json").write_text(
            json.dumps(saved, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        (output / f"{example['id']}.md").write_text(
            f"> {example['notice']}\n\n" + result.markdown, encoding="utf-8"
        )
        print(
            f"Saved {example['id']}: {result.elapsed_seconds}s, "
            f"{len(result.card.seo_description)} chars, {result.llm_calls} calls",
            flush=True,
        )
    return bool(failures)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
