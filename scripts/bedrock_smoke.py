"""Live manual smoke test for BedrockService — NOT run in CI.

Exercises chat(), structured(), and embed() against real Bedrock
credentials. Run by hand after configuring AWS_REGION/AWS_PROFILE (or
the default credential chain) and confirming the configured models are
enabled in the target region:

    python scripts/bedrock_smoke.py
"""

from __future__ import annotations

from heybrain.bedrock.client import BedrockService
from heybrain.bedrock.schemas import ConversationAnalysis
from heybrain.core.config import get_settings
from heybrain.storage.db import get_connection
from heybrain.storage.repositories import UsageRepo


def main() -> None:
    settings = get_settings()
    conn = get_connection()
    service = BedrockService(UsageRepo(conn), settings)

    print(f"Region: {settings.aws_region}  Profile: {settings.aws_profile}")
    print(f"Chat model: {settings.bedrock_model_id}")
    print(f"Embedding model: {settings.bedrock_embedding_model_id}")

    print("\n--- chat() ---")
    reply = service.chat(
        [{"role": "user", "content": "In one sentence, what is a second brain?"}],
        system="You are a concise assistant.",
        effort="medium",
    )
    print(reply)

    print("\n--- structured() ---")
    analysis = service.structured(
        [
            {
                "role": "user",
                "content": (
                    "User: I want to ship the Bedrock client by Friday.\n"
                    "Assistant: Noted — I'll track that as a goal."
                ),
            }
        ],
        system="Extract a structured analysis of this conversation.",
        schema=ConversationAnalysis,
        effort="low",
    )
    print(analysis.model_dump_json(indent=2))

    print("\n--- embed() ---")
    vectors = service.embed(["personal knowledge management", "second brain"])
    print(f"{len(vectors)} vectors, dimensionality={len(vectors[0])}")

    conn.close()
    print("\nSmoke test complete.")


if __name__ == "__main__":
    main()
