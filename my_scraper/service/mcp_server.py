"""MCP server: lets AI assistants such as Claude Code, Claude Desktop or Cursor read the web.

Run with `python -m my_scraper.service.mcp_server` (stdio, for local assistants), or add
`--transport streamable-http --port 8788` to serve it over HTTP.

Every request obeys robots.txt and the scraper's compliance rules. Pages that refuse
come back as errors that say why, rather than as empty results.
"""
import argparse
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from mcp.server.mcpserver import MCPServer  # noqa: E402
from mcp.server.mcpserver.exceptions import ToolError  # noqa: E402
from mcp_types import ToolAnnotations  # noqa: E402

from my_scraper.service.engine import DEFAULT_MAX_CHARS, ScrapeService, ServiceError  # noqa: E402

INSTRUCTIONS = """Reads public web pages for you. Use scrape_url for one page (clean Markdown \
plus structured items), crawl_site to read several pages of one site, map_site to list a \
site's pages before choosing which to read, and extract to pull named fields such as price \
or author. Pages are fetched politely: robots.txt is obeyed, logins, paywalls and anti-bot \
walls are never bypassed, and a refusal is reported as an error with the reason. JavaScript \
sites are rendered in a browser automatically when needed."""

READ_ONLY = ToolAnnotations(readOnlyHint=True, openWorldHint=True, idempotentHint=True)

_service: Optional[ScrapeService] = None


def service() -> ScrapeService:
    global _service
    if _service is None:
        _service = ScrapeService()
    return _service


@asynccontextmanager
async def lifespan(_server):
    try:
        yield None
    finally:
        if _service is not None:
            await _service.close()


mcp = MCPServer("web-data-extraction", instructions=INSTRUCTIONS, lifespan=lifespan)


async def _answer(request) -> Dict[str, Any]:
    try:
        return await request
    except ServiceError as e:
        raise ToolError(f"{e.code}: {e.message}") from e


@mcp.tool(annotations=READ_ONLY, structured_output=False)
async def scrape_url(url: str, render: str = "auto", max_chars: int = DEFAULT_MAX_CHARS,
                     include_items: bool = True, include_links: bool = False) -> Dict[str, Any]:
    """Read one web page. Returns its main content as clean Markdown (no menus, ads or
    cookie banners), its title and metadata (author, date, description), and any repeated
    items on it such as products, search results or table rows.

    Args:
        url: The page, starting with http:// or https://. PDFs, Word, Excel, CSV, JSON and
            feeds work too.
        render: "auto" uses a browser only when the page needs JavaScript, "always" forces
            it, "never" uses plain HTTP only.
        max_chars: Longest Markdown returned; longer content is cut and marked truncated.
        include_items: Also return the structured items found on the page.
        include_links: Also return the links on the page (up to 300).
    """
    return await _answer(service().scrape(url, render=render, max_chars=max_chars,
                                          include_items=include_items, include_links=include_links))


@mcp.tool(annotations=READ_ONLY, structured_output=False)
async def crawl_site(url: str, max_pages: int = 20, max_depth: int = 3, include: Optional[List[str]] = None,
                     exclude: Optional[List[str]] = None, render: str = "auto", use_sitemap: bool = False,
                     max_chars_per_page: int = 8000, timeout_seconds: int = 120) -> Dict[str, Any]:
    """Read several pages of one site by following its links, as for documentation or a
    blog. Returns each page as Markdown, plus the structured items found across them.
    Stays on the starting site. Sites are paced at about one request per second, so
    20 pages take roughly 20-40 seconds.

    Args:
        url: Where to start, e.g. https://docs.example.com/guide/.
        max_pages: Most pages to read (1-200).
        max_depth: How many links away from the start page to go. Next-page links don't count.
        include: Regular expressions; only follow links that match one, e.g. ["/docs/"].
        exclude: Regular expressions; never follow links that match, e.g. ["/blog/", "\\?lang="].
        render: "auto", "always" or "never", as for scrape_url.
        use_sitemap: Also find pages from the site's sitemap.
        max_chars_per_page: Longest Markdown returned per page.
        timeout_seconds: Stop after this long and return what was read so far.
    """
    return await _answer(service().crawl(url, max_pages=max_pages, max_depth=max_depth, include=include,
                                         exclude=exclude, render=render, use_sitemap=use_sitemap,
                                         max_chars_per_page=max_chars_per_page, timeout=timeout_seconds))


@mcp.tool(annotations=READ_ONLY, structured_output=False)
async def map_site(url: str, limit: int = 500, search: Optional[str] = None,
                   use_sitemap: bool = True) -> Dict[str, Any]:
    """List the pages of a site without reading them, from its sitemaps and the links on
    the given page. Use it to find the right pages before scraping them.

    Args:
        url: A page of the site, usually its home page.
        limit: Most URLs to return (up to 5000).
        search: Only return URLs containing this text, e.g. "pricing" or "/api/".
        use_sitemap: Also read the site's sitemaps.
    """
    return await _answer(service().map_site(url, limit=limit, search=search, use_sitemap=use_sitemap))


@mcp.tool(annotations=READ_ONLY, structured_output=False)
async def extract(url: str, fields: List[str], many: bool = False, render: str = "auto") -> Dict[str, Any]:
    """Pull named fields from a page, such as ["title", "price", "brand", "rating"]. Fields
    are found in the page's structured data (JSON-LD, microdata, specification tables) and
    in the items on it, matching common alternative names (price also finds offers.price).
    Fields that can't be found are listed in "missing", and the page's Markdown is
    included so you can read them yourself.

    Args:
        url: The page.
        fields: Names of the fields wanted.
        many: One record per item on the page (for listings and search results) instead of
            one record for the whole page.
        render: "auto", "always" or "never", as for scrape_url.
    """
    return await _answer(service().extract(url, fields, many=many, render=render))


def main(argv: Optional[List[str]] = None):
    parser = argparse.ArgumentParser(description="MCP server for web-data-extraction")
    parser.add_argument("--transport", choices=["stdio", "streamable-http"], default="stdio")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8788)
    args = parser.parse_args(argv)
    if args.transport == "stdio":
        mcp.run()
    else:
        mcp.run(transport="streamable-http", host=args.host, port=args.port)


if __name__ == "__main__":
    main()
