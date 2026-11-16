"""Short-lived, resource-limited tool worker. No access to application objects."""
import io
import json
import re
import sys

MAX_OUTPUT = 200_000


class JsonNumber(str):
    """Keep valid JSON numbers exact, including very large exponents."""


def validate_json(data):
    def invalid_constant(value):
        raise ValueError(f'{value} is not a valid JSON number.')

    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f'Duplicate object key: {key!r}. Use unique keys.')
            result[key] = value
        return result

    try:
        value = json.loads(data, parse_int=JsonNumber, parse_float=JsonNumber,
                           parse_constant=invalid_constant, object_pairs_hook=unique_object)
    except json.JSONDecodeError as error:
        raise ValueError(f'{error.msg} at line {error.lineno}, column {error.colno}.') from error

    def pretty(value, depth=0):
        if isinstance(value, JsonNumber):
            return str(value)
        if isinstance(value, (dict, list)):
            opening, closing = ('{', '}') if isinstance(value, dict) else ('[', ']')
            if not value:
                return opening + closing
            indent = '  ' * (depth + 1)
            entries = [json.dumps(key) + ': ' + pretty(item, depth + 1) for key, item in value.items()] if isinstance(value, dict) else [pretty(item, depth + 1) for item in value]
            return opening + '\n' + ',\n'.join(indent + entry for entry in entries) + '\n' + '  ' * depth + closing
        return json.dumps(value)

    return pretty(value)


def convert_json_yaml(data, direction):
    import yaml
    from decimal import Decimal

    class Loader(yaml.SafeLoader):
        pass

    class Dumper(yaml.SafeDumper):
        pass

    def decimal_value(loader, node):
        value = Decimal(loader.construct_scalar(node).replace('_', ''))
        if not value.is_finite():
            raise ValueError('JSON cannot represent infinity or NaN.')
        return value

    def mapping(loader, node):
        loader.flatten_mapping(node)
        result = {}
        for key_node, value_node in node.value:
            key = loader.construct_object(key_node, deep=True)
            if isinstance(key, int) and not isinstance(key, bool):
                key = str(key)
            if not isinstance(key, str):
                raise ValueError('Mapping keys must be strings or integers to convert to JSON.')
            if key in result:
                raise ValueError(f'Duplicate mapping key: {key!r}.')
            result[key] = loader.construct_object(value_node, deep=True)
        return result

    Loader.add_constructor('tag:yaml.org,2002:float', decimal_value)
    Loader.add_constructor('tag:yaml.org,2002:timestamp', lambda loader, node: loader.construct_scalar(node))
    Loader.add_constructor('tag:yaml.org,2002:map', mapping)

    def decimal_yaml(dumper, value):
        text = str(value)
        if 'e' in text.lower() and '.' not in text.split('e')[0].split('E')[0]:
            text = text.replace('E', 'e').replace('e', '.0e', 1)
        return dumper.represent_scalar('tag:yaml.org,2002:float', text)

    Dumper.add_representer(Decimal, decimal_yaml)
    if direction == 'json_to_yaml':
        # Reuse strict JSON validation, then retain decimal precision.
        value = json.loads(validate_json(data), parse_float=Decimal)
        return yaml.dump(value, Dumper=Dumper, sort_keys=False, allow_unicode=True, default_flow_style=False, indent=2)
    if direction != 'yaml_to_json':
        raise ValueError('Unknown conversion direction.')
    try:
        value = yaml.load(data, Loader=Loader)
    except yaml.YAMLError as error:
        mark = getattr(error, 'problem_mark', None)
        where = f' at line {mark.line + 1}, column {mark.column + 1}' if mark else ''
        raise ValueError(f'Invalid YAML{where}: {getattr(error, "problem", None) or "unsupported YAML value"}') from error

    def encode(value, depth=0, ancestors=frozenset()):
        if isinstance(value, Decimal):
            if not value.is_finite():
                raise ValueError('JSON cannot represent infinity or NaN.')
            return str(value)
        if isinstance(value, (dict, list)):
            if id(value) in ancestors:
                raise ValueError('Recursive YAML aliases cannot be converted to JSON.')
            ancestors = ancestors | {id(value)}
            opening, closing = ('{', '}') if isinstance(value, dict) else ('[', ']')
            if not value:
                return opening + closing
            items = [json.dumps(key) + ': ' + encode(item, depth + 1, ancestors) for key, item in value.items()] if isinstance(value, dict) else [encode(item, depth + 1, ancestors) for item in value]
            return opening + '\n' + ',\n'.join('  ' * (depth + 1) + item for item in items) + '\n' + '  ' * depth + closing
        if value is None or isinstance(value, (str, bool, int)):
            return json.dumps(value, ensure_ascii=False, allow_nan=False)
        raise ValueError(f'YAML {type(value).__name__} values cannot be represented in JSON.')

    return encode(value)


def execute(tool, template, data, conversion_direction="json_to_yaml"):
    if tool == 'json_yaml':
        result = convert_json_yaml(data, conversion_direction)
    elif tool == 'json':
        result = validate_json(data)
    elif tool == 'jinja':
        import yaml
        from jinja2 import StrictUndefined
        from jinja2.sandbox import ImmutableSandboxedEnvironment
        values = yaml.safe_load(data) if data.strip() else {}
        if not isinstance(values, dict):
            raise ValueError('Variables must be a JSON object or YAML mapping.')
        env = ImmutableSandboxedEnvironment(undefined=StrictUndefined, keep_trailing_newline=True)
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from jinja_filters import register_filters
        register_filters(env)
        result = env.from_string(template).render(values)
    elif tool == 'textfsm':
        import textfsm
        parser = textfsm.TextFSM(io.StringIO(template))
        result = json.dumps(parser.ParseTextToDicts(data), indent=2)
    elif tool == 'ttp':
        from lxml import etree
        from ttp import ttp
        # Deliberately support declarative groups, not executable macros, external
        # inputs, includes, output destinations, or Python-evaluated variables.
        root = etree.fromstring(('<template>\n' + template + '\n</template>').encode(),
                                etree.XMLParser(resolve_entities=False, no_network=True))
        for node in root.iter():
            if node is root:
                continue
            if node.tag != 'group' or set(node.attrib) - {'name', 'method'}:
                raise ValueError('TTP supports group tags with name/method only; macros and external resources are disabled.')
            if node.get('method', 'group') not in {'group', 'table'}:
                raise ValueError('Unsupported TTP group method.')
        allowed = {'WORD', 'PHRASE', 'ORPHRASE', 'ROW', 'DIGIT', 'IP', 'PREFIX', 'MAC',
                   '_start_', '_end_', '_line_', 'to_int', 'to_float', 'strip', 'lower', 'upper'}
        for match in re.findall(r'{{(.*?)}}', template, re.S):
            parts = [part.strip() for part in match.split('|')]
            if not re.fullmatch(r'[\w.]+', parts[0]):
                raise ValueError('Invalid TTP variable name.')
            if any(part not in allowed for part in parts[1:]):
                raise ValueError('Unsupported TTP filter. Use simple pattern or conversion filters.')
        # A newline forces literal content instead of a possible file path.
        parser = ttp(data='\n' + data + '\n', template=etree.tostring(root, encoding='unicode') + '\n', log_level='CRITICAL')
        parser.parse(one=True)
        result = json.dumps(parser.result(), indent=2)
    else:
        from lxml import etree
        if re.search(r'<!\s*(DOCTYPE|ENTITY)', data, re.I):
            raise ValueError('XML document types and entities are disabled.')
        root = etree.fromstring(data.encode(), etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=False))
        namespaces = {key: value for key, value in root.nsmap.items() if key}
        if None in root.nsmap:
            namespaces['default'] = root.nsmap[None]
        value = root.xpath(template, namespaces=namespaces)
        if isinstance(value, list):
            value = [etree.tostring(item, encoding='unicode', with_tail=False) if isinstance(item, etree._Element) else str(item) for item in value]
        result = json.dumps(value, indent=2)
    if len(result.encode()) > MAX_OUTPUT:
        raise ValueError('Result exceeds 200 KB. Narrow your template or input.')
    return result


if __name__ == '__main__':
    import resource
    resource.setrlimit(resource.RLIMIT_CPU, (2, 2))
    if sys.platform == 'linux':
        resource.setrlimit(resource.RLIMIT_AS, (256 * 1024 * 1024, 256 * 1024 * 1024))
    try:
        payload = json.load(sys.stdin)
        print(json.dumps({'result': execute(payload['tool'], payload['template'], payload['input'], payload.get('conversion_direction', 'json_to_yaml'))}))
    except Exception as error:
        print(json.dumps({'error': str(error)[:2000] or type(error).__name__}))
