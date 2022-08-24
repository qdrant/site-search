import json
import multiprocessing
import os
from dataclasses import dataclass
from typing import List, Optional

import urllib.parse
from urllib.parse import urlparse
import requests
import tqdm as tqdm
from bs4 import BeautifulSoup
from usp.fetch_parse import SitemapFetcher
from usp.objects.sitemap import IndexWebsiteSitemap, InvalidSitemap
from usp.tree import sitemap_tree_for_homepage

from site_search.config import DATA_DIR

HEADER_TAGS = ['h1', 'h2', 'h3', 'h4', 'h5', 'h6']


@dataclass
class PageAbstract:
    text: str
    url: str
    tag: str
    location: str
    sections: Optional[List[str]] = None
    titles: Optional[List[str]] = None


def get_path_hierarchy(url: str) -> List[str]:
    """
    Input example: "/foo/bar"
    Output example: ["/foo", "/foo/bar"]

    >>> get_path_hierarchy("/foo/bar")
    ['foo', 'foo/bar']

    >>> get_path_hierarchy("foo")
    ['foo']

    >>> get_path_hierarchy("/foo/")
    ['foo']

    >>> get_path_hierarchy("foo/bar/")
    ['foo', 'foo/bar']

    """
    parsed_url = urlparse(url)
    path = parsed_url.path
    if not path:
        return []
    prefix = ''
    result = []
    for directory in path.strip('/').split("/"):
        prefix += directory
        result.append(prefix)
        prefix += '/'
    return result


def selector_soup(element):
    components = []
    child = element if element.name else element.parent
    for parent in child.parents:
        siblings = parent.find_all(child.name, recursive=False)
        components.append(
            child.name
            if siblings == [child] else
            '%s:nth-of-type(%d)' % (child.name, 1 + siblings.index(child))
        )
        child = parent
    components.reverse()
    return '%s' % ' > '.join(components)


class Crawler:
    def __init__(self, site):
        self.site = site
        self.pages = []

    def download_sitemap(self, sitemap_url: Optional[str] = None):
        if not sitemap_url:
            tree = sitemap_tree_for_homepage(self.site)
            return tree.all_pages()
        else:
            sitemaps = []
            unpublished_sitemap_fetcher = SitemapFetcher(
                url=sitemap_url,
                web_client=None,
                recursion_level=0,
            )
            unpublished_sitemap = unpublished_sitemap_fetcher.sitemap()

            # Skip the ones that weren't found
            if not isinstance(unpublished_sitemap, InvalidSitemap):
                sitemaps.append(unpublished_sitemap)

            index_sitemap = IndexWebsiteSitemap(url=self.site, sub_sitemaps=sitemaps)
            return index_sitemap.all_pages()

    def crawl_page(self, url: str) -> List[PageAbstract]:
        abstracts = []

        sections = get_path_hierarchy(url)

        resp = requests.get(url)
        if not resp.ok:
            return abstracts

        soup = BeautifulSoup(resp.content, 'html.parser')

        title = soup.find('title')

        titles = []

        if title:
            titles.append(title.text)

        current_headers_per_element = {}

        for tag in soup.find_all(['p', *HEADER_TAGS]):
            tag_text = tag.text.strip()
            parent_selector = selector_soup(tag.parent)

            parent_headers = current_headers_per_element.get(parent_selector, None)
            if parent_headers:
                parent_headers = [parent_headers]
            else:
                parent_headers = []

            if tag.name in HEADER_TAGS:
                current_headers_per_element[parent_selector] = tag.text

            for line in tag_text.splitlines():
                if line:
                    abstracts.append(PageAbstract(
                        text=line,
                        url=url,
                        tag=tag.name,
                        location=selector_soup(tag),
                        titles=titles + parent_headers,
                        sections=sections
                    ))

        return abstracts


if __name__ == '__main__':
    page_url = "https://qdrant.tech/"
    site_map_url = page_url + "sitemap.xml"
    crawler = Crawler(page_url)

    pages = crawler.download_sitemap(site_map_url)

    with open(os.path.join(DATA_DIR, 'abstracts.jsonl'), 'w') as out:
        page_urls = []
        for page in pages:
            full_page_url = urllib.parse.urljoin(page_url, page.url)
            page_urls.append(full_page_url)

        with multiprocessing.Pool(processes=10) as pool:
            for abstracts in tqdm.tqdm(pool.imap(crawler.crawl_page, page_urls)):
                for abstract in abstracts:
                    out.write(json.dumps(abstract.__dict__))
                    out.write('\n')
