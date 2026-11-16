"""Register trusted local Python filters here, then rebuild the container.

Example:
    def interface_slug(value):
        return str(value).lower().replace('/', '_')

    FILTERS = {"interface_slug": interface_slug}

Use in Jinja: {{ interface_name | interface_slug }}

These functions execute as Python with the container user's permissions.
Keep them pure: accept data, return data, and avoid file/network operations.
Never load Python code supplied through template or input fields.
"""

FILTERS = {}
