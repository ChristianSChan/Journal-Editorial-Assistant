"""LLM provider abstraction for hosted API or local CLI calls."""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import requests

OPENAI_CHAT_COMPLETIONS_URL = "https://api.openai.com/v1/chat/completions"
REQUEST_TIMEOUT_SECONDS = 25
LOCAL_LLM_TIMEOUT_SECONDS = 90
CODEX_LLM_TIMEOUT_SECONDS = 120
DEFAULT_CODEX_COMMAND = "codex"
DEFAULT_CODEX_HOME = str(Path.home() / ".codex")
_LAST_LLM_ERROR = ""


def llm_enabled() -> bool:
    provider = llm_provider()
    if provider == "local_cli":
        command = local_llm_command()
        return bool(command and local_llm_model() and (shutil.which(command) or os.path.exists(command)))
    if provider == "custom_cli":
        command = custom_cli_command()
        executable = shlex.split(command)[0] if command else ""
        return bool(command and executable and (shutil.which(executable) or os.path.exists(executable)))
    if provider == "codex_cli":
        command = codex_cli_command()
        return bool(command and (shutil.which(command) or os.path.exists(command)))
    if provider == "openai":
        return bool(os.getenv("OPENAI_API_KEY", "").strip())
    return False


def llm_provider() -> str:
    provider = os.getenv("LLM_PROVIDER", "openai").strip().casefold()
    return provider if provider in {"openai", "local_cli", "custom_cli", "codex_cli"} else "openai"


def local_llm_command() -> str:
    return os.getenv("LOCAL_LLM_COMMAND", "ollama").strip() or "ollama"


def local_llm_model() -> str:
    return os.getenv("LOCAL_LLM_MODEL", "llama3.1:8b").strip() or "llama3.1:8b"


def custom_cli_command() -> str:
    return os.getenv("LOCAL_LLM_COMMAND_TEMPLATE", "").strip()


def codex_cli_command() -> str:
    return os.getenv("CODEX_CLI_COMMAND", DEFAULT_CODEX_COMMAND).strip() or DEFAULT_CODEX_COMMAND


def codex_cli_model() -> str:
    return os.getenv("CODEX_CLI_MODEL", "").strip()


def codex_cli_home() -> str:
    return os.getenv("CODEX_CLI_HOME", DEFAULT_CODEX_HOME).strip() or DEFAULT_CODEX_HOME


def llm_status_label() -> str:
    if llm_provider() == "codex_cli":
        if llm_enabled():
            model = f" {codex_cli_model()}" if codex_cli_model() else ""
            return f"LLM: Codex CLI{model}"
        return "LLM: Codex CLI not configured"
    if llm_provider() == "local_cli":
        if llm_enabled():
            return f"LLM: local CLI ({local_llm_command()} {local_llm_model()})"
        return "LLM: local CLI not configured"
    if llm_provider() == "custom_cli":
        if llm_enabled():
            return f"LLM: custom CLI ({custom_cli_command()})"
        return "LLM: custom CLI not configured"
    return "LLM: OpenAI enabled" if llm_enabled() else "LLM: not enabled"


def last_llm_error() -> str:
    return _LAST_LLM_ERROR


def call_llm_json(system_prompt: str, user_payload: Any, temperature: float = 0) -> dict[str, Any] | None:
    content = call_llm_text(system_prompt, user_payload, temperature=temperature)
    if not content:
        return None
    content = _strip_code_fence(content)
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", content, flags=re.DOTALL)
        if not match:
            return None
        try:
            parsed = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
    return parsed if isinstance(parsed, dict) else None


def call_llm_text(system_prompt: str, user_payload: Any, temperature: float = 0) -> str | None:
    _set_last_llm_error("")
    if not llm_enabled():
        _set_last_llm_error("LLM provider is not enabled or command/key is not configured.")
        return None
    if llm_provider() == "codex_cli":
        return _call_codex_cli(system_prompt, user_payload)
    if llm_provider() == "custom_cli":
        return _call_custom_cli(system_prompt, user_payload)
    if llm_provider() == "local_cli":
        return _call_local_cli(system_prompt, user_payload)
    return _call_openai_text(system_prompt, user_payload, temperature=temperature)


def _call_openai_text(system_prompt: str, user_payload: Any, temperature: float = 0) -> str | None:
    payload = {
        "model": os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        "temperature": temperature,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": _payload_to_text(user_payload)},
        ],
    }
    try:
        response = requests.post(
            os.getenv("OPENAI_CHAT_COMPLETIONS_URL", OPENAI_CHAT_COMPLETIONS_URL),
            headers={
                "Authorization": f"Bearer {os.getenv('OPENAI_API_KEY', '').strip()}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"]
    except (requests.RequestException, KeyError, IndexError, TypeError, ValueError):
        _set_last_llm_error("OpenAI API request failed or returned an unexpected response.")
        return None


def _call_local_cli(system_prompt: str, user_payload: Any) -> str | None:
    prompt = (
        f"{system_prompt}\n\n"
        "User payload:\n"
        f"{_payload_to_text(user_payload)}\n\n"
        "Follow the requested output format exactly."
    )
    command = [local_llm_command(), "run", local_llm_model()]
    try:
        result = subprocess.run(
            command,
            input=prompt,
            text=True,
            capture_output=True,
            timeout=LOCAL_LLM_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        _set_last_llm_error(f"Local CLI command could not be run: {local_llm_command()}")
        return None
    if result.returncode != 0:
        _set_last_llm_error(result.stderr.strip() or f"Local CLI exited with code {result.returncode}.")
        return None
    return result.stdout.strip()


def _call_custom_cli(system_prompt: str, user_payload: Any) -> str | None:
    prompt = (
        f"{system_prompt}\n\n"
        "User payload:\n"
        f"{_payload_to_text(user_payload)}\n\n"
        "Follow the requested output format exactly."
    )
    template = custom_cli_command()
    try:
        command = [
            part.format(model=local_llm_model())
            for part in shlex.split(template)
        ]
    except ValueError:
        _set_last_llm_error("Custom CLI command template could not be parsed.")
        return None
    if not command:
        _set_last_llm_error("Custom CLI command template is empty.")
        return None
    try:
        result = subprocess.run(
            command,
            input=prompt,
            text=True,
            capture_output=True,
            timeout=LOCAL_LLM_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        _set_last_llm_error(f"Custom CLI command could not be run: {template}")
        return None
    if result.returncode != 0:
        _set_last_llm_error(result.stderr.strip() or f"Custom CLI exited with code {result.returncode}.")
        return None
    return result.stdout.strip()


def _call_codex_cli(system_prompt: str, user_payload: Any) -> str | None:
    prompt = (
        f"{system_prompt}\n\n"
        "User payload:\n"
        f"{_payload_to_text(user_payload)}\n\n"
        "Return only the requested final answer. Do not edit files or run shell commands."
    )
    with tempfile.NamedTemporaryFile("w+", suffix=".txt", delete=False) as output_file:
        output_path = output_file.name

    command = [
        codex_cli_command(),
        "exec",
        "--skip-git-repo-check",
        "--ephemeral",
        "--sandbox",
        "read-only",
        "--output-last-message",
        output_path,
        "-",
    ]
    model = codex_cli_model()
    if model:
        command[2:2] = ["--model", model]

    try:
        Path(codex_cli_home()).mkdir(parents=True, exist_ok=True)
        env = os.environ.copy()
        env["CODEX_HOME"] = codex_cli_home()
        result = subprocess.run(
            command,
            input=prompt,
            text=True,
            capture_output=True,
            timeout=CODEX_LLM_TIMEOUT_SECONDS,
            check=False,
            env=env,
        )
        if result.returncode != 0:
            _set_last_llm_error(result.stderr.strip() or f"Codex CLI exited with code {result.returncode}.")
            return None
        with open(output_path, encoding="utf-8") as file:
            content = file.read().strip()
        return content or result.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        _set_last_llm_error(f"Codex CLI command could not be run: {codex_cli_command()}")
        return None
    finally:
        try:
            os.unlink(output_path)
        except OSError:
            pass


def _payload_to_text(user_payload: Any) -> str:
    if isinstance(user_payload, str):
        return user_payload
    return json.dumps(user_payload, ensure_ascii=False)


def _strip_code_fence(value: str) -> str:
    value = value.strip()
    if value.startswith("```"):
        value = re.sub(r"^```(?:json|text)?\s*", "", value)
        value = re.sub(r"\s*```$", "", value)
    return value.strip()


def _set_last_llm_error(message: str) -> None:
    global _LAST_LLM_ERROR
    _LAST_LLM_ERROR = message
