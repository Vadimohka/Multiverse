import pytest
from workflow_engine.nodes import browser_context_options, perform_browser_action


class Locator:
    def __init__(self, calls, selector):
        self.calls = calls
        self.selector = selector

    @property
    def first(self):
        return self

    async def click(self, **kwargs):
        self.calls.append(("click", self.selector, kwargs))

    async def fill(self, value, **kwargs):
        self.calls.append(("fill", self.selector, value, kwargs))

    async def select_option(self, value, **kwargs):
        self.calls.append(("select", self.selector, value, kwargs))

    async def hover(self, **kwargs):
        self.calls.append(("hover", self.selector, kwargs))

    async def press(self, value, **kwargs):
        self.calls.append(("press", self.selector, value, kwargs))

    async def wait_for(self, **kwargs):
        self.calls.append(("wait_for", self.selector, kwargs))


class Page:
    def __init__(self):
        self.calls = []

    def locator(self, selector):
        return Locator(self.calls, selector)

    async def wait_for_timeout(self, milliseconds):
        self.calls.append(("wait", milliseconds))

    async def evaluate(self, script, *args):
        self.calls.append(("evaluate", script, args))


@pytest.mark.asyncio
async def test_all_browser_actions_have_one_validated_contract():
    page = Page()
    actions = [
        {"type": "click", "selector": "#open"},
        {"type": "fill", "selector": "#query", "value": "text"},
        {"type": "select", "selector": "#kind", "value": "all"},
        {"type": "hover", "selector": "#menu"},
        {"type": "press", "selector": "body", "value": "Enter"},
        {"type": "wait", "seconds": 0.1},
        {"type": "wait_for", "selector": ".ready"},
        {"type": "scroll", "pixels": 500},
        {"type": "javascript", "script": "document.title"},
    ]
    for action in actions:
        await perform_browser_action(page, action, 5000)

    assert [call[0] for call in page.calls] == [
        "click",
        "fill",
        "select",
        "hover",
        "press",
        "wait",
        "wait_for",
        "evaluate",
        "evaluate",
    ]


@pytest.mark.asyncio
async def test_browser_actions_reject_missing_fields_and_unknown_types():
    with pytest.raises(ValueError, match="selector"):
        await perform_browser_action(Page(), {"type": "click"}, 1000)
    with pytest.raises(ValueError, match="script"):
        await perform_browser_action(Page(), {"type": "javascript"}, 1000)
    with pytest.raises(ValueError, match="Unknown browser action"):
        await perform_browser_action(Page(), {"type": "site_magic"}, 1000)


def test_browser_context_options_accept_storage_state_and_profile_settings():
    options = browser_context_options(
        {
            "viewport": {"width": 1280, "height": 720},
            "locale": "en-US",
            "timezone": "UTC",
            "user_agent": "FixtureAgent/1.0",
            "storage_state": {"cookies": [], "origins": []},
        },
        {},
    )
    assert options == {
        "viewport": {"width": 1280, "height": 720},
        "locale": "en-US",
        "timezone_id": "UTC",
        "user_agent": "FixtureAgent/1.0",
        "storage_state": {"cookies": [], "origins": []},
    }
