import os

import pytest

from sprout import hello
from sprout.config import get_settings

pytestmark = pytest.mark.live


@pytest.mark.skipif(
    not (os.environ.get("ANTHROPIC_API_KEY") or get_settings().anthropic_api_key),
    reason="no ANTHROPIC_API_KEY",
)
def test_hello_call(capsys):
    hello.main()
    assert "hello" in capsys.readouterr().out.lower()
