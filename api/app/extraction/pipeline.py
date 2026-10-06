from dataclasses import dataclass

from pydantic import ValidationError

from app.config import Settings
from app.extraction.contract import Extraction
from app.extraction.pdf import (
    FileRejected,
    extract_text,
    has_text_layer,
    inspect_pdf,
    render_pages,
)
from app.extraction.prompt import (
    PROMPT_VERSION,
    document_text,
    repair_messages,
    text_messages,
    vision_messages,
)
from app.extraction.provider import (
    Completion,
    ExtractionPath,
    ExtractionProvider,
    ProviderUnavailable,
)

MAX_REPORTED_ERRORS = 20


# reason is a skip_reason (unsupported_type) or a failure_reason from docs/SPEC.md section 10.
class ExtractionError(Exception):
    def __init__(
        self,
        reason: str,
        detail: str,
        completions: tuple[Completion, ...] = (),
        fatal: bool = False,
    ) -> None:
        super().__init__(f"{reason}: {detail}")
        self.reason = reason
        self.completions = completions
        self.fatal = fatal


@dataclass(frozen=True)
class ExtractionResult:
    path: ExtractionPath
    provider: str
    model: str
    prompt_version: str
    extraction: Extraction
    text: str | None
    completions: tuple[Completion, ...]
    hidden_chars: int = 0


def parse_extraction(content: str) -> tuple[Extraction | None, str]:
    """Validate a model answer against the contract; on failure return a short error list."""
    try:
        return Extraction.model_validate_json(content), ""
    except ValidationError as exc:
        errors = [
            f"{'.'.join(str(part) for part in error['loc']) or 'root'}: {error['msg']}"
            for error in exc.errors(include_url=False)[:MAX_REPORTED_ERRORS]
        ]
        return None, "\n".join(errors)


def run_extraction(
    data: bytes, provider: ExtractionProvider, settings: Settings
) -> ExtractionResult:
    """Worker steps 1-4: file checks, text or vision path, LLM call, schema check, one repair."""
    try:
        pages = inspect_pdf(
            data, max_bytes=settings.max_file_mb * 1024 * 1024, max_pages=settings.max_pages
        )
        layer = extract_text(data)
    except FileRejected as exc:
        raise ExtractionError(exc.reason, str(exc)) from exc

    path: ExtractionPath
    if has_text_layer(layer.pages):
        path, text, messages = "text", document_text(layer.pages), text_messages(layer.pages)
    elif pages > provider.max_images:
        raise ExtractionError(
            "too_large", f"{pages} scanned pages, the vision model accepts {provider.max_images}"
        )
    else:
        path, text, messages = "vision", None, vision_messages(render_pages(data))

    completions: list[Completion] = []
    try:
        completions.append(provider.complete(messages, path))
        extraction, errors = parse_extraction(completions[-1].content)
        if extraction is None:
            repair = repair_messages(messages, completions[-1].content, errors)
            completions.append(provider.complete(repair, path))
            extraction, errors = parse_extraction(completions[-1].content)
    except ProviderUnavailable as exc:
        raise ExtractionError("llm_unavailable", str(exc), tuple(completions), exc.fatal) from exc
    if extraction is None:
        raise ExtractionError("invalid_extraction", errors, tuple(completions))

    return ExtractionResult(
        path=path,
        provider=provider.name,
        model=provider.model_for(path),
        prompt_version=PROMPT_VERSION,
        extraction=extraction,
        text=text,
        completions=tuple(completions),
        hidden_chars=layer.hidden_chars,
    )
