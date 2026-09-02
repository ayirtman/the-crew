"""Crew station 14: the Analytics Agent. Watches what actually happened out there: the live
URL, measured. A program; a failed probe is data in the report, never a crash."""
from __future__ import annotations

import time
import urllib.request
from typing import Callable

from pipeline.config import Config
from pipeline.contracts import AnalyticsReport, UrlCheck

Probe = Callable[[str], tuple[int, int, int]]


def default_probe(url: str, timeout: float = 20.0) -> tuple[int, int, int]:
    t0 = time.monotonic()
    req = urllib.request.Request(url, headers={"User-Agent": "pipeline-analytics/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = r.read()
    return int(r.status), int((time.monotonic() - t0) * 1000), len(body)


def produce(*, url: str, parent_sha: str, run_id: str, cfg: Config,
            probe: Probe | None = None) -> AnalyticsReport:
    probe = probe or default_probe
    try:
        status, ms, nbytes = probe(url)
    except Exception:
        status, ms, nbytes = 0, 0, 0
    return AnalyticsReport(run_id=run_id, parent=parent_sha, url=url,
                           checks=[UrlCheck(url=url, status=status, response_ms=ms, bytes=nbytes)])
