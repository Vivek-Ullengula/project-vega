import re
from urllib.parse import urlparse


class BaseCrawler:
    """Base class for Coaction crawlers providing common URL and text utilities."""

    def __init__(self, start_url: str, base_path: str = "/manuals/"):
        self.start_url = start_url
        self.base_domain = urlparse(start_url).netloc
        self.base_path = base_path
        self.visited = set()
        self.page_contents = {}

    def is_valid_url(self, url: str) -> bool:
        parsed = urlparse(url)
        clean = parsed._replace(fragment="").geturl()
        return (
            parsed.netloc == self.base_domain
            and parsed.path.startswith(self.base_path)
            and clean not in self.visited
        )

    def normalize_url(self, url: str) -> str:
        parsed = urlparse(url)
        return parsed._replace(fragment="").geturl()

    def extract_links(self, markdown: str) -> list[str]:
        pattern = r"\[.*?\]\((http[s]?://[^\)]+)\)"
        links = re.findall(pattern, markdown)
        valid = []
        for lnk in links:
            norm = self.normalize_url(lnk)
            if self.is_valid_url(norm):
                valid.append(norm)
        return valid

    def extract_class_code(self, url: str) -> str | None:
        m = re.search(r"/manuals/(\d+)\.html$", url)
        return m.group(1) if m else None
