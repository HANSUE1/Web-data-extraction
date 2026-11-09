"""The crawl frontier: pages waiting to be visited, taken from each site in turn.

A plain queue lets the first site crawled fill it, so every worker ends up
waiting on that one site's rate limit. Taking turns between sites keeps
workers busy on different sites, and new sites get a turn soon after they
are found. Within one site, pages are taken in the order they were found.
"""
from collections import OrderedDict, deque
from typing import Callable, Deque, Iterable, Iterator, List, Optional, Tuple

QueuedUrl = Tuple[str, int]  # (url, depth)


class SiteFrontier:
    """A queue of (url, depth) pairs, with one queue per site, served in turn."""

    def __init__(self, site_of: Callable[[str], str], items: Iterable[QueuedUrl] = ()):
        self._site_of = site_of
        self._sites: "OrderedDict[str, Deque[QueuedUrl]]" = OrderedDict()
        self._size = 0
        self.extend(items)

    def append(self, entry: QueuedUrl):
        self._queue_for(entry[0]).append(entry)
        self._size += 1

    def appendleft(self, entry: QueuedUrl):
        """Put a page back at the front, for its site to be served next."""
        site = self._site_of(entry[0])
        self._queue_for(entry[0]).appendleft(entry)
        self._sites.move_to_end(site, last=False)
        self._size += 1

    def extend(self, entries: Iterable[QueuedUrl]):
        for entry in entries:
            self.append(tuple(entry))

    def popleft(self) -> QueuedUrl:
        """The next page of the site whose turn it is. That site then goes to the back."""
        if not self._sites:
            raise IndexError("pop from an empty frontier")
        site, queue = next(iter(self._sites.items()))
        entry = queue.popleft()
        self._size -= 1
        if queue:
            self._sites.move_to_end(site)
        else:
            del self._sites[site]
        return entry

    def popleft_ready(self, is_ready: Callable[[str], bool]) -> Optional[QueuedUrl]:
        """Like popleft, but skip sites whose next page is_ready rejects. None if no site is ready.

        Skipped sites keep their place at the front, so they are served as soon as they are ready.
        """
        for site, queue in self._sites.items():
            if is_ready(queue[0][0]):
                break
        else:
            return None
        entry = queue.popleft()
        self._size -= 1
        if queue:
            self._sites.move_to_end(site)
        else:
            del self._sites[site]
        return entry

    def heads(self) -> Iterator[str]:
        """The URL each site would serve next."""
        for queue in self._sites.values():
            yield queue[0][0]

    def queued_for(self, site: str) -> int:
        queue = self._sites.get(site)
        return len(queue) if queue else 0

    def drop_site(self, site: str) -> List[QueuedUrl]:
        """Take out every page of a site, as when its page budget is used up, and return them."""
        dropped = list(self._sites.pop(site, ()))
        self._size -= len(dropped)
        return dropped

    @property
    def site_count(self) -> int:
        return len(self._sites)

    def _queue_for(self, url: str) -> Deque[QueuedUrl]:
        site = self._site_of(url)
        queue = self._sites.get(site)
        if queue is None:
            queue = self._sites[site] = deque()
        return queue

    def __len__(self) -> int:
        return self._size

    def __bool__(self) -> bool:
        return self._size > 0

    def __iter__(self) -> Iterator[QueuedUrl]:
        """Every queued page, site by site. Rebuilding a frontier from this restores the same turns."""
        for queue in self._sites.values():
            yield from queue
