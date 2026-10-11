"""Read-only login probe of an existing eval preview, without displaying secrets.

Uses the private preview API response file saved by the root task. Does not mint
previews, submit bids, or run agents. Actual credentials are passed unchanged.
"""
import argparse
import json
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode, urljoin
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def probe(site):
    headers = {'User-Agent': 'isar-labs-eval-infra-audit/1.0'}
    try:
        with urlopen(Request(site['url'], headers=headers), timeout=20) as response:
            html, address, get_status = response.read().decode(), response.url, response.status
        match = re.search(r'<form[^>]*action=["\']([^"\']+)', html)
        if not match:
            return {'site': site['name'], 'login_form_present': False, 'get_status': get_status}
        action = urljoin(address, match[1])
        request = Request(action, data=urlencode({'username': site['username'],
            'password': site['password']}).encode(), headers={**headers,
            'Content-Type': 'application/x-www-form-urlencoded'}, method='POST')
        try:
            response = build_opener(NoRedirect()).open(request, timeout=20)
        except HTTPError as error:
            response = error
        with response:
            html, status = response.read().decode(), response.status
            authenticated = status == 303 and bool(response.headers.get('Set-Cookie'))
        return {'site': site['name'], 'get_status': get_status, 'post_status': status,
            'authenticated_redirect': authenticated,
            'upstream_status': re.findall(r'upstream status ([\d]+)', html),
            'node_reference_status': re.findall(r'node-status:([^\]]+)', html),
            'error_terms': re.findall(r'terms:([^\]]+)', html)}
    except HTTPError as error:
        return {'site': site['name'], 'http_error_status': error.code}
    except Exception as error:
        return {'site': site['name'], 'transport_error_type': type(error).__name__}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('preview_response', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    preview = json.loads(args.preview_response.read_text())['preview']
    with ThreadPoolExecutor(4) as pool:
        results = list(pool.map(probe, preview['sites']))
    report = {'preview_id': preview['id'], 'version_id': preview['version_id'],
              'credentials_transformed': False, 'results': results}
    text = json.dumps(report, indent=2)
    if args.output:
        args.output.write_text(text + '\n')
    print(text)


if __name__ == '__main__':
    main()
