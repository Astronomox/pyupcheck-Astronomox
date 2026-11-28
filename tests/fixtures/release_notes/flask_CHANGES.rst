Version 3.0.3
-------------

Released 2024-04-07

-   The default ``hashlib.sha1`` may not be available in FIPS builds. Don't
    access it at import time so the developer has time to change the default.
    :issue:`5448`
-   Don't initialize the ``cli`` attribute in the sansio scaffold, but rather in
    the ``Flask`` concrete class. :pr:`5270`


Version 3.0.2
-------------

Released 2024-02-03

-   Correct type for ``jinja_loader`` property. :issue:`5388`
-   Fix error with ``--extra-files`` and ``--exclude-patterns`` CLI options.
    :issue:`5391`


Version 3.0.1
-------------

Released 2024-01-18

-   Correct type for ``path`` argument to ``send_file``. :issue:`5336`
-   Fix a typo in an error message for the ``flask run --key`` option. :pr:`5344`
-   Session data is untagged without relying on the built-in ``json.loads``
    ``object_hook``. This allows other JSON providers that don't implement that.
    :issue:`5381`
-   Address more type findings when using mypy strict mode. :pr:`5383`


Version 3.0.0
-------------

Released 2023-09-30

-   Remove previously deprecated code. :pr:`5223`
-   Deprecate the ``__version__`` attribute. Use feature detection, or
    ``importlib.metadata.version("flask")``, instead. :issue:`5230`
-   Restructure the code such that the Flask (app) and Blueprint
    classes have Sans-IO bases. :pr:`5127`
-   Allow self as an argument to url_for. :pr:`5264`
-   Require Werkzeug >= 3.0.0.


Version 2.3.3
-------------

Released 2023-08-21

-   Python 3.12 compatibility.
-   Require Werkzeug >= 2.3.7.
-   Use ``flit_core`` instead of ``setuptools`` as build backend.
-   Refactor how an app's root and instance paths are determined. :issue:`5160`


Version 2.3.2
-------------

Released 2023-05-01

-   Set ``Vary: Cookie`` header when the session is accessed, modified, or refreshed.
-   Update Werkzeug requirement to >=2.3.3 to apply recent bug fixes.
    :ghsa:`m2qf-hxjv-5gpq`


Version 2.3.1
-------------

Released 2023-04-25

-   Restore deprecated ``from flask import Markup``. :issue:`5084`


Version 2.3.0
-------------

Released 2023-04-25

-   Drop support for Python 3.7. :pr:`5072`
-   Update minimum requirements to the latest versions: Werkzeug>=2.3.0, Jinja2>3.1.2,
    itsdangerous>=2.1.2, click>=8.1.3.
-   Remove previously deprecated code. :pr:`4995`

    -   The ``push`` and ``pop`` methods of the deprecated ``_app_ctx_stack`` and
        ``_request_ctx_stack`` objects are removed. ``top`` still exists to give
        extensions more time to update, but it will be removed.
    -   The ``FLASK_ENV`` environment variable, ``ENV`` config key, and ``app.env``
        property are removed.
    -   The ``session_cookie_name``, ``send_file_max_age_default``, ``use_x_sendfile``,
        ``propagate_exceptions``, and ``templates_auto_reload`` properties on ``app``
        are removed.
    -   The ``JSON_AS_ASCII``, ``JSON_SORT_KEYS``, ``JSONIFY_MIMETYPE``, and
        ``JSONIFY_PRETTYPRINT_REGULAR`` config keys are removed.
    -   The ``app.before_first_request`` and ``bp.before_app_first_request`` decorators
        are removed.
    -   ``json_encoder`` and ``json_decoder`` attributes on app and blueprint, and the
        corresponding ``json.JSONEncoder`` and ``JSONDecoder`` classes, are removed.
    -   The ``json.htmlsafe_dumps`` and ``htmlsafe_dump`` functions are removed.
    -   Calling setup methods on blueprints after registration is an error instead of a
        warning. :pr:`4997`

-   Importing ``escape`` and ``Markup`` from ``flask`` is deprecated. Import them
    directly from ``markupsafe`` instead. :pr:`4996`
-   The ``app.got_first_request`` property is deprecated. :pr:`4997`
-   The ``locked_cached_property`` decorator is deprecated. Use a lock inside the
    decorated function if locking is needed. :issue:`4993`
-   Signals are always available. ``blinker>=1.6.2`` is a required dependency. The
    ``signals_available`` attribute is deprecated. :issue:`5056`
-   Signals support ``async`` subscriber functions. :pr:`5049`
-   Remove uses of locks that could cause requests to block each other very briefly.
    :issue:`4993`
-   Use modern packaging metadata with ``pyproject.toml`` instead of ``setup.cfg``.
    :pr:`4947`
-   Ensure subdomains are applied with nested blueprints. :issue:`4834`
-   ``config.from_file`` can use ``text=False`` to indicate that the parser wants a
    binary file instead. :issue:`4989`
-   If a blueprint is created with an empty name it raises a ``ValueError``.
    :issue:`5010`
-   ``SESSION_COOKIE_DOMAIN`` does not fall back to ``SERVER_NAME``. The default is not
    to set the domain, which modern browsers interpret as an exact match rather than
    a subdomain match. Warnings about ``localhost`` and IP addresses are also removed.
    :issue:`5051`
-   The ``routes`` command shows each rule's ``subdomain`` or ``host`` when domain
    matching is in use. :issue:`5004`
-   Use postponed evaluation of annotations. :pr:`5071`


Version 2.2.5
-------------

Released 2023-05-02

-   Update for compatibility with Werkzeug 2.3.3.
-   Set ``Vary: Cookie`` header when the session is accessed, modified, or refreshed.


Version 2.2.4
-------------

Released 2023-04-25

-   Update for compatibility with Werkzeug 2.3.


Version 2.2.3
-------------

Released 2023-02-15

-   Autoescape is enabled by default for ``.svg`` template files. :issue:`4831`
-   Fix the type of ``template_folder`` to accept ``pathlib.Path``. :issue:`4892`
-   Add ``--debug`` option to the ``flask run`` command. :issue:`4777`


