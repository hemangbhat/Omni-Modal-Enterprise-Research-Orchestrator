from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from omni_modal.entity_extraction.training_data import load_training_jsonl


@dataclass(frozen=True)
class QLoRATrainingConfig:
    base_model: str
    train_jsonl: Path
    output_dir: Path
    max_steps: int = 200
    learning_rate: float = 2e-4
    lora_rank: int = 16
    lora_alpha: int = 32
    lora_dropout: float = 0.05


class TrainingNotReadyError(RuntimeError):
    pass


def run_qlora_training(config: QLoRATrainingConfig) -> dict[str, object]:
    if not config.train_jsonl.exists():
        raise TrainingNotReadyError(
            f"Training data not found: {config.train_jsonl}. "
            "Create JSONL examples using the enterprise_entities.v1 schema first."
        )

    examples = load_training_jsonl(config.train_jsonl)
    if not examples:
        raise TrainingNotReadyError("Training data file contains no examples.")

    try:
        import torch  # type: ignore[import-not-found]  # noqa: F401
        from peft import LoraConfig, get_peft_model  # type: ignore[import-not-found]  # noqa: F401
        from transformers import AutoModelForCausalLM, AutoTokenizer  # type: ignore[import-not-found]  # noqa: F401
    except ImportError as exc:
        raise TrainingNotReadyError(
            "QLoRA training requires local torch, transformers, peft, accelerate, "
            "bitsandbytes, and a compatible GPU/runtime. The dataset schema is ready, "
            "but the training loop is intentionally stubbed until those dependencies "
            "and labeled examples are available."
        ) from exc

    return {
        "status": "stubbed",
        "reason": "Validated dataset and ML dependencies, but no project-specific "
        "training recipe has been run yet.",
        "base_model": config.base_model,
        "output_dir": str(config.output_dir),
        "training_examples": len(examples),
        "max_steps": config.max_steps,
        "learning_rate": config.learning_rate,
        "lora_rank": config.lora_rank,
        "lora_alpha": config.lora_alpha,
        "lora_dropout": config.lora_dropout,
    }
