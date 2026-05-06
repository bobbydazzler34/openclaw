"""Minimal Google Gemini text generation for structured extraction."""

from __future__ import annotations

import os


def generate_text(
    system: str,
    user: str,
    *,
    model: str,
    api_key: str,
) -> str:
    """Call Gemini and return the model's text response.

    Args:
        system: System instruction (e.g. JSON-only rules).
        user: User message (the Telegram instruction).
        model: Model id, e.g. ``gemini-2.5-flash``.
        api_key: Google AI API key (not logged).

    Returns:
        Raw response text from the model.

    Raises:
        ValueError: If ``api_key`` is empty.
        Exception: From the underlying ``google-generativeai`` client on failure.
    """
    if not api_key or not str(api_key).strip():
        msg = "Gemini API key is missing; set the configured environment variable."
        raise ValueError(msg)

    import google.generativeai as genai

    genai.configure(api_key=api_key)
    gemini_model = genai.GenerativeModel(model, system_instruction=system)
    response = gemini_model.generate_content(user)
    if not response.text:
        msg = "Gemini returned empty response"
        raise RuntimeError(msg)
    return response.text


def api_key_from_env(env_var: str) -> str | None:
    """Read API key from ``os.environ``."""
    return os.environ.get(env_var)
