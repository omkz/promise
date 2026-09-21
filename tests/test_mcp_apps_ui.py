from __future__ import annotations

import promise_mcp.server as mcp_server
import pytest

pytestmark = pytest.mark.anyio

UI_RESOURCE_URI = mcp_server.UI_RESOURCE_URI


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def bound(seeded_ctx, monkeypatch):
    """Point the MCP server module at an isolated per-test AppContext."""
    monkeypatch.setattr(mcp_server, "ctx", seeded_ctx)
    return seeded_ctx


async def test_tool_discovery_advertises_ui_metadata(bound):
    """tools/list must expose create_commitment with _meta.ui.resourceUri, per
    SEP-1865 (io.modelcontextprotocol/ui): the tool declares which ui:// view
    renders its results."""
    discovered = await mcp_server.mcp.list_tools()
    names = [t.name for t in discovered]
    assert "create_commitment" in names
    assert "handle_commitment" in names  # the view's Handle action calls this

    create_commitment_tool = next(t for t in discovered if t.name == "create_commitment")
    assert create_commitment_tool.meta == {"ui": {"resourceUri": UI_RESOURCE_URI}}

    # The output schema must be a real, discoverable JSON schema (not an opaque blob).
    schema = create_commitment_tool.output_schema
    assert schema is not None
    assert set(schema.get("properties", {}).keys()) >= {"commitment", "source", "contact"}


async def test_resource_discovery_lists_the_ui_view(bound):
    """resources/list must surface the ui:// resource with the MCP Apps mime type."""
    resources = await mcp_server.mcp.list_resources()
    matches = [r for r in resources if str(r.uri) == UI_RESOURCE_URI]
    assert len(matches) == 1

    resource = matches[0]
    assert str(resource.uri).startswith("ui://")
    assert resource.mime_type == "text/html;profile=mcp-app"


async def test_resource_read_returns_the_commitment_card_html(bound):
    """resources/read must return the real, built HTML for the Commitment Card
    View — a self-contained bundle produced by the official
    `@modelcontextprotocol/ext-apps` client/runtime, not free-form text."""
    contents = list(await mcp_server.mcp.read_resource(UI_RESOURCE_URI))
    assert len(contents) == 1

    html = contents[0].content
    assert contents[0].mime_type == "text/html;profile=mcp-app"
    assert isinstance(html, str) and html.strip().startswith("<!doctype html>")

    # Compact Commitment Card requirements (markup, not the bundled script).
    assert "PROMISE" in html
    assert "Commitment captured" in html
    for field_id in ('id="title"', 'id="contact"', 'id="due"', 'id="excerpt"'):
        assert field_id in html
    assert 'id="handle"' in html and "Handle this" in html

    # The view is the built bundle (services/mcp/ui/commitment-card/dist),
    # inlined as a single <script type="module"> from the official
    # @modelcontextprotocol/ext-apps client/runtime — never the hand-written,
    # JSON-RPC-over-postMessage bridge this milestone replaced.
    assert '<script type="module"' in html
    assert "handle_commitment" in html  # the bundled callServerTool() call site


def test_ui_dist_is_built_from_the_dedicated_mcp_app_package():
    """The served HTML must be the build output of services/mcp/ui/commitment-card,
    not a file hand-maintained inside the Python service."""
    assert mcp_server._UI_DIST.exists(), (
        "run `npm run build` in services/mcp/ui/commitment-card before serving"
    )
    assert mcp_server._UI_DIST.read_text(encoding="utf-8") == mcp_server._UI_HTML


async def test_create_commitment_returns_structured_data_and_ui_metadata(bound):
    """tools/call create_commitment must return both structuredContent (for the
    view to render) and _meta.ui.resourceUri (so the host knows which view to use),
    exercised through the real MCPServer call path (argument + output validation)."""
    result = await mcp_server.mcp.call_tool(
        "create_commitment", {"text": "I'll send Andi the revised proposal tomorrow morning."}
    )

    assert result.is_error is False
    assert result.meta == {"ui": {"resourceUri": UI_RESOURCE_URI}}

    assert result.content and result.content[0].type == "text"
    assert "Commitment captured" in result.content[0].text

    structured = result.structured_content
    assert structured["commitment"]["status"] == "open"
    assert structured["contact"]["name"] == "Andi"
    assert structured["source"]["excerpt"] == "I'll send Andi the revised proposal tomorrow morning."


async def test_ui_resource_uri_is_consistent_between_tool_listing_and_call_result(bound):
    """The resourceUri advertised at discovery time and the one returned on a real
    call must be the exact same ui:// resource — a host must not need special-casing."""
    discovered = await mcp_server.mcp.list_tools()
    listed_uri = next(t for t in discovered if t.name == "create_commitment").meta["ui"]["resourceUri"]

    call_result = await mcp_server.mcp.call_tool("create_commitment", {"text": "I'll call Sam today."})
    result_uri = call_result.meta["ui"]["resourceUri"]

    assert listed_uri == result_uri == UI_RESOURCE_URI

    resources = await mcp_server.mcp.list_resources()
    assert any(str(r.uri) == result_uri for r in resources)


async def test_handle_action_in_the_view_calls_the_existing_application_tool(bound):
    """The Commitment Card's "Handle this" button calls back `tools/call` with
    name="handle_commitment" — the same tool/application-layer operation used
    everywhere else, never a direct database mutation from the view."""
    created = await mcp_server.mcp.call_tool(
        "create_commitment", {"text": "I'll send Andi the revised proposal tomorrow morning."}
    )
    commitment_id = created.structured_content["commitment"]["id"]

    handled = await mcp_server.mcp.call_tool("handle_commitment", {"commitment_id": commitment_id})

    assert handled.is_error is False
    assert handled.structured_content["action"]["status"] == "waiting_for_approval"

    # Went through the real application layer: the commitment/action/approval trail exists.
    from promise_app import tools

    commitment = tools.get_commitment(bound, workspace_id=bound.default_workspace_id, commitment_id=commitment_id)
    assert commitment.status.value == "waiting_for_approval"
