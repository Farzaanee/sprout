"""Smoke test: one tiny Claude call using the key from .env. Run: uv run python -m sprout.hello"""

import anthropic

from sprout.config import get_settings


def main() -> None:
    settings = get_settings()
    client = anthropic.Anthropic(api_key=settings.require_anthropic_key())
    response = client.messages.create(
        model=settings.bulk_model,
        max_tokens=1024,  # thinking tokens count towards this, so leave headroom
        output_config={"effort": "low"},
        messages=[{"role": "user", "content": "Reply with the single word: hello"}],
    )
    text = "".join(block.text for block in response.content if block.type == "text")
    print(f"{settings.bulk_model}: {text.strip()}")
    print(f"tokens in={response.usage.input_tokens} out={response.usage.output_tokens}")


if __name__ == "__main__":
    main()
