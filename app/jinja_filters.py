"""Explicitly registered, trusted filters for the offline Jinja environment."""
import importlib
import sys
from pathlib import Path

NETWORK_FILTERS = (
    'ipaddr', 'ipv4', 'ipv6', 'ipwrap', 'ipmath', 'ipsubnet',
    'cidr_merge', 'nthhost', 'hwaddr',
)

VLAN_FILTERS = ('vlan_parser', 'vlan_expander')
CORE_FILTERS = ('regex_search', 'regex_findall', 'regex_replace', 'combine', 'dict2items', 'items2dict')
LIST_FILTERS = ('difference', 'intersect', 'symmetric_difference', 'union')


def register_filters(environment):
    # Only the pinned collection bundled with this project is imported. No
    # discovery of user/global Ansible plugins, lookups, or templating engines.
    collection_path = Path(__file__).resolve().parent.parent / '.collections'
    sys.path.insert(0, str(collection_path))
    for name in NETWORK_FILTERS:
        module = importlib.import_module(f'ansible_collections.ansible.utils.plugins.filter.{name}')
        function = module.FilterModule().filters()[name]
        environment.filters[name] = function
        environment.filters[f'ansible.utils.{name}'] = function
    for name in VLAN_FILTERS:
        module = importlib.import_module(f'ansible_collections.ansible.netcommon.plugins.filter.{name}')
        function = module.FilterModule().filters()[name]
        environment.filters[name] = function
        environment.filters[f'ansible.netcommon.{name}'] = function
    for module_name, names in [('core', CORE_FILTERS), ('mathstuff', LIST_FILTERS)]:
        module = importlib.import_module(f'ansible.plugins.filter.{module_name}')
        available = module.FilterModule().filters()
        for name in names:
            environment.filters[name] = available[name]
            environment.filters[f'ansible.builtin.{name}'] = available[name]
    from custom_filters import FILTERS
    for name, function in FILTERS.items():
        if name in environment.filters:
            raise ValueError(f'Custom filter {name!r} conflicts with an existing filter.')
        if not isinstance(name, str) or not name.isidentifier() or not callable(function):
            raise ValueError('Custom filters require identifier names and callable values.')
        environment.filters[name] = function
