import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from app.tools.notion_tools import get_notion_tools


@pytest.fixture
def mock_notion_client():
    client = MagicMock()
    client.search = AsyncMock(return_value={
        "results": [
            {"object": "page", "id": "page-001", "properties": {"title": {"title": [{"plain_text": "My Note"}]}}, "url": "https://notion.so/my-note"}
        ]
    })
    client.pages.create = AsyncMock(return_value={
        "id": "page-002", "url": "https://notion.so/new-task"
    })
    client.blocks.children.append = AsyncMock(return_value={"results": []})
    client.databases.query = AsyncMock(return_value={
        "results": [
            {
                "id": "item-001",
                "properties": {
                    "Name": {"title": [{"plain_text": "Buy groceries"}]},
                    "Status": {"select": {"name": "In Progress"}},
                }
            }
        ]
    })
    return client


@pytest.fixture
def tools(mock_notion_client):
    with patch("app.tools.notion_tools.AsyncClient", return_value=mock_notion_client):
        return get_notion_tools(api_key="test-key", tasks_database_id="db-001")


@pytest.mark.asyncio
async def test_search_pages_returns_results(tools, mock_notion_client):
    result = await tools["search_pages"]["executor"]({"query": "My Note"})
    assert "My Note" in result
    mock_notion_client.search.assert_called_once()


@pytest.mark.asyncio
async def test_search_pages_empty_returns_message(tools, mock_notion_client):
    mock_notion_client.search = AsyncMock(return_value={"results": []})
    result = await tools["search_pages"]["executor"]({"query": "nonexistent"})
    assert "No pages found" in result


@pytest.mark.asyncio
async def test_create_task_creates_page_in_database(tools, mock_notion_client):
    result = await tools["create_task"]["executor"]({"title": "Fix bug", "notes": "urgent"})
    assert "Fix bug" in result or "created" in result.lower()
    mock_notion_client.pages.create.assert_called_once()
    call_kwargs = mock_notion_client.pages.create.call_args[1]
    assert call_kwargs["parent"]["database_id"] == "db-001"


@pytest.mark.asyncio
async def test_append_to_page_calls_blocks_append(tools, mock_notion_client):
    mock_notion_client.search = AsyncMock(return_value={
        "results": [{"object": "page", "id": "page-001", "properties": {"title": {"title": [{"plain_text": "My Note"}]}}, "url": "https://notion.so/my-note"}]
    })
    result = await tools["append_to_page"]["executor"]({"page_title": "My Note", "content": "New paragraph"})
    mock_notion_client.blocks.children.append.assert_called_once()
    assert "appended" in result.lower() or "added" in result.lower()


@pytest.mark.asyncio
async def test_list_database_items_returns_items(tools, mock_notion_client):
    result = await tools["list_database_items"]["executor"]({"database_id": "db-001"})
    assert "Buy groceries" in result


def test_all_four_tools_are_present(tools):
    assert "search_pages" in tools
    assert "create_task" in tools
    assert "append_to_page" in tools
    assert "list_database_items" in tools


def test_all_tools_have_schema_and_executor(tools):
    for name, entry in tools.items():
        assert "schema" in entry, f"{name} missing schema"
        assert "executor" in entry, f"{name} missing executor"
        assert entry["schema"]["name"] == name
