from __future__ import annotations

import asyncio
import re

from playwright.async_api import Locator, Page

from app.selectors import CHAT_PANEL_MARKERS, MESSAGE_INPUTS, SEARCH_INPUTS


class PageOperationError(RuntimeError):
    pass


class TargetUnavailableError(PageOperationError):
    pass


_CONVERSATION_LIST = ".conversationConversationListwrapper"
_CONVERSATION_ROW = '[data-e2e="conversation-item"]'
_CONVERSATION_HEADER = ".RightPanelHeaderconvHeader:visible"

# The web app exposes the conversation ID in the mounted list and chat components.
# Use the same ID in both places so a reordered row cannot change the recipient.
_REACT_CONVERSATION_ID = """(element, property) => {
    const key = Object.keys(element).find(key => key.startsWith("__reactFiber$"));
    for (let fiber = key && element[key]; fiber; fiber = fiber.return) {
        const id = fiber.memoizedProps?.[property]?.id;
        if (typeof id === "string" && id) return id;
    }
    return null;
}"""


class DouyinChat:
    def __init__(
        self,
        page: Page,
        timeout_ms: int = 15_000,
        confirm_timeout_ms: int = 15_000,
    ) -> None:
        self.page = page
        self.timeout_ms = timeout_ms
        self.confirm_timeout_ms = confirm_timeout_ms

    async def open_target(self, name: str, *, conversation_id: str | None = None) -> None:
        if conversation_id is None:
            await self._open_target_once(name)
        else:
            await self._open_conversation(conversation_id)

    async def _open_target_once(self, name: str) -> None:
        search = await first_visible(self.page, SEARCH_INPUTS, self.timeout_ms)
        await search.click()
        await search.fill("")
        await search.fill(name)
        await self.page.wait_for_timeout(1_500)

        # Results arrive over the network; a slow response is not a missing friend.
        for _ in range(max(0, self.timeout_ms - 1_500) // 500 + 1):
            result = await self._search_result(name)
            if result is not None:
                break
            await self.page.wait_for_timeout(500)
        else:
            raise TargetUnavailableError("搜索不到目标好友")
        await result.click(force=True)
        await self._confirm_opened(name)

    async def _open_conversation(self, conversation_id: str) -> None:
        conversation_list = self.page.locator(_CONVERSATION_LIST).first
        await conversation_list.wait_for(state="visible", timeout=self.timeout_ms)
        rows = conversation_list.locator(_CONVERSATION_ROW)
        lookup = f"""(rows) => rows.map(row => ({_REACT_CONVERSATION_ID})(row, "conversation"))"""
        deadline = asyncio.get_running_loop().time() + self.timeout_ms / 1_000
        await conversation_list.evaluate("(list) => { list.scrollTop = 0; }")
        await self.page.wait_for_timeout(350)
        last_bottom = None
        while True:
            ids = await rows.evaluate_all(lookup)
            if ids.count(conversation_id) == 1:
                await rows.nth(ids.index(conversation_id)).click(timeout=self.timeout_ms)
                await self.confirm_conversation_id(conversation_id, self.confirm_timeout_ms)
                return
            if asyncio.get_running_loop().time() >= deadline:
                break
            before, after, height = await conversation_list.evaluate("""list => {
                const before = list.scrollTop;
                list.scrollTop += Math.max(1, list.clientHeight * 0.8);
                return [before, list.scrollTop, list.scrollHeight];
            }""")
            bottom = (after, height) if before == after else None
            if bottom is not None and bottom == last_bottom:
                break
            last_bottom = bottom
            await self.page.wait_for_timeout(350)
        raise TargetUnavailableError("Conversation with the configured ID was not found")

    async def confirm_conversation_id(self, conversation_id: str, timeout_ms: int = 0) -> None:
        headers = self.page.locator(_CONVERSATION_HEADER)
        read_ids = f"""(headers) => headers.map(header => ({_REACT_CONVERSATION_ID})(header, "curConversation"))"""
        deadline = asyncio.get_running_loop().time() + timeout_ms / 1_000
        while True:
            if await headers.evaluate_all(read_ids) == [conversation_id] and await self._composer_visible():
                return
            if asyncio.get_running_loop().time() >= deadline:
                raise PageOperationError("Could not confirm the active conversation ID")
            await self.page.wait_for_timeout(250)

    async def _search_result(self, name: str) -> Locator | None:
        # Search mode renders a separate SearchPanel. Its "发消息" action is the
        # correct control; clicking the hidden conversation cache does not mount
        # the composer.
        #
        # Identity must be resolved from the per-result name node, never from the
        # collection container: `[class*="SearchPanelitem"]` also matches an outer
        # `SearchPanelitems` wrapper, whose descendants would then contain the
        # target name while `.first` returns another row's button. Scope to result
        # rows and require the matched name node and its button to be visible here.
        search_items = self.page.locator('[class*="SearchPanelitembox"], [class*="SearchPanelitem-box"], [class*="SearchPanelitem_box"]')
        name_selectors = (
            '[class*="SearchPanelitemtitle"]',
            '[class*="SearchPanelitemTitle"]',
            '[class*="SearchPanelitem_title"]',
            '[class*="SearchPanelitem-title"]',
            '[class*="SearchPanelitemname"]',
            '[class*="SearchPanelitemName"]',
            '[class*="SearchPanelitem_name"]',
            '[class*="SearchPanelitem-name"]',
        )

        # Two-phase priority: an exact friend name always wins over a group whose
        # display name happens to start with it. Pass 1 scans every search row for
        # an exact name; only if none is found does pass 2 accept a group member
        # count suffix like "4161(7)" for target "4161". This ordering guarantees
        # "test" never returns "test(7)" or "test1".
        for index in range(await search_items.count()):
            item = search_items.nth(index)
            name_locator = await _visible_exact_text_locator(item, name_selectors, name)
            if name_locator is None:
                continue
            button = item.locator('[class*="SearchPanelitemchat_btn"]').first
            try:
                if await button.count() and await button.is_visible():
                    return button
            except Exception:
                continue

        for index in range(await search_items.count()):
            item = search_items.nth(index)
            name_locator = await _visible_group_text_locator(item, name_selectors, name)
            if name_locator is None:
                continue
            button = item.locator('[class*="SearchPanelitemchat_btn"]').first
            try:
                if await button.count() and await button.is_visible():
                    return button
            except Exception:
                continue

        # The nickname node can be hidden while its conversation row is visible.
        # Locate and click the complete row instead of relying on text visibility.
        row_selectors = (
            '[data-e2e="conversation-item"]',
            '[class*="conversationConversationItem"]',
            '[class*="conversation-item"]',
            '[class*="ConversationItem"]',
        )
        title_selectors = (
            '[class*="conversationConversationItemtitle"]',
            '[class*="ConversationItemtitle"]',
            '[class*="ConversationItemTitle"]',
            '[class*="conversation-item-title"]',
            '[class*="conversation-item-Title"]',
        )
        for selector in row_selectors:
            rows = self.page.locator(selector)
            for index in range(await rows.count()):
                row = rows.nth(index)
                title_locator = await _visible_exact_text_locator(row, title_selectors, name)
                if title_locator is None:
                    continue
                try:
                    if await row.is_visible():
                        return row
                except Exception:
                    continue

        # Second-phase group suffix over conversation rows (same priority rule).
        for selector in row_selectors:
            rows = self.page.locator(selector)
            for index in range(await rows.count()):
                row = rows.nth(index)
                title_locator = await _visible_group_text_locator(row, title_selectors, name)
                if title_locator is None:
                    continue
                try:
                    if await row.is_visible():
                        return row
                except Exception:
                    continue

        # Some Douyin builds render the title itself as hidden, but keep a visible
        # ancestor as the actionable result. Find that ancestor from the hidden title.
        # This hidden-title fallback stays STRICT exact only: a hidden stale name
        # node (group or plain) must never be trusted to resolve the recipient.
        hidden_titles = self.page.locator('[class*="conversationConversationItemtitle"]')
        for index in range(await hidden_titles.count()):
            title = hidden_titles.nth(index)
            if not await _text_equals(title, name):
                continue
            row = title.locator(
                "xpath=ancestor::*[contains(@class, 'conversationConversationItem')][1]"
            )
            if await row.count() and await row.is_visible():
                return row

        return None

    async def message_input(self) -> Locator:
        return await first_visible(self.page, MESSAGE_INPUTS, self.timeout_ms)

    async def _confirm_opened(self, name: str, timeout_ms: int | None = None) -> None:
        timeout = timeout_ms if timeout_ms is not None else self.confirm_timeout_ms
        deadline = asyncio.get_running_loop().time() + timeout / 1000
        while True:
            last_error = await self._chat_open_error(name)
            if last_error is None:
                return
            if asyncio.get_running_loop().time() >= deadline:
                raise last_error
            await self.page.wait_for_timeout(500)

    async def _chat_open_error(self, name: str) -> PageOperationError | None:
        # Confirm the right-side current chat by the authoritative chat title, which
        # must itself be visible. A visible header retaining a hidden stale name node
        # (common during SPA transitions) must not confirm the wrong recipient, and
        # a secondary username/title field must never substitute for the chat title.
        title_selectors = (
            '[class*="RightPanelHeadertitle"]',
            '[class*="RightPanelHeaderTitle"]',
            '[class*="RightPanelHeader_title"]',
            '[class*="RightPanelHeader-title"]',
            '[class*="chatHeadertitle"]',
            '[class*="ChatHeaderTitle"]',
            '[class*="chatHeader_title"]',
            '[class*="ChatHeader-title"]',
            '[class*="name"]',
            '[class*="Name"]',
            '[class*="nickname"]',
            '[class*="Nickname"]',
        )
        for selector in CHAT_PANEL_MARKERS[:3]:
            headers = self.page.locator(selector)
            for index in range(await headers.count()):
                header = headers.nth(index)
                try:
                    if not await header.is_visible():
                        continue
                except Exception:
                    continue
                if await _visible_exact_or_group_text_in(header, title_selectors, name):
                    return None

        composer_visible = await self._composer_visible()
        return PageOperationError(
            f"点击搜索结果后无法确认聊天已打开（输入框: {'有' if composer_visible else '无'}）"
        )

    async def _composer_visible(self) -> bool:
        return await visible_selector(self.page, MESSAGE_INPUTS) is not None


async def _visible_exact_text_locator(
    container: Locator, selectors: tuple[str, ...], expected: str
) -> Locator | None:
    for selector in selectors:
        nodes = container.locator(selector)
        for index in range(await nodes.count()):
            node = nodes.nth(index)
            if await _text_equals(node, expected):
                try:
                    if await node.is_visible():
                        return node
                except Exception:
                    continue
    return None


async def _visible_group_text_locator(
    container: Locator, selectors: tuple[str, ...], expected: str
) -> Locator | None:
    # Second-phase match for group chats: accepts a trailing "(N)"/"（N）"
    # member count. Only reached after the exact pass found nothing, so a bare
    # "test" never reaches here when an exact "test" row exists.
    for selector in selectors:
        nodes = container.locator(selector)
        for index in range(await nodes.count()):
            node = nodes.nth(index)
            if await _group_name_matches(node, expected):
                try:
                    if await node.is_visible():
                        return node
                except Exception:
                    continue
    return None


async def _visible_exact_or_group_text_in(
    container: Locator, selectors: tuple[str, ...], expected: str
) -> bool:
    # Chat-header confirmation: an exact title wins; otherwise a group member
    # count suffix also confirms. The node must be visible in both cases, so a
    # hidden stale title node can never confirm the wrong recipient.
    if await _visible_exact_text_locator(container, selectors, expected) is not None:
        return True
    return await _visible_group_text_locator(container, selectors, expected) is not None


async def _text_equals(locator: Locator, expected: str) -> bool:
    try:
        return (await locator.inner_text(timeout=500)).strip() == expected
    except Exception:
        return False


# Matches a group chat display name: the configured target name optionally
# followed by exactly one pair of brackets containing a pure member count,
# e.g. "4161" / "4161(7)" / "4161（123）". This is an INDEPENDENT helper kept
# separate from _text_equals so the friend exact-match semantics stay strict:
# it must never let "test" match "test1" (no trailing brackets to legitimize a
# longer name). re.fullmatch anchors both ends; re.escape makes the name literal.
_GROUP_COUNT_SUFFIX_RE_TEMPLATE = r"{name}\s*[\(（]\s*\d+\s*[\)）]"


def _group_count_suffix_matches(actual: str, expected: str) -> bool:
    actual = actual.strip()
    expected = expected.strip()
    if actual == expected:
        return True
    pattern = _GROUP_COUNT_SUFFIX_RE_TEMPLATE.format(name=re.escape(expected))
    return re.fullmatch(pattern, actual) is not None


async def _group_name_matches(locator: Locator, expected: str) -> bool:
    try:
        return _group_count_suffix_matches(
            await locator.inner_text(timeout=500), expected
        )
    except Exception:
        return False


POLL_MS = 250


async def visible_selector(scope: Page | Locator, selectors: tuple[str, ...]) -> str | None:
    """The first selector with a visible match right now, without waiting."""
    for selector in selectors:
        try:
            if await scope.locator(selector).first.is_visible():
                return selector
        except Exception:
            continue
    return None


async def wait_visible_selector(page: Page, selectors: tuple[str, ...], timeout_ms: int) -> str | None:
    """Poll every selector together until one is visible; earlier selectors win ties."""
    for _ in range(max(0, timeout_ms) // POLL_MS + 1):
        selector = await visible_selector(page, selectors)
        if selector is not None:
            return selector
        await page.wait_for_timeout(POLL_MS)
    return None


async def first_visible(page: Page, selectors: tuple[str, ...], timeout_ms: int = 15_000) -> Locator:
    selector = await wait_visible_selector(page, selectors, timeout_ms)
    if selector is None:
        raise PageOperationError(f"找不到页面元素，已尝试: {', '.join(selectors)}")
    return page.locator(selector).first
