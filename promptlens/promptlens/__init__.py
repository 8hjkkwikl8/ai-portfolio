"""PromptLens：轻量、可复现的提示词实验工具。"""

from .models import DatasetItem, DatasetValidationError, PromptRenderError, PromptVersion, load_dataset
from .registry import PromptRegistry
from .providers import GenerationResult, MockProvider, OpenAICompatibleProvider, ProviderError, ProviderTimeout
from .runner import compare_runs, run_experiment, save_json
from .report import write_html_report

__all__ = [
    "DatasetItem",
    "DatasetValidationError",
    "GenerationResult",
    "MockProvider",
    "OpenAICompatibleProvider",
    "PromptRegistry",
    "PromptRenderError",
    "PromptVersion",
    "ProviderError",
    "ProviderTimeout",
    "compare_runs",
    "load_dataset",
    "run_experiment",
    "save_json",
    "write_html_report",
]

__version__ = "1.0.0"
