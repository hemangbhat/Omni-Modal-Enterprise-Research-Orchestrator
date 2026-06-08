from __future__ import annotations

import argparse
import sys
from pathlib import Path

from omni_modal.entity_extraction.inference import (
    QLoRAEnterpriseEntityExtractor,
    RuleBasedEnterpriseEntityExtractor,
)
from omni_modal.entity_extraction.training import (
    QLoRATrainingConfig,
    TrainingNotReadyError,
    run_qlora_training,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Local enterprise entity extraction.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    infer_parser = subparsers.add_parser("infer")
    infer_parser.add_argument("--tenant-id", required=True)
    infer_parser.add_argument("--document-id", required=True)
    infer_parser.add_argument("--chunk-id")
    infer_parser.add_argument("--input", required=True, type=Path)
    infer_parser.add_argument("--model-path", type=Path)

    train_parser = subparsers.add_parser("train")
    train_parser.add_argument("--base-model", required=True)
    train_parser.add_argument("--train-jsonl", required=True, type=Path)
    train_parser.add_argument("--output-dir", required=True, type=Path)

    args = parser.parse_args(argv)
    if args.command == "infer":
        text = args.input.read_text(encoding="utf-8")
        extractor = (
            QLoRAEnterpriseEntityExtractor(args.model_path)
            if args.model_path
            else RuleBasedEnterpriseEntityExtractor()
        )
        output = extractor.extract(
            tenant_id=args.tenant_id,
            document_id=args.document_id,
            chunk_id=args.chunk_id,
            text=text,
        )
        print(output.to_json())
        return 0

    try:
        summary = run_qlora_training(
            QLoRATrainingConfig(
                base_model=args.base_model,
                train_jsonl=args.train_jsonl,
                output_dir=args.output_dir,
            )
        )
    except TrainingNotReadyError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    print(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
