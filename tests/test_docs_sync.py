"""Guard against documentation drift.

Every environment variable the loader reads must appear in both `.env.example`
and `DEPLOYMENT.md`, and neither may document a variable the code ignores. A
deployment guide that quietly omits a variable is worse than no guide.
"""
from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent

CONFIG_PATTERN = re.compile(r'_(?:raw|int|float|bool|required)\(\s*"([A-Z0-9_]+)"')
EXAMPLE_PATTERN = re.compile(r"^([A-Z0-9_]+)=", re.M)
DOC_TABLE_PATTERN = re.compile(r"^\|\s*`([A-Z0-9_]+)`\s*\|", re.M)


def config_variables() -> set[str]:
    return set(CONFIG_PATTERN.findall((ROOT / "bot" / "config.py").read_text()))


def example_variables() -> set[str]:
    return set(EXAMPLE_PATTERN.findall((ROOT / ".env.example").read_text()))


def deployment_variables() -> set[str]:
    return set(DOC_TABLE_PATTERN.findall((ROOT / "DEPLOYMENT.md").read_text()))


def test_config_reads_a_plausible_number_of_variables():
    """Catches the regex silently matching nothing."""
    assert len(config_variables()) > 25


def test_env_example_documents_every_variable():
    missing = config_variables() - example_variables()
    assert not missing, f".env.example is missing: {sorted(missing)}"


def test_env_example_documents_nothing_extra():
    extra = example_variables() - config_variables()
    assert not extra, f".env.example documents unread variables: {sorted(extra)}"


def test_deployment_guide_documents_every_variable():
    missing = config_variables() - deployment_variables()
    assert not missing, f"DEPLOYMENT.md is missing: {sorted(missing)}"


def test_deployment_guide_documents_nothing_extra():
    extra = deployment_variables() - config_variables()
    assert not extra, f"DEPLOYMENT.md documents unread variables: {sorted(extra)}"


def test_required_credentials_are_called_out_in_the_guide():
    guide = (ROOT / "DEPLOYMENT.md").read_text()
    for name in (
        "CTRADER_APP_ID",
        "CTRADER_APP_SECRET",
        "CTRADER_ACCESS_TOKEN",
        "CTRADER_ACCOUNT_ID",
    ):
        assert name in guide, f"{name} is required but absent from DEPLOYMENT.md"


def test_no_credential_looking_values_are_committed():
    """A worked example must never carry a real-looking secret."""
    guide = (ROOT / "DEPLOYMENT.md").read_text()
    assert not re.search(r"(?:access_token|client_secret)\"?\s*[:=]\s*[A-Za-z0-9_-]{20,}", guide)
