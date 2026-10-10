"""Measure local fixture overhead, not model/platform latency or agent quality."""
import concurrent.futures
import http.cookiejar
import json
from pathlib import Path
import statistics
import time
import urllib.parse
import urllib.request

from variant_server import PortalCluster, PROFILES


def benchmark(profile):
    rows = []
    with PortalCluster('baseline', profile) as cluster:
        for role, origin in cluster.origins.items():
            client = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
            with client.open(origin, timeout=3) as response:
                login_url = response.url
            body = urllib.parse.urlencode({'username': 'demo', 'password': 'synthetic-only'}).encode()
            with client.open(login_url, body, timeout=3) as response:
                home_url = response.url
            samples, sizes = [], []
            for _ in range(20):
                start = time.perf_counter()
                with client.open(home_url, timeout=3) as response:
                    data = response.read()
                samples.append((time.perf_counter() - start) * 1000)
                sizes.append(len(data))
            rows.append({'profile': profile, 'role': role, 'requests': len(samples),
                         'p50_ms': round(statistics.median(samples), 3),
                         'p95_ms': round(sorted(samples)[18], 3), 'max_ms': round(max(samples), 3),
                         'html_bytes': max(sizes)})
    return rows


def main():
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        rows = [row for group in pool.map(benchmark, PROFILES) for row in group]
    report = {'scope': '480 authenticated localhost home-page GETs; three clusters concurrently; no LLM or browser render timing',
              'not_platform_latency_or_agent_quality': True, 'rows': rows,
              'worst_role_p95_ms': max(row['p95_ms'] for row in rows)}
    Path(__file__).with_name('fixture-performance.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({'requests': sum(row['requests'] for row in rows),
                      'worst_role_p95_ms': report['worst_role_p95_ms']}))


if __name__ == '__main__':
    main()
