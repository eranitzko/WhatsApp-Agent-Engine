from notion_client import AsyncClient


def get_notion_tools(api_key: str, tasks_database_id: str) -> dict[str, dict]:
    client = AsyncClient(auth=api_key)

    async def search_pages(params: dict, **ctx) -> str:
        query = params.get("query", "")
        response = await client.search(query=query, filter={"property": "object", "value": "page"})
        results = response.get("results", [])
        if not results:
            return f"No pages found matching '{query}'."
        lines = []
        for page in results[:5]:
            props = page.get("properties", {})
            title_prop = next(
                (v for v in props.values() if v.get("title")),
                None,
            )
            title = (
                title_prop["title"][0]["plain_text"]
                if title_prop and title_prop["title"]
                else "(untitled)"
            )
            url = page.get("url", "")
            lines.append(f"• {title} — {url}")
        return "Found pages:\n" + "\n".join(lines)

    async def create_task(params: dict, **ctx) -> str:
        title = params.get("title", "Untitled Task")
        notes = params.get("notes", "")
        children = []
        if notes:
            children.append({
                "object": "block",
                "type": "paragraph",
                "paragraph": {"rich_text": [{"type": "text", "text": {"content": notes}}]},
            })
        response = await client.pages.create(
            parent={"database_id": tasks_database_id},
            properties={
                "Name": {"title": [{"type": "text", "text": {"content": title}}]},
            },
            children=children,
        )
        url = response.get("url", "")
        return f"Task '{title}' created. {url}"

    async def append_to_page(params: dict, **ctx) -> str:
        page_title = params.get("page_title", "")
        content = params.get("content", "")
        search_result = await client.search(
            query=page_title,
            filter={"property": "object", "value": "page"},
        )
        results = search_result.get("results", [])
        if not results:
            return f"No page found with title '{page_title}'."
        page_id = results[0]["id"]
        await client.blocks.children.append(
            block_id=page_id,
            children=[{
                "object": "block",
                "type": "paragraph",
                "paragraph": {"rich_text": [{"type": "text", "text": {"content": content}}]},
            }],
        )
        return f"Content appended to '{page_title}'."

    async def list_database_items(params: dict, **ctx) -> str:
        db_id = params.get("database_id", tasks_database_id)
        response = await client.databases.query(database_id=db_id)
        items = response.get("results", [])
        if not items:
            return "No items found in this database."
        lines = []
        for item in items[:10]:
            props = item.get("properties", {})
            name_prop = next(
                (v for v in props.values() if v.get("title")),
                None,
            )
            name = (
                name_prop["title"][0]["plain_text"]
                if name_prop and name_prop["title"]
                else "(untitled)"
            )
            status_prop = props.get("Status", {})
            status = status_prop.get("select", {}).get("name", "") if status_prop else ""
            line = f"• {name}"
            if status:
                line += f" [{status}]"
            lines.append(line)
        return "Items:\n" + "\n".join(lines)

    return {
        "search_pages": {
            "schema": {
                "name": "search_pages",
                "category": "notion",
                "description": "Search for pages in the Notion workspace by keyword.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Search keyword"},
                    },
                    "required": ["query"],
                },
            },
            "executor": search_pages,
        },
        "create_task": {
            "schema": {
                "name": "create_task",
                "category": "notion",
                "description": "Create a new task in the Notion tasks database.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string", "description": "Task title"},
                        "notes": {"type": "string", "description": "Optional notes or description"},
                    },
                    "required": ["title"],
                },
            },
            "executor": create_task,
        },
        "append_to_page": {
            "schema": {
                "name": "append_to_page",
                "category": "notion",
                "description": "Append text content to an existing Notion page.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "page_title": {"type": "string", "description": "Title of the page to find"},
                        "content": {"type": "string", "description": "Text to append"},
                    },
                    "required": ["page_title", "content"],
                },
            },
            "executor": append_to_page,
        },
        "list_database_items": {
            "schema": {
                "name": "list_database_items",
                "category": "notion",
                "description": "List items from a Notion database. Defaults to the tasks database.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "database_id": {
                            "type": "string",
                            "description": "Notion database ID. Omit to use the default tasks database.",
                        },
                    },
                    "required": [],
                },
            },
            "executor": list_database_items,
        },
    }
