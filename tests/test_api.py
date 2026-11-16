import sqlite3
import json
from pathlib import Path
import time

import pytest
from fastapi.testclient import TestClient

from app import main

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(main.storage, "DATA_DIR", tmp_path)
    with TestClient(main.app) as client:
        yield client


def test_autosave_persistence_and_conflict(client):
    for tool in ["jinja", "textfsm", "ttp", "xpath"]:
        created = client.post("/api/workspaces", json={"tool": tool, "name": "Draft"})
        assert created.status_code == 201
    item = client.get("/api/workspaces?tool=jinja").json()[0]
    url = "/api/workspaces/" + item["id"]
    payload = {key: item[key] for key in main.WorkspaceUpdate.model_fields}
    payload.update(template="hostname {{ name }}", input='{"name":"r1"}')
    saved = client.put(url, json=payload)
    assert saved.status_code == 200
    assert saved.json()["revision"] == 2
    assert client.put(url, json=payload).status_code == 409
    main.initialize()  # Simulate startup against existing database.
    assert client.get(url).json()["template"] == payload["template"]
    with TestClient(main.app) as fresh:
        assert len(fresh.get("/api/workspaces").json()) == 4
    assert client.delete(url).status_code == 204
    assert client.get(url).status_code == 404


def test_request_guards(client):
    payload = {"tool": "jinja", "name": "Draft"}
    assert client.post("/api/workspaces", json=payload, headers={"Origin": "https://evil.example"}).status_code == 403
    assert client.get("/health", headers={"Host": "evil.example"}).status_code == 400
    assert client.post("/api/workspaces", content="x" * (1024 * 1024 + 1)).status_code == 413
    invalid = client.post("/api/workspaces", json={"tool": "private-secret", "name": "Draft"})
    assert invalid.status_code == 422
    assert "private-secret" not in invalid.text
    assert client.get("/api/tools").json()[0]["execution_available"] is True


def test_no_remote_documentation_assets(client):
    assert client.get("/docs").status_code == 404
    assert client.get("/openapi.json").status_code == 200
    assert client.get("/health").headers["cache-control"] == "no-store"


def test_no_authentication(client):
    assert client.get('/api/config').json() == {'authentication': False, 'storage': 'local-files'}
    assert client.get('/api/workspaces').status_code == 200
    for endpoint in ['setup', 'login', 'logout']:
        assert client.post('/api/auth/' + endpoint, json={}).status_code == 404
    assert client.get('/api/auth/me').status_code == 404


def test_home_and_assets(client):
    response = client.get('/')
    assert response.status_code == 200
    assert 'aria-label="Tools"' in response.text
    assert "default-src 'self'" in response.headers['content-security-policy']
    assert client.get('/static/app.js').status_code == 200
    assert client.get('/static/style.css').status_code == 200


@pytest.mark.parametrize('tool,template,data,expected', [
    ('jinja', 'hostname {{ name }}', 'name: router1', 'hostname router1'),
    ('textfsm', 'Value NAME (\\S+)\n\nStart\n  ^${NAME} -> Record\n', 'router1\n', '[\n  {\n    "NAME": "router1"\n  }\n]'),
    ('ttp', '<group name="interfaces*">\ninterface {{ name }}\n</group>', 'interface Gi0/1\n', None),
    ('xpath', '//name/text()', '<device><name>r1</name></device>', '[\n  "r1"\n]'),
])
def test_execute_engines(client, tool, template, data, expected):
    payload = {'tool': tool, 'template': template, 'input': data}
    response = client.post('/api/execute', json=payload)
    assert response.status_code == 200, response.text
    if expected is not None:
        assert response.json()['result'] == expected
    else:
        assert 'Gi0/1' in response.json()['result']


@pytest.mark.parametrize('tool,template,data', [
    ('jinja', '{{ missing }}', '{}'),
    ('jinja', '{{ "".__class__.__mro__ }}', '{}'),
    ('jinja', '{{ name }}', '- not a mapping'),
    ('jinja', '{{ "x" * 200001 }}', '{}'),
    ('textfsm', 'invalid template', 'data'),
    ('ttp', '<macro>import os</macro>', 'data'),
    ('ttp', '<input load="text">/etc/passwd</input>', 'data'),
    ('ttp', '{{ x | macro("evil") }}', 'data'),
    ('xpath', '//*', '<!DOCTYPE foo [<!ENTITY x SYSTEM "file:///etc/passwd">]><a>&x;</a>'),
    ('xpath', '[[[', '<a/>'),
])
def test_engine_errors_and_restrictions(client, tool, template, data):
    response = client.post('/api/execute', json={'tool': tool, 'template': template, 'input': data})
    assert response.status_code == 422, response.text
    assert response.json()['detail']


def test_execution_timeout_and_capacity(client, monkeypatch):
    def timeout(*args, **kwargs):
        raise main.subprocess.TimeoutExpired('worker', 3)
    monkeypatch.setattr(main.subprocess, 'run', timeout)
    payload = {'tool': 'jinja', 'template': 'hello', 'input': '{}'}
    assert client.post('/api/execute', json=payload).status_code == 422
    # Timeouts must release the worker slot.
    assert main.ENGINE_SLOTS.acquire(blocking=False)
    assert main.ENGINE_SLOTS.acquire(blocking=False)
    try:
        assert client.post('/api/execute', json=payload).status_code == 429
    finally:
        main.ENGINE_SLOTS.release()
        main.ENGINE_SLOTS.release()


@pytest.mark.parametrize('expression,expected', [
    ('"192.0.2.10/24" | ipaddr("network")', '192.0.2.0'),
    ('"192.0.2.10/24" | ansible.utils.ipaddr("netmask")', '255.255.255.0'),
    ('"192.0.2.10/24" | ipaddr("prefix")', '24'),
    ('"192.0.2.0/24" | ipaddr("1") | ipaddr("address")', '192.0.2.1'),
    ('"192.0.2.0/24" | ipaddr("-1")', '192.0.2.255/24'),
    ('"2001:db8::10/64" | ipv6("network")', '2001:db8::'),
    ('["192.0.2.1", "bad", "2001:db8::1"] | ipv4 | join(",")', '192.0.2.1'),
    ('"bad" | ipaddr', 'False'),
    ('"192.0.2.1" | ipmath(5)', '192.0.2.6'),
    ('"192.0.2.0/24" | nthhost(2)', '192.0.2.2'),
    ('"2001:db8::1" | ipwrap', '[2001:db8::1]'),
    ('"192.0.2.0/24" | ipsubnet(26, 1)', '192.0.2.64/26'),
    ('["192.0.2.0/25", "192.0.2.128/25"] | cidr_merge | join(",")', '192.0.2.0/24'),
    ('"00:11:22:33:44:55" | hwaddr("cisco")', '0011.2233.4455'),
])
def test_network_filters(client, expression, expected):
    response = client.post('/api/execute', json={'tool': 'jinja', 'template': '{{ ' + expression + ' }}', 'input': '{}'})
    assert response.status_code == 200, response.text
    assert response.json()['result'] == expected


def test_trusted_custom_filter_registry(monkeypatch, tmp_path):
    import types
    import sys
    from app.engine import execute
    monkeypatch.setenv('ANSIBLE_HOME', str(tmp_path))
    monkeypatch.setitem(sys.modules, 'custom_filters', types.SimpleNamespace(FILTERS={'interface_slug': lambda value: value.lower().replace('/', '_')}))
    assert execute('jinja', '{{ "Gi0/1" | interface_slug }}', '{}') == 'gi0_1'


@pytest.mark.parametrize('example', json.loads(
    (Path(__file__).parent.parent / 'examples/jinja-first-batch.json').read_text()
), ids=lambda item: item['name'])
def test_first_batch_examples(client, example):
    response = client.post('/api/execute', json={
        'tool': 'jinja', 'template': example['template'], 'input': example['data'],
    })
    assert response.status_code == 200, response.text
    assert response.json()['result'] == example['expected']


@pytest.mark.parametrize('expression,expected', [
    ('[10,11,12,20] | ansible.netcommon.vlan_parser | first', '10-12,20'),
    ('"10-12,20" | ansible.netcommon.vlan_expander | join(",")', '10,11,12,20'),
    ('"Gi0/1" | ansible.builtin.regex_replace("^Gi", "GigabitEthernet")', 'GigabitEthernet0/1'),
    ('"Gi0/1" | ansible.builtin.regex_search("[0-9]+/[0-9]+")', '0/1'),
    ('"Gi0/1 Gi0/2" | ansible.builtin.regex_findall("Gi[0-9]+/[0-9]+") | length', '2'),
    ('{"mtu":1500} | ansible.builtin.combine({"mtu":9000}) | ansible.builtin.dict2items | ansible.builtin.items2dict', "{'mtu': 9000}"),
    ('[10,20] | ansible.builtin.difference([20]) | join(",")', '10'),
    ('[10,20] | ansible.builtin.intersect([20,30]) | join(",")', '20'),
    ('[10,20] | ansible.builtin.symmetric_difference([20,30]) | sort | join(",")', '10,30'),
    ('[10,20] | union([20,30]) | sort | join(",")', '10,20,30'),
    ('[10,20] | ansible.builtin.union([20,30]) | sort | join(",")', '10,20,30'),
])
def test_first_batch_aliases(client, expression, expected):
    response = client.post('/api/execute', json={'tool': 'jinja', 'template': '{{ ' + expression + ' }}', 'input': '{}'})
    assert response.status_code == 200, response.text
    assert response.json()['result'] == expected


@pytest.mark.parametrize('template', [
    '{{ [0, 5000] | vlan_parser }}',
    '{{ "Gi0/1" | regex_search("[") }}',
    '{{ [1, 2] | items2dict }}',
])
def test_first_batch_invalid_input(client, template):
    response = client.post('/api/execute', json={'tool': 'jinja', 'template': template, 'input': '{}'})
    assert response.status_code == 422, response.text


@pytest.mark.parametrize('tool,example', [
    (tool, example)
    for tool, examples in json.loads((Path(__file__).parent.parent / 'app/static/examples.json').read_text()).items()
    for example in examples
], ids=lambda item: item['name'] if isinstance(item, dict) else item)
def test_dropdown_examples(client, tool, example):
    response = client.post('/api/execute', json={'tool': tool, 'template': example['template'], 'input': example['data'], 'conversion_direction': example.get('options', {}).get('direction', 'json_to_yaml')})
    assert response.status_code == 200, response.text
    result = response.json()['result']
    if tool == 'json_yaml' and example['options']['direction'] == 'json_to_yaml':
        import yaml
        assert yaml.safe_load(result) == example['expected']
    else:
        assert (result if tool == 'jinja' else json.loads(result)) == example['expected']


@pytest.mark.parametrize('data,expected', [
    ('{"a":1,}', 'line 1, column'),
    ('{\n "a":\n}', 'line 3, column'),
    ('{"a":NaN}', 'not a valid JSON number'),
    ('Infinity', 'not a valid JSON number'),
    ('{"a":1,"a":2}', 'Duplicate object key'),
    ('{"a":true} extra', 'Extra data'),
])
def test_json_validation_errors(client, data, expected):
    response = client.post('/api/execute', json={'tool':'json', 'template':'', 'input':data})
    assert response.status_code == 422, response.text
    assert expected in response.json()['detail']


@pytest.mark.parametrize('data', ['null', 'true', '"router"', '[]', '{}', '1e400', '123456789012345678901234567890', '0.12345678901234567890123456789'])
def test_json_values_and_precision(client, data):
    response = client.post('/api/execute', json={'tool':'json', 'template':'', 'input':data})
    assert response.status_code == 200, response.text
    assert response.json()['result'] == data


def test_json_workspace_persistence(client):
    item = client.post('/api/workspaces', json={'tool':'json', 'name':'Inventory', 'input':'{"hostname":"r1"}'}).json()
    assert client.get('/api/workspaces/' + item['id']).json()['input'] == '{"hostname":"r1"}'


def test_jinja_module_inventory(client):
    response = client.get('/api/jinja/modules')
    assert response.status_code == 200
    inventory = response.json()
    groups = {item['name']: item['filters'] for item in inventory['filter_groups']}
    assert 'ipaddr' in groups['ansible.utils']
    assert 'vlan_parser' in groups['ansible.netcommon']
    assert 'combine' in groups['ansible.builtin']
    assert 'default' in groups['Jinja built-ins']
    assert 'Custom filters' in groups
    from importlib.metadata import version
    packages = {item['name'].lower(): item['version'] for item in inventory['packages']}
    assert packages['netaddr'] == version('netaddr')
    collections = {item['name']: item['version'] for item in inventory['collections']}
    assert collections['ansible.utils'] and collections['ansible.netcommon']


@pytest.mark.parametrize('direction,data', [
    ('json_to_yaml', '{"bad":}'),
    ('json_to_yaml', '{"a":1,"a":2}'),
    ('yaml_to_json', 'a: [1, 2'),
    ('yaml_to_json', 'a: 1\na: 2'),
    ('yaml_to_json', '!!python/object/apply:os.system [echo unsafe]'),
    ('yaml_to_json', 'a: &a [*a]'),
    ('yaml_to_json', 'a: .inf'),
    ('yaml_to_json', 'a: !!set {x: null}'),
    ('yaml_to_json', '1: a\n"1": b'),
])
def test_converter_rejects_invalid_data(client, direction, data):
    response = client.post('/api/execute', json={'tool':'json_yaml','template':'','input':data,'conversion_direction':direction})
    assert response.status_code == 422, response.text


def test_converter_round_trip_precision_and_saved_direction(client):
    source = '{"number": 0.12345678901234567890123456789, "large": 1e400, "text": "on", "empty": null}'
    first = client.post('/api/execute', json={'tool':'json_yaml','template':'','input':source,'conversion_direction':'json_to_yaml'})
    assert first.status_code == 200, first.text
    second = client.post('/api/execute', json={'tool':'json_yaml','template':'','input':first.json()['result'],'conversion_direction':'yaml_to_json'})
    assert second.status_code == 200, second.text
    from decimal import Decimal
    assert json.loads(second.json()['result'], parse_float=Decimal) == json.loads(source, parse_float=Decimal)
    item = client.post('/api/workspaces', json={'tool':'json_yaml','name':'Conversion','input':'hostname: r1','options':{'direction':'yaml_to_json'}})
    assert item.status_code == 201
    saved = client.get('/api/workspaces/' + item.json()['id']).json()
    assert saved['options']['direction'] == 'yaml_to_json'


def test_converter_integer_keys_and_timestamps(client):
    response = client.post('/api/execute', json={'tool':'json_yaml','template':'','input':'1: one\ndate: 2026-11-16','conversion_direction':'yaml_to_json'})
    assert response.status_code == 200, response.text
    assert json.loads(response.json()['result']) == {'1':'one','date':'2026-11-16'}


def test_workspace_files_delete_and_no_resurrection(client):
    item = client.post('/api/workspaces', json={'tool':'jinja','name':'../../not-a-path','template':'hello','input':'{}'}).json()
    directory = main.storage.DATA_DIR / 'workspaces' / item['id']
    assert json.loads((directory / 'workspace.json').read_text())['template'] == 'hello'
    (directory / 'extra.txt').write_text('attached file')
    assert client.delete('/api/workspaces/' + item['id']).status_code == 204
    assert not directory.exists()
    payload = {key:item[key] for key in main.WorkspaceUpdate.model_fields}
    assert client.put('/api/workspaces/' + item['id'], json=payload).status_code == 404
    main.initialize()
    assert client.get('/api/workspaces').json() == []
    assert list((main.storage.DATA_DIR / 'workspaces').iterdir()) == []


def test_storage_path_safety(client, tmp_path):
    outside = tmp_path / 'outside'
    outside.mkdir()
    (outside / 'workspace.json').write_text('{}')
    (tmp_path / 'workspaces' / ('a'*32)).symlink_to(outside, target_is_directory=True)
    assert client.delete('/api/workspaces/' + 'a'*32).status_code == 404
    assert (outside / 'workspace.json').exists()
    assert client.get('/api/workspaces/not-an-id').status_code == 404


def test_legacy_migration_preserves_all_work_and_does_not_reimport(client):
    root = main.storage.DATA_DIR
    old_id = 'b'*32
    with sqlite3.connect(root / 'racksmith.sqlite3') as db:
        db.execute('CREATE TABLE workspaces (id TEXT, user_id TEXT, tool TEXT, name TEXT, template TEXT, input TEXT, options TEXT, result TEXT, revision INTEGER, updated_at INTEGER)')
        db.execute('INSERT INTO workspaces VALUES (?,?,?,?,?,?,?,?,?,?)', (old_id,'old-account','jinja','Old draft','{{ x }}','x: 1','{}','1',4,123))
    main.initialize()
    item = client.get('/api/workspaces/' + old_id).json()
    assert item['name'] == 'Old draft' and item['revision'] == 4
    assert item['input'] == 'x: 1'
    assert not (root / 'racksmith.sqlite3').exists()
    assert (root / 'archive/before-file-storage.sqlite3').exists()
    assert client.delete('/api/workspaces/' + old_id).status_code == 204
    main.initialize()
    assert client.get('/api/workspaces/' + old_id).status_code == 404


def test_failed_atomic_save_keeps_previous_file(client, monkeypatch):
    item = client.post('/api/workspaces', json={'tool':'jinja','name':'Original'}).json()
    original = main.storage.get(item['id'])
    def fail_replace(*args):
        raise OSError('Simulated disk failure')
    monkeypatch.setattr(main.storage.os, 'replace', fail_replace)
    payload = {key:item[key] for key in main.WorkspaceUpdate.model_fields}
    payload['name'] = 'Unsaved change'
    with pytest.raises(OSError):
        main.storage.save(item['id'], payload)
    assert main.storage.get(item['id']) == original
    directory = main.storage.DATA_DIR / 'workspaces' / item['id']
    assert [path.name for path in directory.iterdir()] == ['workspace.json']


def test_concurrent_save_and_delete_cannot_recreate_files(client):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    item = client.post('/api/workspaces', json={'tool':'json','name':'Concurrent'}).json()
    payload = {key:item[key] for key in main.WorkspaceUpdate.model_fields}
    barrier = Barrier(2)
    def saving():
        barrier.wait()
        try:
            main.storage.save(item['id'], payload)
        except main.HTTPException as error:
            assert error.status_code == 404
    def deleting():
        barrier.wait()
        main.storage.delete(item['id'])
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(saving), pool.submit(deleting)]
        for future in futures:
            future.result()
    assert not (main.storage.DATA_DIR / 'workspaces' / item['id']).exists()


def test_cleanup_all_workspace_contents(client, tmp_path):
    items = [client.post('/api/workspaces', json={'tool': tool, 'name': 'Cleanup test'}).json()
             for tool in ('jinja', 'json', 'ttp')]
    root = main.storage.DATA_DIR / 'workspaces'
    (root / 'stray.txt').write_text('extra')
    (root / '.hidden').mkdir()
    (root / '.hidden' / 'attachment').write_text('extra')
    outside = tmp_path / 'keep.txt'
    outside.write_text('keep')
    (root / 'external-link').symlink_to(outside)
    (root / 'external-directory').symlink_to(tmp_path, target_is_directory=True)
    assert client.delete('/api/workspaces', headers={'Origin': 'https://evil.example'}).status_code == 403
    assert client.delete('/api/workspaces').status_code == 204
    assert list(root.iterdir()) == []
    assert outside.read_text() == 'keep'
    for item in items:
        payload = {key: item[key] for key in main.WorkspaceUpdate.model_fields}
        assert client.put('/api/workspaces/' + item['id'], json=payload).status_code == 404
    main.initialize()
    assert client.get('/api/workspaces').json() == []
    assert client.delete('/api/workspaces').status_code == 204
    assert client.post('/api/workspaces', json={'tool': 'json', 'name': 'New work'}).status_code == 201
