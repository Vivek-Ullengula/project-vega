"""
Full page crawler — preserves full pages with injected metadata for Bedrock Semantic Chunking.
Usage: python -m scripts.crawlers.full_page_crawler
"""

import asyncio
import re
import os
from crawl4ai import AsyncWebCrawler, CrawlerRunConfig
from scripts.crawlers.base_crawler import BaseCrawler


class FullPageCrawler(BaseCrawler):
    def __init__(self, start_url: str):
        super().__init__(start_url)

    def clean_text(self, text: str) -> str:
        text = re.sub(r"!\[.*?\]\(.*?\)", "", text, flags=re.DOTALL)
        text = re.sub(r"AI-generated content may be incorrect\.", "", text)
        text = re.sub(r"\[([^\]]+)\]\(https://bindingauthority[^\)]+#[^\)]+\)", r"\1", text)
        return text.strip()

    async def _crawl_recursive(self, url: str, crawler: AsyncWebCrawler):
        norm_url = self.normalize_url(url)
        if norm_url in self.visited:
            return
        self.visited.add(norm_url)
        print(f"Crawling: {norm_url}")
        config = CrawlerRunConfig(word_count_threshold=10)
        result = await crawler.arun(url=norm_url, config=config)
        if not getattr(result, "success", False):  # type: ignore
            return
        markdown = getattr(result, "markdown", "")  # type: ignore
        cleaned = self.clean_text(markdown)
        class_code = self.extract_class_code(norm_url)
        metadata_header = f"SOURCE_URL: {norm_url}\n"
        if class_code:
            metadata_header += f"CLASS_CODE: {class_code}\n"
        metadata_header += "--- \n\n"
        self.page_contents[norm_url] = metadata_header + cleaned
        links = self.extract_links(markdown)
        tasks = [self._crawl_recursive(lnk, crawler) for lnk in links]
        if tasks:
            await asyncio.gather(*tasks)

    async def run(self) -> dict:
        async with AsyncWebCrawler() as crawler:
            await self._crawl_recursive(self.start_url, crawler)
        return self.page_contents


if __name__ == "__main__":

    async def fast_run():
        start_url = "https://bindingauthority.coactionspecialty.com/manuals/guide.html"
        output_dir = "data/bedrock_ingest/full_manuals"
        os.makedirs(output_dir, exist_ok=True)
        print(f"--- Starting crawl from {start_url} ---")
        crawler = FullPageCrawler(start_url)
        pages = await crawler.run()
        print(f"Crawled {len(pages)} pages. Saving to {output_dir}...")
        for url, content in pages.items():
            name = url.split("/")[-1].replace(".html", ".md")
            if not name:
                name = "index.md"
            with open(os.path.join(output_dir, name), "w", encoding="utf-8") as f:
                f.write(content)
        print(f"SUCCESS: {len(pages)} files saved!")

    asyncio.run(fast_run())
