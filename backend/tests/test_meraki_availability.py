"""Cloud totals count complete regional API inventory, not just imported members."""
from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx
import pytest
from fastapi.testclient import TestClient

from labkeeper.app import create_app
from labkeeper.config import Settings
from labkeeper.providers.base import DeviceRef
from labkeeper.providers.meraki import MerakiClient, MerakiError, MerakiProvider

MEMBER = DeviceRef(id=1, key="member", name="member", serial="IMPORTED", mgmt_ip=None,
                   org_id="global", model="C9300", role="switch", type="switch")


def make_provider(records, fail=False):
    calls = []
    def respond(request):
        path = request.url.path
        calls.append(path)
        if path.endswith('/devices') or path.endswith('/networks'):
            return httpx.Response(200, json=[])
        if path.endswith('/devices/availabilities'):
            org = path.split('/organizations/')[1].split('/')[0]
            if org == 'bad':
                return httpx.Response(400, json={'serial': 'PRIVATE_ERROR', 'key': 'SECRET_VALUE'})
            return httpx.Response(200, json=[{'serial': 'IMPORTED', 'status': 'offline'}] if org == 'global' else records)
        return httpx.Response(404)
    settings = Settings(meraki={"enabled": True,
        'api_key': 'global-key', 'base_url': 'https://api.meraki.test/api/v1', 'org_ids': ['global'],
        'poll_interval': 200,
        'regions': [{'name': 'India', 'base_url': 'https://api.meraki.in/api/v1', 'api_key': 'india-key',
                     'org_ids': ['india', 'bad'] if fail else ['india']}],
    })
    return MerakiProvider(settings, transport=httpx.MockTransport(respond)), calls


def india(p):
    return next(r for r in p.details()['regions'] if r['name'] == 'India')


async def test_nonmembers_dedup_unknowns_and_device_routing():
    p, calls = make_provider([
        {'serial': 'IMPORTED', 'status': 'online'},
        {'serial': 'imported', 'status': 'offline'},
        {'serial': 'PRIVATE_NONMEMBER', 'status': 'offline', 'mac': 'PRIVATE_MAC'},
        {'serial': 'private_nonmember', 'status': 'dormant'},
        {'serial': 'NEW_STATUS', 'status': 'new-api-state'},
    ])
    before = datetime.now(UTC).replace(microsecond=0)
    try:
        assert india(p)['availability']['state'] == 'pending'
        updates = await p.poll([MEMBER])
        summary = india(p)['availability']
        assert summary['state'] == 'available'
        assert summary['counts'] == {'online': 1, 'alerting': 0, 'offline': 1, 'dormant': 0, 'unknown': 1}
        assert summary['stale_after_seconds'] == 600
        assert before <= datetime.fromisoformat(summary['checked_at']) <= datetime.now(UTC)
        assert india(p)['devices'] == 1 and updates['member'].status == 'online'
        assert updates['member'].live['meraki']['region'] == 'India'
        assert len(calls) == 6
        public = json.dumps(p.details())
        assert all(secret not in public for secret in ['PRIVATE_NONMEMBER', 'PRIVATE_MAC', 'NEW_STATUS', 'india-key'])
    finally:
        await p.aclose()


async def test_partial_failure_hides_totals_and_raw_error_payload():
    p, _ = make_provider([{'serial': 'IMPORTED', 'status': 'online'}], fail=True)
    try:
        assert (await p.poll([MEMBER]))['member'].status == 'online'
        assert india(p)['availability'] == {
            'state': 'unavailable', 'counts': None, 'checked_at': None, 'stale_after_seconds': 600,
        }
        assert 'PRIVATE_ERROR' not in json.dumps(p.details()) and 'SECRET_VALUE' not in json.dumps(p.details())
    finally:
        await p.aclose()


@pytest.mark.parametrize('bad', [None, 7, {'status': 'online'}, {'serial': 4, 'status': 'online'},
                                 {'serial': 'PRIVATE', 'status': None}, {'serial': 'PRIVATE', 'status': ''}])
async def test_invalid_records_fail_the_complete_region(bad):
    p, _ = make_provider([{'serial': 'IMPORTED', 'status': 'online'}, bad])
    try:
        assert (await p.poll([MEMBER]))['member'].status == 'offline'
        assert india(p)['availability']['state'] == 'unavailable'
        assert india(p)['availability']['counts'] is None
        assert india(p)['availability']['checked_at'] is None
    finally:
        await p.aclose()


async def test_missing_key_and_unpolled_regions_are_not_zero_counts(monkeypatch):
    monkeypatch.delenv('MISSING_KEY', raising=False)
    p = MerakiProvider(Settings(meraki={"enabled": True,
        'api_key': 'global-key', 'regions': [{'name': 'India', 'base_url': 'https://api.meraki.in/api/v1', 'api_key_env': 'MISSING_KEY'}],
    }))
    try:
        assert india(p)['availability'] == {
            'state': 'not-configured', 'counts': None, 'checked_at': None, 'stale_after_seconds': 180,
        }
        assert next(r for r in p.details()['regions'] if r['name'] == 'Global')['availability']['state'] == 'pending'
    finally:
        await p.aclose()


async def test_all_unknown_records_stay_unknown_and_empty_scope_unavailable():
    p, _ = make_provider([{'serial': 'NONMEMBER', 'status': 'future-state'}])
    try:
        await p.poll([MEMBER])
        assert india(p)['availability']['counts'] == {'online': 0, 'alerting': 0, 'offline': 0, 'dormant': 0, 'unknown': 1}
    finally:
        await p.aclose()
    p = MerakiProvider(Settings(meraki={"enabled": True, 'api_key': 'key'}), transport=httpx.MockTransport(lambda _: httpx.Response(200, json=[])))
    try:
        await p.poll([MEMBER])
        assert p.details()['regions'][0]['availability']['state'] == 'unavailable'
        assert p.details()['regions'][0]['availability']['counts'] is None
    finally:
        await p.aclose()


async def test_truncated_pagination_is_not_successful():
    c = MerakiClient('key', 'https://api.meraki.test/api/v1', transport=httpx.MockTransport(lambda _: httpx.Response(
        200, json=[{'serial': 'PRIVATE', 'status': 'online'}], headers={
            'Link': '<https://api.meraki.test/api/v1/organizations/1/devices/availabilities?startingAfter=next>; rel="next"',
        })))
    try:
        with pytest.raises(MerakiError, match='pagination limit'):
            await c.get_all('/organizations/1/devices/availabilities', max_pages=2)
    finally:
        await c.aclose()


def test_system_includes_current_server_time(settings):
    settings.meraki.enabled = False
    before = datetime.now(UTC).replace(microsecond=0)
    with TestClient(create_app(settings)) as client:
        response = client.get('/api/v1/system')
    assert response.status_code == 200
    assert before <= datetime.fromisoformat(response.json()['server_time']) <= datetime.now(UTC)


async def test_provider_snapshot_cannot_change_after_system_response_time_is_captured():
    import asyncio

    entered, finish = asyncio.Event(), asyncio.Event()

    async def respond(request):
        if request.url.path.endswith('/devices/availabilities'):
            entered.set()
            await finish.wait()
            return httpx.Response(200, json=[{'serial': 'IMPORTED', 'status': 'online'}])
        return httpx.Response(200, json=[])

    p = MerakiProvider(Settings(meraki={"enabled": True, 'api_key': 'key', 'org_ids': ['1']}), transport=httpx.MockTransport(respond))
    task = asyncio.create_task(p.poll([MEMBER]))
    try:
        await entered.wait()
        response_snapshot = p.details()['regions'][0]['availability']
        assert response_snapshot['state'] == 'pending'
        finish.set()
        await task
        assert p.details()['regions'][0]['availability']['state'] == 'available'
        assert response_snapshot['state'] == 'pending' and response_snapshot['checked_at'] is None
    finally:
        finish.set()
        await task
        await p.aclose()
