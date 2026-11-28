.. currentmodule:: numpy

=========================
NumPy 2.0.0 Release Notes
=========================

*Released on 2024-06-16.*

NumPy 2.0.0 is the first major release since 2006. It is the result of 11
months of development since the last feature release and is the work of 212
contributors spread over 1078 pull requests. It contains a large number of
exciting new features as well as changes to both the Python and C APIs.

This major release includes breaking changes that could not happen in a regular
minor (feature) release - including an ABI break, changes to type promotion
rules, and API changes which may not have been emitting deprecation warnings
in 1.26.x. Key documents related to how to adapt to changes in NumPy 2.0, in
addition to these release notes, include:

- The :ref:`numpy-2-migration-guide`
- The :ref:`NumPy 2.0-specific advice <numpy-2-abi-handling>` in
  :ref:`for-downstream-package-authors`


Highlights
==========

Highlights of this release include:

- New features:

  - A new variable-length string dtype, `~numpy.dtypes.StringDType` and a new
    `numpy.strings` namespace with performant ufuncs for string operations,
  - Support for ``float32`` and ``longdouble`` in all `numpy.fft` functions,
  - Support for the array API standard in the main ``numpy`` namespace.

- Performance improvements:

  - Sorting functions (`sort`, `argsort`, `partition`, `argpartition`)
    have been accelerated through the use of the Intel x86-simd-sort and Google
    Highway libraries, and may see large (hardware-specific) speedups,
  - macOS Accelerate support and binary wheels for macOS >=14, with significant
    performance improvements for linear algebra operations on macOS, and wheels
    that are about 3 times smaller,
  - `numpy.char` fixed-length string operations have been accelerated by
    implementing ufuncs that also support `~numpy.dtypes.StringDType` in
    addition to the fixed-length string dtypes,
  - A new tracing and introspection API, `~numpy.lib.introspect.opt_func_info`,
    to determine which hardware-specific kernels are available and will be
    dispatched to.
  - `numpy.save` now uses pickle protocol version 4 for saving arrays with
    object dtype, which allows for pickle objects larger than 4GB and improves
    saving speed by about 5% for large arrays.

- Python API improvements:

  - A clear split between public and private API, with a new
    :ref:`module structure <module-structure>`, and each public function now
    available in a single place,
  - Many removals of non-recommended functions and aliases. This should make
    it easier to learn and use NumPy. The number of objects in the main
    namespace decreased by ~10% and in ``numpy.lib`` by ~80%,
  - :ref:`Canonical dtype names <canonical-python-and-c-types>` and a new
    `~numpy.isdtype` introspection function,

- C API improvements:

  - A new :ref:`public C API for creating custom dtypes <dtype-api>`,
  - Many outdated functions and macros removed, and private internals hidden to
    ease future extensibility,
  - New, easier to use, initialization functions:
    :c:func:`PyArray_ImportNumPyAPI` and :c:func:`PyUFunc_ImportUFuncAPI`.

- Improved behavior:

  - Improvements to type promotion behavior was changed by adopting :ref:`NEP
    50 <NEP50>`. This fixes many user surprises about promotions which
    previously often depended on data values of input arrays rather than only
    their dtypes.  Please see the NEP and the :ref:`numpy-2-migration-guide`
    for details as this change can lead to changes in output dtypes and lower
    precision results for mixed-dtype operations.
  - The default integer type on Windows is now ``int64`` rather than ``int32``,
    matching the behavior on other platforms,
  - The maximum number of array dimensions is changed from 32 to 64

- Documentation:

  - The reference guide navigation was significantly improved, and there is now
    documentation on NumPy's :ref:`module structure <module-structure>`,
  - The :ref:`building from source <building-from-source>` documentation was
    completely rewritten,

Furthermore there are many changes to NumPy internals, including continuing to
migrate code from C to C++, that will make it easier to improve and maintain
NumPy in the future.

The "no free lunch" theorem dictates that there is a price to pay for all these
API and behavior improvements and better future extensibility. This price is:

1. Backwards compatibility. There are a significant number of breaking changes
   to both the Python and C APIs. In the majority of cases, there are clear
   error messages that will inform the user how to adapt their code. However,
   there are also changes in behavior for which it was not possible to give
   such an error message - these cases are all covered in the Deprecation and
   Compatibility sections below, and in the :ref:`numpy-2-migration-guide`.

   Note that there is a ``ruff`` mode to auto-fix many things in Python code.

2. Breaking changes to the NumPy ABI. As a result, binaries of packages that
   use the NumPy C API and were built against a NumPy 1.xx release will not
   work with NumPy 2.0. On import, such packages will see an ``ImportError``
   with a message about binary incompatibility.

   It is possible to build binaries against NumPy 2.0 that will work at runtime
   with both NumPy 2.0 and 1.x. See :ref:`numpy-2-abi-handling` for more details.

   **All downstream packages that depend on the NumPy ABI are advised to do a
   new release built against NumPy 2.0 and verify that that release works with
   both 2.0 and 1.26 - ideally in the period between 2.0.0rc1 (which will be
   ABI-stable) and the final 2.0.0 release to avoid problems for their users.**

The Python versions supported by this release are 3.9-3.12.


NumPy 2.0 Python API removals
=============================

* ``np.geterrobj``, ``np.seterrobj`` and the related ufunc keyword argument
  ``extobj=`` have been removed.  The preferred replacement for all of these
  is using the context manager ``with np.errstate():``.

  (`gh-23922 <https://github.com/numpy/numpy/pull/23922>`__)

* ``np.cast`` has been removed. The literal replacement for
  ``np.cast[dtype](arg)`` is ``np.asarray(arg, dtype=dtype)``.

* ``np.source`` has been removed. The preferred replacement is
  ``inspect.getsource``.

* ``np.lookfor`` has been removed.

  (`gh-24144 <https://github.com/numpy/numpy/pull/24144>`__)

* ``numpy.who`` has been removed. As an alternative for the removed functionality, one
  can use a variable explorer that is available in IDEs such as Spyder or Jupyter Notebook.

  (`gh-24321 <https://github.com/numpy/numpy/pull/24321>`__)

* Warnings and exceptions present in `numpy.exceptions` (e.g,
  `~numpy.exceptions.ComplexWarning`,
  `~numpy.exceptions.VisibleDeprecationWarning`) are no longer exposed in the
  main namespace.
* Multiple niche enums, expired members and functions have been removed from
  the main namespace, such as: ``ERR_*``, ``SHIFT_*``, ``np.fastCopyAndTranspose``,
  ``np.kernel_version``, ``np.numarray``, ``np.oldnumeric`` and ``np.set_numeric_ops``.

  (`gh-24316 <https://github.com/numpy/numpy/pull/24316>`__)

* Replaced ``from ... import *`` in the ``numpy/__init__.py`` with explicit imports.
  As a result, these main namespace members got removed: ``np.FLOATING_POINT_SUPPORT``,
  ``np.FPE_*``, ``np.NINF``, ``np.PINF``, ``np.NZERO``, ``np.PZERO``, ``np.CLIP``,
  ``np.WRAP``, ``np.WRAP``, ``np.RAISE``, ``np.BUFSIZE``, ``np.UFUNC_BUFSIZE_DEFAULT``,
  ``np.UFUNC_PYVALS_NAME``, ``np.ALLOW_THREADS``, ``np.MAXDIMS``, ``np.MAY_SHARE_EXACT``,
  ``np.MAY_SHARE_BOUNDS``, ``add_newdoc``, ``np.add_docstring`` and
  ``np.add_newdoc_ufunc``.

  (`gh-24357 <https://github.com/numpy/numpy/pull/24357>`__)

* Alias ``np.float_`` has been removed. Use ``np.float64`` instead.

* Alias ``np.complex_`` has been removed. Use ``np.complex128`` instead.

* Alias ``np.longfloat`` has been removed. Use ``np.longdouble`` instead.

* Alias ``np.singlecomplex`` has been removed. Use ``np.complex64`` instead.

* Alias ``np.cfloat`` has been removed. Use ``np.complex128`` instead.

* Alias ``np.longcomplex`` has been removed. Use ``np.clongdouble`` instead.

* Alias ``np.clongfloat`` has been removed. Use ``np.clongdouble`` instead.

* Alias ``np.string_`` has been removed. Use ``np.bytes_`` instead.

* Alias ``np.unicode_`` has been removed. Use ``np.str_`` instead.

* Alias ``np.Inf`` has been removed. Use ``np.inf`` instead.

* Alias ``np.Infinity`` has been removed. Use ``np.inf`` instead.

* Alias ``np.NaN`` has been removed. Use ``np.nan`` instead.

* Alias ``np.infty`` has been removed. Use ``np.inf`` instead.

* Alias ``np.mat`` has been removed. Use ``np.asmatrix`` instead.

* ``np.issubclass_`` has been removed. Use the ``issubclass`` builtin instead.

* ``np.asfarray`` has been removed. Use ``np.asarray`` with a proper dtype instead.

* ``np.set_string_function`` has been removed. Use ``np.set_printoptions``
  instead with a formatter for custom printing of NumPy objects.

* ``np.tracemalloc_domain`` is now only available from ``np.lib``.

* ``np.recfromcsv`` and ``np.recfromtxt`` were removed from the main namespace.
  Use ``np.genfromtxt`` with comma delimiter instead.

* ``np.issctype``, ``np.maximum_sctype``, ``np.obj2sctype``, ``np.sctype2char``,
  ``np.sctypes``, ``np.issubsctype`` were all removed from the
  main namespace without replacement, as they where niche members.

* Deprecated ``np.deprecate`` and ``np.deprecate_with_doc`` has been removed 
  from the main namespace. Use ``DeprecationWarning`` instead.

* Deprecated ``np.safe_eval`` has been removed from the main namespace. 
  Use ``ast.literal_eval`` instead.

  (`gh-24376 <https://github.com/numpy/numpy/pull/24376>`__)

* ``np.find_common_type`` has been removed. Use ``numpy.promote_types`` or
  ``numpy.result_type`` instead. To achieve semantics for the ``scalar_types``
  argument, use ``numpy.result_type`` and pass ``0``, ``0.0``, or ``0j`` as a
  Python scalar instead.

* ``np.round_`` has been removed. Use ``np.round`` instead.

* ``np.nbytes`` has been removed. Use ``np.dtype(<dtype>).itemsize`` instead.

  (`gh-24477 <https://github.com/numpy/numpy/pull/24477>`__)

* ``np.compare_chararrays`` has been removed from the main namespace. 
  Use ``np.char.compare_chararrays`` instead.

* The ``charrarray`` in the main namespace has been deprecated. It can be imported
  without a deprecation warning from ``np.char.chararray`` for now,
  but we are planning to fully deprecate and remove ``chararray`` in the future.

* ``np.format_parser`` has been removed from the main namespace. 
  Use ``np.rec.format_parser`` instead.

  (`gh-24587 <https://github.com/numpy/numpy/pull/24587>`__)

* Support for seven data type string aliases has been removed from ``np.dtype``:
  ``int0``, ``uint0``, ``void0``, ``object0``, ``str0``, ``bytes0`` and ``bool8``.

  (`gh-24807 <https://github.com/numpy/numpy/pull/24807>`__)

* The experimental ``numpy.array_api`` submodule has been removed. Use the main
  ``numpy`` namespace for regular usage instead, or the separate
  ``array-api-strict`` package for the compliance testing use case for which
  ``numpy.array_api`` was mostly used.

  (`gh-25911 <https://github.com/numpy/numpy/pull/25911>`__)


``__array_prepare__`` is removed
--------------------------------
UFuncs called ``__array_prepare__`` before running computations
for normal ufunc calls (not generalized ufuncs, reductions, etc.).
The function was also called instead of ``__array_wrap__`` on the
results of some linear algebra functions.

It is now removed. If you use it, migrate to ``__array_ufunc__`` or rely on
``__array_wrap__`` which is called with a context in all cases, although only
after the result array is filled. In those code paths, ``__array_wrap__`` will
now be passed a base class, rather than a subclass array.

(`gh-25105 <https://github.com/numpy/numpy/pull/25105>`__)


Deprecations
============

* ``np.compat`` has been deprecated, as Python 2 is no longer supported.

* ``numpy.int8`` and similar classes will no longer support conversion of 
  out of bounds python integers to integer arrays. For example, 
  conversion of 255 to int8 will not return -1.
  ``numpy.iinfo(dtype)`` can be used to check the machine limits for data types. 
  For example, ``np.iinfo(np.uint16)`` returns min = 0 and max = 65535.

  ``np.array(value).astype(dtype)`` will give the desired result.


* ``np.safe_eval`` has been deprecated. ``ast.literal_eval`` should be used instead.

  (`gh-23830 <https://github.com/numpy/numpy/pull/23830>`__)

* ``np.recfromcsv``, ``np.recfromtxt``, ``np.disp``, ``np.get_array_wrap``,
  ``np.maximum_sctype``, ``np.deprecate`` and ``np.deprecate_with_doc``
  have been deprecated.

  (`gh-24154 <https://github.com/numpy/numpy/pull/24154>`__)

* ``np.trapz`` has been deprecated. Use ``np.trapezoid`` or a ``scipy.integrate`` function instead.

* ``np.in1d`` has been deprecated. Use ``np.isin`` instead.

* Alias ``np.row_stack`` has been deprecated. Use ``np.vstack`` directly.

  (`gh-24445 <https://github.com/numpy/numpy/pull/24445>`__)

* ``__array_wrap__`` is now passed ``arr, context, return_scalar`` and
  support for implementations not accepting all three are deprecated.  Its signature
  should be ``__array_wrap__(self, arr, context=None, return_scalar=False)``

  (`gh-25409 <https://github.com/numpy/numpy/pull/25409>`__)

* Arrays of 2-dimensional vectors for ``np.cross`` have been deprecated. Use
  arrays of 3-dimensional vectors instead.

  (`gh-24818 <https://github.com/numpy/numpy/pull/24818>`__)

* ``np.dtype("a")`` alias for ``np.dtype(np.bytes_)`` was deprecated. Use
  ``np.dtype("S")`` alias instead.

  (`gh-24854 <https://github.com/numpy/numpy/pull/24854>`__)

* Use of keyword arguments ``x`` and ``y`` with functions
  ``assert_array_equal`` and ``assert_array_almost_equal`` has been deprecated.
  Pass the first two arguments as positional arguments instead.

  (`gh-24978 <https://github.com/numpy/numpy/pull/24978>`__)


``numpy.fft`` deprecations for n-D transforms with None values in arguments
---------------------------------------------------------------------------
Using ``fftn``, ``ifftn``, ``rfftn``, ``irfftn``, ``fft2``, ``ifft2``,
``rfft2`` or ``irfft2`` with the ``s`` parameter set to a value that is not
``None`` and the ``axes`` parameter set to ``None`` has been deprecated, in
line with the array API standard. To retain current behaviour, pass a sequence
[0, ..., k-1] to ``axes`` for an array of dimension k.

Furthermore, passing an array to ``s`` which contains ``None`` values is
deprecated as the parameter is documented to accept a sequence of integers
in both the NumPy docs and the array API specification. To use the default
behaviour of the corresponding 1-D transform, pass the value matching
the default for its ``n`` parameter. To use the default behaviour for every
axis, the ``s`` argument can be omitted.

(`gh-25495 <https://github.com/numpy/numpy/pull/25495>`__)


``np.linalg.lstsq`` now defaults to a new ``rcond`` value
---------------------------------------------------------
`~numpy.linalg.lstsq` now uses the new rcond value of the machine precision
times ``max(M, N)``.  Previously, the machine precision was used but a
FutureWarning was given to notify that this change will happen eventually.
That old behavior can still be achieved by passing ``rcond=-1``.

(`gh-25721 <https://github.com/numpy/numpy/pull/25721>`__)


Expired deprecations
====================

* The ``np.core.umath_tests`` submodule has been removed from the public API.
  (Deprecated in NumPy 1.15)

  (`gh-23809 <https://github.com/numpy/numpy/pull/23809>`__)

* The ``PyDataMem_SetEventHook`` deprecation has expired and it is
  removed.  Use ``tracemalloc`` and the ``np.lib.tracemalloc_domain``
  domain.  (Deprecated in NumPy 1.23)

  (`gh-23921 <https://github.com/numpy/numpy/pull/23921>`__)

* The deprecation of ``set_numeric_ops`` and the C functions
  ``PyArray_SetNumericOps`` and ``PyArray_GetNumericOps`` has
  been expired and the functions removed.  (Deprecated in NumPy 1.16)

  (`gh-23998 <https://github.com/numpy/numpy/pull/23998>`__)

* The ``fasttake``, ``fastclip``, and ``fastputmask``  ``ArrFuncs``
  deprecation is now finalized.
* The deprecated function ``fastCopyAndTranspose`` and its C counterpart
  are now removed.
* The deprecation of ``PyArray_ScalarFromObject`` is now finalized.

  (`gh-24312 <https://github.com/numpy/numpy/pull/24312>`__)

* ``np.msort`` has been removed. For a replacement, ``np.sort(a, axis=0)``
  should be used instead.

  (`gh-24494 <https://github.com/numpy/numpy/pull/24494>`__)


* ``np.dtype(("f8", 1)`` will now return a shape 1 subarray dtype
  rather than a non-subarray one.

  (`gh-25761 <https://github.com/numpy/numpy/pull/25761>`__)

* Assigning to the ``.data`` attribute of an ndarray is disallowed and will
  raise.

* ``np.binary_repr(a, width)`` will raise if width is too small.

* Using ``NPY_CHAR`` in ``PyArray_DescrFromType()`` will raise, use
  ``NPY_STRING`` ``NPY_UNICODE``, or ``NPY_VSTRING`` instead.

  (`gh-25794 <https://github.com/numpy/numpy/pull/25794>`__)


